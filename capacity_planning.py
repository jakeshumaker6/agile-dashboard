"""
Capacity Planning Module — Gantt-style project timeline with engineer assignments.

Pulls projects (ClickUp lists from the Operations space) and lets admins
assign engineers, set start/end dates, and rate difficulty.
All users can view; only admins can edit.
"""

import os
import math
import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from flask import Blueprint, render_template, jsonify, request, session
from auth import login_required, admin_required

logger = logging.getLogger(__name__)

cp_bp = Blueprint('capacity_planning', __name__)

# ClickUp IDs (same as client_health.py)
OPERATIONS_SPACE_ID = "90139881259"
RECURRING_CLIENTS_FOLDER_ID = "901313653969"
EXCLUDED_FOLDERS = [
    "Client Template", "2-Day AI POCs", "Internal Projects", "Recurring Clients"
]

# Fibonacci score mapping (same as app.py)
SCORE_OPTIONS = {
    "86763539-3c8e-497a-8995-e4349917bc80": 1,
    "20341d9b-5f30-4d78-97c0-ad17a5f3a04c": 2,
    "5db17019-f2d7-417b-9cc5-fe727f3d29f1": 3,
    "8973f792-78cc-4fed-90f5-a88354fe881c": 5,
    "c57e955d-5247-494f-80e7-110e88ac5c89": 8,
    "ef195667-5b4f-4ae5-b878-7d55e0176fd3": 13,
}
ORDERINDEX_TO_SCORE = {0: 1, 1: 2, 2: 3, 3: 5, 4: 8, 5: 13}
FIBONACCI_FIELD_ID = os.environ.get(
    "FIBONACCI_FIELD_ID", "c88be994-51de-4bd3-b2f5-7850202b84bd"
)

# Engineer color palette (dark-theme friendly)
ENGINEER_COLORS = [
    '#E85A71', '#4ADE80', '#60A5FA', '#FBBF24', '#A78BFA', '#F97316',
    '#2DD4BF', '#FB7185', '#34D399', '#818CF8', '#F472B6', '#38BDF8',
]

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _cp_db_path():
    if os.path.isdir("/data"):
        return "/data/capacity_planning.db"
    return os.path.join(os.path.dirname(__file__), "capacity_planning.db")


def _get_db():
    conn = sqlite3.connect(_cp_db_path(), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def _db():
    """Context manager for safe DB access — always closes connection."""
    conn = _get_db()
    try:
        yield conn
    finally:
        conn.close()


def _row_to_dict(row):
    return dict(row) if row else None


def _rows_to_list(rows):
    return [dict(r) for r in rows]


def init_cp_db():
    """Create capacity planning tables if they don't exist."""
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cp_projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clickup_list_id TEXT UNIQUE NOT NULL,
            clickup_folder_id TEXT,
            name TEXT NOT NULL,
            folder_name TEXT,
            assigned_engineer_id INTEGER,
            start_date TEXT,
            end_date TEXT,
            difficulty TEXT DEFAULT 'medium',
            total_points INTEGER DEFAULT 0,
            task_count INTEGER DEFAULT 0,
            is_visible INTEGER DEFAULT 1,
            last_synced_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cp_engineer_colors (
            user_id INTEGER PRIMARY KEY,
            color TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cp_project_engineers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(project_id, user_id),
            FOREIGN KEY (project_id) REFERENCES cp_projects(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_cp_projects_list_id
            ON cp_projects(clickup_list_id);
        CREATE INDEX IF NOT EXISTS idx_cp_projects_engineer
            ON cp_projects(assigned_engineer_id);
        CREATE INDEX IF NOT EXISTS idx_cp_pe_project
            ON cp_project_engineers(project_id);
        CREATE INDEX IF NOT EXISTS idx_cp_pe_user
            ON cp_project_engineers(user_id);
    """)

    # Migrate existing assigned_engineer_id data into junction table
    now = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        "SELECT id, assigned_engineer_id FROM cp_projects "
        "WHERE assigned_engineer_id IS NOT NULL"
    ).fetchall()
    for row in rows:
        conn.execute(
            "INSERT OR IGNORE INTO cp_project_engineers (project_id, user_id, created_at) "
            "VALUES (?, ?, ?)",
            (row["id"], row["assigned_engineer_id"], now)
        )

    # Add project_type column if missing
    cols = [r[1] for r in conn.execute("PRAGMA table_info(cp_projects)").fetchall()]
    if "project_type" not in cols:
        conn.execute("ALTER TABLE cp_projects ADD COLUMN project_type TEXT DEFAULT 'standard'")
        # Auto-migrate: projects >= 365 days → long_term
        conn.execute("""
            UPDATE cp_projects
            SET project_type = 'long_term'
            WHERE start_date IS NOT NULL AND end_date IS NOT NULL
              AND (julianday(end_date) - julianday(start_date)) >= 365
        """)
        logger.info("Added project_type column and migrated long-term projects")

    conn.close()
    logger.info("Capacity planning database initialized")


# ---------------------------------------------------------------------------
# ClickUp sync
# ---------------------------------------------------------------------------

def _parse_task_score(task):
    """Extract Fibonacci score from a ClickUp task dict."""
    for cf in task.get("custom_fields", []):
        if cf.get("id") == FIBONACCI_FIELD_ID and cf.get("value") is not None:
            value = cf["value"]
            if isinstance(value, str):
                return SCORE_OPTIONS.get(value, 0)
            elif isinstance(value, int):
                return ORDERINDEX_TO_SCORE.get(value, 0)
    return 0


def sync_projects_from_clickup(clickup_request_fn):
    """
    Sync projects from ClickUp Operations space into the local DB.

    Each ClickUp list (under a folder in the Operations space) = one project.
    Updates name, folder_name, total_points, task_count.
    Never overwrites admin-set fields (engineer, dates, difficulty).

    Args:
        clickup_request_fn: Callable that makes ClickUp API requests.

    Returns:
        Dict with sync results.
    """
    results = {"synced": 0, "created": 0, "hidden": 0, "errors": []}
    now = datetime.now(timezone.utc).isoformat()
    seen_list_ids = set()

    try:
        # 1. Get folders from Operations space
        folders_data = clickup_request_fn(f"/space/{OPERATIONS_SPACE_ID}/folder")
        folders = folders_data.get("folders", [])

        for folder in folders:
            folder_name = folder["name"]
            folder_id = folder["id"]

            if folder_name in EXCLUDED_FOLDERS:
                continue
            if folder_id == RECURRING_CLIENTS_FOLDER_ID:
                continue

            # Each list in the folder is a project
            for lst in folder.get("lists", []):
                list_id = lst["id"]
                list_name = lst["name"]
                seen_list_ids.add(list_id)

                # Fetch tasks to compute points
                try:
                    tasks_data = clickup_request_fn(
                        f"/list/{list_id}/task?include_closed=true&subtasks=true"
                    )
                    tasks = tasks_data.get("tasks", [])
                    total_points = sum(_parse_task_score(t) for t in tasks)
                    task_count = len(tasks)
                except Exception as e:
                    logger.warning(f"Failed to fetch tasks for list {list_id}: {e}")
                    total_points = 0
                    task_count = 0
                    results["errors"].append(f"Tasks fetch failed for {list_name}")

                # Upsert project — only update synced fields
                with _db() as db:
                    existing = db.execute(
                        "SELECT id FROM cp_projects WHERE clickup_list_id = ?",
                        (list_id,)
                    ).fetchone()

                    if existing:
                        db.execute("""
                            UPDATE cp_projects
                            SET name = ?, folder_name = ?, clickup_folder_id = ?,
                                total_points = ?, task_count = ?,
                                is_visible = 1, last_synced_at = ?, updated_at = ?
                            WHERE clickup_list_id = ?
                        """, (list_name, folder_name, folder_id,
                              total_points, task_count, now, now, list_id))
                        results["synced"] += 1
                    else:
                        db.execute("""
                            INSERT INTO cp_projects
                                (clickup_list_id, clickup_folder_id, name, folder_name,
                                 total_points, task_count, is_visible,
                                 last_synced_at, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                        """, (list_id, folder_id, list_name, folder_name,
                              total_points, task_count, now, now, now))
                        results["created"] += 1

        # 2. Hide projects no longer in ClickUp
        with _db() as db:
            all_projects = db.execute(
                "SELECT id, clickup_list_id FROM cp_projects WHERE is_visible = 1"
            ).fetchall()
            for proj in all_projects:
                if proj["clickup_list_id"] not in seen_list_ids:
                    db.execute(
                        "UPDATE cp_projects SET is_visible = 0, updated_at = ? WHERE id = ?",
                        (now, proj["id"])
                    )
                    results["hidden"] += 1

    except Exception as e:
        logger.error(f"Error syncing projects from ClickUp: {e}", exc_info=True)
        results["errors"].append(str(e))

    logger.info(
        f"Capacity planning sync: {results['created']} created, "
        f"{results['synced']} updated, {results['hidden']} hidden"
    )
    return results


# ---------------------------------------------------------------------------
# Helper: get engineers with colors
# ---------------------------------------------------------------------------

def _get_engineers_with_colors():
    """Get all active users with their assigned colors."""
    from auth.db import get_all_users

    users = get_all_users()
    try:
        with _db() as db:
            color_rows = db.execute("SELECT * FROM cp_engineer_colors").fetchall()
        color_map = {r["user_id"]: r["color"] for r in color_rows}
    except Exception as e:
        logger.warning(f"Failed to load engineer colors: {e}")
        color_map = {}

    engineers = []
    for user in users:
        engineers.append({
            "id": user["id"],
            "username": user["username"],
            "email": user.get("email", ""),
            "color": color_map.get(user["id"]),
        })
    return engineers


def _get_all_project_engineers(db):
    """Load all project→engineer assignments as a dict keyed by project_id."""
    from auth.db import get_all_users

    users = get_all_users()
    user_map = {u["id"]: u for u in users}

    try:
        color_rows = db.execute("SELECT * FROM cp_engineer_colors").fetchall()
        color_map = {r["user_id"]: r["color"] for r in color_rows}
    except Exception:
        color_map = {}

    rows = db.execute(
        "SELECT project_id, user_id FROM cp_project_engineers ORDER BY id"
    ).fetchall()

    result = {}
    for row in rows:
        pid = row["project_id"]
        uid = row["user_id"]
        user = user_map.get(uid)
        if not user:
            continue
        if pid not in result:
            result[pid] = []
        result[pid].append({
            "id": uid,
            "username": user["username"],
            "color": color_map.get(uid),
        })
    return result


def _assign_color_if_needed(user_id):
    """Auto-assign a color to an engineer if they don't have one."""
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return ENGINEER_COLORS[0]

    with _db() as db:
        existing = db.execute(
            "SELECT color FROM cp_engineer_colors WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        if existing:
            return existing["color"]

        # Find first unused color
        used = db.execute("SELECT color FROM cp_engineer_colors").fetchall()
        used_colors = {r["color"] for r in used}
        for color in ENGINEER_COLORS:
            if color not in used_colors:
                break
        else:
            # All colors used, cycle
            color = ENGINEER_COLORS[user_id % len(ENGINEER_COLORS)]

        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT OR REPLACE INTO cp_engineer_colors (user_id, color, updated_at) VALUES (?, ?, ?)",
            (user_id, color, now)
        )
        return color


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------

@cp_bp.route("/capacity-planning")
@login_required
def capacity_planning_page():
    return render_template("capacity_planning.html")


# ---------------------------------------------------------------------------
# API: Projects
# ---------------------------------------------------------------------------

@cp_bp.route("/api/capacity-planning/projects")
@login_required
def api_cp_projects():
    """Get all visible projects with engineer info."""
    with _db() as db:
        proj_engineers = _get_all_project_engineers(db)
        rows = db.execute(
            "SELECT * FROM cp_projects WHERE is_visible = 1 ORDER BY folder_name, name"
        ).fetchall()

    projects = []
    for row in _rows_to_list(rows):
        try:
            engineers = proj_engineers.get(row["id"], [])
            # Backwards compat: first engineer or None
            first_eng = engineers[0] if engineers else None

            difficulty = row.get("difficulty", "medium") or "medium"
            total_pts = row.get("total_points", 0) or 0
            complexity_label = f"{total_pts} pts / {difficulty.capitalize()}"

            # Calculate avg points per week
            avg_pts_week = None
            start = row.get("start_date")
            end = row.get("end_date")
            if start and end and total_pts:
                try:
                    d_start = datetime.strptime(start, "%Y-%m-%d")
                    d_end = datetime.strptime(end, "%Y-%m-%d")
                    weeks = max(1, math.ceil((d_end - d_start).days / 7))
                    avg_pts_week = round(total_pts / weeks, 1)
                except ValueError:
                    pass

            projects.append({
                "id": row["id"],
                "clickup_list_id": row["clickup_list_id"],
                "name": row["name"],
                "folder_name": row.get("folder_name", ""),
                "assigned_engineers": engineers,
                "assigned_engineer": first_eng,
                "start_date": start,
                "end_date": end,
                "difficulty": difficulty,
                "total_points": total_pts,
                "task_count": row.get("task_count", 0) or 0,
                "complexity_label": complexity_label,
                "avg_points_per_week": avg_pts_week,
                "project_type": row.get("project_type", "standard") or "standard",
            })
        except Exception as e:
            logger.warning(f"Failed to process project row {row.get('id', '?')}: {e}")
            continue

    return jsonify({"projects": projects})


@cp_bp.route("/api/capacity-planning/projects/<int:project_id>", methods=["PUT"])
@admin_required
def api_cp_update_project(project_id):
    """Update project planning fields (admin only)."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    # Handle engineer IDs — accept both new array and legacy single ID
    engineer_ids = None
    if "assigned_engineer_ids" in data:
        raw = data["assigned_engineer_ids"]
        if isinstance(raw, list):
            engineer_ids = []
            for v in raw:
                try:
                    engineer_ids.append(int(v))
                except (TypeError, ValueError):
                    pass
        data.pop("assigned_engineer_ids")
    elif "assigned_engineer_id" in data:
        val = data.pop("assigned_engineer_id")
        if val is not None:
            try:
                engineer_ids = [int(val)]
            except (TypeError, ValueError):
                engineer_ids = []
        else:
            engineer_ids = []

    allowed = {"start_date", "end_date", "difficulty", "project_type"}
    updates = {k: v for k, v in data.items() if k in allowed}

    # Validate difficulty
    if "difficulty" in updates and updates["difficulty"] not in ("easy", "medium", "hard", "complex"):
        return jsonify({"error": "Invalid difficulty. Use: easy, medium, hard, complex"}), 400

    # Validate project_type
    if "project_type" in updates and updates["project_type"] not in ("standard", "long_term"):
        return jsonify({"error": "Invalid project_type. Use: standard, long_term"}), 400

    if not updates and engineer_ids is None:
        return jsonify({"error": "No valid fields to update"}), 400

    now = datetime.now(timezone.utc).isoformat()
    updates["updated_at"] = now

    with _db() as db:
        row = db.execute("SELECT * FROM cp_projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            return jsonify({"error": "Project not found"}), 404

        # Update project fields
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [project_id]
        db.execute(f"UPDATE cp_projects SET {set_clause} WHERE id = ?", values)

        # Update engineer assignments via junction table
        if engineer_ids is not None:
            db.execute(
                "DELETE FROM cp_project_engineers WHERE project_id = ?",
                (project_id,)
            )
            for uid in engineer_ids:
                _assign_color_if_needed(uid)
                db.execute(
                    "INSERT OR IGNORE INTO cp_project_engineers (project_id, user_id, created_at) "
                    "VALUES (?, ?, ?)",
                    (project_id, uid, now)
                )
            # Backwards compat: keep assigned_engineer_id in sync
            first_id = engineer_ids[0] if engineer_ids else None
            db.execute(
                "UPDATE cp_projects SET assigned_engineer_id = ? WHERE id = ?",
                (first_id, project_id)
            )

        row = db.execute("SELECT * FROM cp_projects WHERE id = ?", (project_id,)).fetchone()

    return jsonify({"message": "Project updated", "project": _row_to_dict(row)})


# ---------------------------------------------------------------------------
# API: Engineers
# ---------------------------------------------------------------------------

@cp_bp.route("/api/capacity-planning/engineers")
@login_required
def api_cp_engineers():
    """Get all engineers with their colors."""
    return jsonify({"engineers": _get_engineers_with_colors()})


@cp_bp.route("/api/capacity-planning/engineers/<int:user_id>/color", methods=["PUT"])
@admin_required
def api_cp_update_engineer_color(user_id):
    """Update an engineer's color (admin only)."""
    data = request.get_json()
    color = data.get("color", "").strip()
    if not color or not color.startswith("#"):
        return jsonify({"error": "Invalid color. Provide a hex color like #E85A71"}), 400

    now = datetime.now(timezone.utc).isoformat()
    with _db() as db:
        existing = db.execute(
            "SELECT user_id FROM cp_engineer_colors WHERE user_id = ?", (user_id,)
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE cp_engineer_colors SET color = ?, updated_at = ? WHERE user_id = ?",
                (color, now, user_id)
            )
        else:
            db.execute(
                "INSERT INTO cp_engineer_colors (user_id, color, updated_at) VALUES (?, ?, ?)",
                (user_id, color, now)
            )

    return jsonify({"message": "Color updated"})


# ---------------------------------------------------------------------------
# API: Bandwidth
# ---------------------------------------------------------------------------

@cp_bp.route("/api/capacity-planning/bandwidth")
@login_required
def api_cp_bandwidth():
    """Get week-by-week point load per engineer for a date range."""
    start_str = request.args.get("start")
    end_str = request.args.get("end")
    if not start_str or not end_str:
        return jsonify({"error": "start and end query params required"}), 400

    try:
        range_start = datetime.strptime(start_str, "%Y-%m-%d")
        range_end = datetime.strptime(end_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    # Build list of week start dates (Monday-aligned)
    # Align range_start to its Monday
    week_start = range_start - timedelta(days=range_start.weekday())
    weeks = []
    while week_start <= range_end:
        weeks.append(week_start)
        week_start += timedelta(days=7)

    if not weeks:
        return jsonify({"weeks": [], "engineers": []})

    with _db() as db:
        proj_engineers = _get_all_project_engineers(db)
        rows = db.execute(
            "SELECT * FROM cp_projects WHERE is_visible = 1 "
            "AND start_date IS NOT NULL AND end_date IS NOT NULL"
        ).fetchall()

    # Get user weekly_hours
    from auth.db import get_all_users
    users = get_all_users()
    user_hours = {u["id"]: u.get("weekly_hours") or 40 for u in users}

    # 1X/10X based on standard 40hr/week, 1.5 hrs/point
    HOURS_PER_POINT = 1.5
    one_x = round(40 / HOURS_PER_POINT)
    ten_x = one_x * 10

    # Build per-engineer weekly load + unassigned
    engineer_load = {}
    engineer_info = {}
    unassigned_load = [0.0] * len(weeks)

    for row in _rows_to_list(rows):
        try:
            p_start = datetime.strptime(row["start_date"], "%Y-%m-%d")
            p_end = datetime.strptime(row["end_date"], "%Y-%m-%d")
        except ValueError:
            continue

        total_pts = row.get("total_points", 0) or 0
        if total_pts == 0:
            continue

        duration_weeks = max(1, math.ceil((p_end - p_start).days / 7))
        weekly_pts = total_pts / duration_weeks
        engineers = proj_engineers.get(row["id"], [])

        if not engineers:
            # Track unassigned work
            for i, wk in enumerate(weeks):
                wk_end = wk + timedelta(days=6)
                if p_start <= wk_end and p_end >= wk:
                    unassigned_load[i] += weekly_pts
            continue

        pts_per_engineer_per_week = weekly_pts / len(engineers)

        for eng in engineers:
            uid = eng["id"]
            if uid not in engineer_load:
                engineer_load[uid] = [0.0] * len(weeks)
                engineer_info[uid] = eng

            for i, wk in enumerate(weeks):
                wk_end = wk + timedelta(days=6)
                if p_start <= wk_end and p_end >= wk:
                    engineer_load[uid][i] += pts_per_engineer_per_week

    # Build response
    week_labels = [w.strftime("%Y-%m-%d") for w in weeks]
    engineers_out = []
    for uid, load in sorted(engineer_load.items(), key=lambda x: engineer_info[x[0]]["username"]):
        info = engineer_info[uid]
        engineers_out.append({
            "id": uid,
            "username": info["username"],
            "color": info.get("color"),
            "weekly_hours": user_hours.get(uid, 40),
            "weekly_load": [round(v, 1) for v in load],
        })

    return jsonify({
        "weeks": week_labels,
        "engineers": engineers_out,
        "unassigned": [round(v, 1) for v in unassigned_load],
        "oneX": one_x,
        "tenX": ten_x,
    })


# ---------------------------------------------------------------------------
# API: Sync
# ---------------------------------------------------------------------------

@cp_bp.route("/api/capacity-planning/sync", methods=["POST"])
@admin_required
def api_cp_sync():
    """Trigger ClickUp project sync (admin only)."""
    from app import clickup_request
    result = sync_projects_from_clickup(clickup_request)
    return jsonify(result)
