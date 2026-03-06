"""
Capacity Planning Module — Gantt-style project timeline with engineer assignments.

Pulls projects (ClickUp lists from the Operations space) and lets admins
assign engineers, set start/end dates, and rate difficulty.
All users can view; only admins can edit.
"""

import os
import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
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

        CREATE INDEX IF NOT EXISTS idx_cp_projects_list_id
            ON cp_projects(clickup_list_id);
        CREATE INDEX IF NOT EXISTS idx_cp_projects_engineer
            ON cp_projects(assigned_engineer_id);
    """)
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
    try:
        engineers = _get_engineers_with_colors()
        eng_map = {e["id"]: e for e in engineers}
    except Exception as e:
        logger.error(f"Failed to load engineers: {e}", exc_info=True)
        eng_map = {}

    with _db() as db:
        rows = db.execute(
            "SELECT * FROM cp_projects WHERE is_visible = 1 ORDER BY folder_name, name"
        ).fetchall()

    projects = []
    for row in _rows_to_list(rows):
        try:
            eng_id = row.get("assigned_engineer_id")
            if eng_id is not None:
                eng_id = int(eng_id)
            engineer = None
            if eng_id and eng_id in eng_map:
                engineer = {
                    "id": eng_map[eng_id]["id"],
                    "username": eng_map[eng_id]["username"],
                    "color": eng_map[eng_id].get("color"),
                }

            difficulty = row.get("difficulty", "medium") or "medium"
            total_pts = row.get("total_points", 0) or 0
            complexity_label = f"{total_pts} pts / {difficulty.capitalize()}"

            projects.append({
                "id": row["id"],
                "clickup_list_id": row["clickup_list_id"],
                "name": row["name"],
                "folder_name": row.get("folder_name", ""),
                "assigned_engineer": engineer,
                "start_date": row.get("start_date"),
                "end_date": row.get("end_date"),
                "difficulty": difficulty,
                "total_points": total_pts,
                "task_count": row.get("task_count", 0) or 0,
                "complexity_label": complexity_label,
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

    allowed = {"start_date", "end_date", "assigned_engineer_id", "difficulty"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    # Validate difficulty
    if "difficulty" in updates and updates["difficulty"] not in ("easy", "medium", "hard", "complex"):
        return jsonify({"error": "Invalid difficulty. Use: easy, medium, hard, complex"}), 400

    # Ensure engineer ID is stored as integer
    if "assigned_engineer_id" in updates:
        val = updates["assigned_engineer_id"]
        if val is not None:
            try:
                updates["assigned_engineer_id"] = int(val)
            except (TypeError, ValueError):
                updates["assigned_engineer_id"] = None

    # Auto-assign color if engineer is being assigned
    if updates.get("assigned_engineer_id"):
        _assign_color_if_needed(updates["assigned_engineer_id"])

    now = datetime.now(timezone.utc).isoformat()
    updates["updated_at"] = now

    with _db() as db:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [project_id]
        db.execute(f"UPDATE cp_projects SET {set_clause} WHERE id = ?", values)

        row = db.execute("SELECT * FROM cp_projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            return jsonify({"error": "Project not found"}), 404

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
# API: Sync
# ---------------------------------------------------------------------------

@cp_bp.route("/api/capacity-planning/sync", methods=["POST"])
@admin_required
def api_cp_sync():
    """Trigger ClickUp project sync (admin only)."""
    from app import clickup_request
    result = sync_projects_from_clickup(clickup_request)
    return jsonify(result)
