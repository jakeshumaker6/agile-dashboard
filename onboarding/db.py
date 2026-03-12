"""
Database helpers for the Onboarding module.

Schema creation, connection management, and CRUD operations
for participants, modules, progress, and tool setup tracking.
"""

import os
import json
import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _db_path():
    """Use /data on Render (persistent disk), fall back to app dir for local dev."""
    if os.path.isdir("/data"):
        return "/data/onboarding.db"
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "onboarding.db")


def _get_db():
    conn = sqlite3.connect(_db_path(), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
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


def _now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def init_onboarding_db():
    """Create onboarding tables if they don't exist."""
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS onboarding_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            track TEXT NOT NULL DEFAULT 'general',
            start_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            current_day INTEGER DEFAULT 1,
            first_ticket_url TEXT,
            clickup_task_id TEXT,
            touchpoint_schedule TEXT,
            satisfaction_rating INTEGER,
            welcome_message TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS onboarding_modules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day INTEGER NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            content_html TEXT,
            content_type TEXT DEFAULT 'text',
            loom_url TEXT,
            track TEXT NOT NULL DEFAULT 'all',
            is_required INTEGER DEFAULT 1,
            estimated_minutes INTEGER DEFAULT 15,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS onboarding_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id INTEGER NOT NULL,
            module_id INTEGER NOT NULL,
            status TEXT DEFAULT 'not_started',
            response_text TEXT,
            response_data TEXT,
            started_at TEXT,
            completed_at TEXT,
            UNIQUE(participant_id, module_id),
            FOREIGN KEY (participant_id)
                REFERENCES onboarding_participants(id) ON DELETE CASCADE,
            FOREIGN KEY (module_id)
                REFERENCES onboarding_modules(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS onboarding_tool_setup (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id INTEGER NOT NULL,
            tool_name TEXT NOT NULL,
            confirmed INTEGER DEFAULT 0,
            confirmed_at TEXT,
            notes TEXT,
            UNIQUE(participant_id, tool_name),
            FOREIGN KEY (participant_id)
                REFERENCES onboarding_participants(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_ob_participants_email
            ON onboarding_participants(email);
        CREATE INDEX IF NOT EXISTS idx_ob_participants_status
            ON onboarding_participants(status);
        CREATE INDEX IF NOT EXISTS idx_ob_modules_day
            ON onboarding_modules(day);
        CREATE INDEX IF NOT EXISTS idx_ob_progress_participant
            ON onboarding_progress(participant_id);
        CREATE INDEX IF NOT EXISTS idx_ob_progress_module
            ON onboarding_progress(module_id);
        CREATE INDEX IF NOT EXISTS idx_ob_tool_participant
            ON onboarding_tool_setup(participant_id);
    """)
    conn.close()
    logger.info("Onboarding DB initialized")


# ---------------------------------------------------------------------------
# Participant CRUD
# ---------------------------------------------------------------------------

def create_participant(name, email, track, start_date, touchpoint_schedule=None):
    """Create a new onboarding participant. Returns the new row dict."""
    now = _now()
    tp_json = json.dumps(touchpoint_schedule) if touchpoint_schedule else None
    with _db() as conn:
        conn.execute(
            """INSERT INTO onboarding_participants
               (name, email, track, start_date, touchpoint_schedule,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, email.lower().strip(), track, start_date, tp_json, now, now),
        )
        row = conn.execute(
            "SELECT * FROM onboarding_participants WHERE email = ?",
            (email.lower().strip(),),
        ).fetchone()
        return _row_to_dict(row)


def get_participant(participant_id):
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM onboarding_participants WHERE id = ?",
            (participant_id,),
        ).fetchone()
        return _row_to_dict(row)


def get_active_participant_by_email(email):
    """Find an active onboarding participant by email."""
    if not email:
        return None
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM onboarding_participants WHERE email = ? AND status = 'active'",
            (email.lower().strip(),),
        ).fetchone()
        return _row_to_dict(row)


def get_active_participant_by_user_id(user_id):
    """Find an active onboarding participant by auth user_id."""
    if not user_id:
        return None
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM onboarding_participants WHERE user_id = ? AND status = 'active'",
            (user_id,),
        ).fetchone()
        return _row_to_dict(row)


def list_participants():
    """Return all participants ordered by creation date (newest first)."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM onboarding_participants ORDER BY created_at DESC"
        ).fetchall()
        return _rows_to_list(rows)


def update_participant(participant_id, **fields):
    """Update arbitrary fields on a participant."""
    allowed = {
        "name", "email", "track", "start_date", "status", "current_day",
        "first_ticket_url", "clickup_task_id", "touchpoint_schedule",
        "satisfaction_rating", "welcome_message", "completed_at", "user_id",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return None
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [participant_id]
    with _db() as conn:
        conn.execute(
            f"UPDATE onboarding_participants SET {set_clause} WHERE id = ?",
            values,
        )
        return get_participant(participant_id)


def link_participant_to_user(email, user_id):
    """Link an onboarding participant to their auth user account."""
    with _db() as conn:
        conn.execute(
            "UPDATE onboarding_participants SET user_id = ?, updated_at = ? "
            "WHERE email = ? AND user_id IS NULL",
            (user_id, _now(), email.lower().strip()),
        )


# ---------------------------------------------------------------------------
# Module CRUD
# ---------------------------------------------------------------------------

def get_modules_for_day(day, track="all"):
    """Get modules for a specific day, filtered by track."""
    with _db() as conn:
        rows = conn.execute(
            """SELECT * FROM onboarding_modules
               WHERE day = ? AND (track = 'all' OR track = ?)
               ORDER BY sort_order""",
            (day, track),
        ).fetchall()
        return _rows_to_list(rows)


def get_all_modules():
    """Get all modules ordered by day and sort_order."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM onboarding_modules ORDER BY day, sort_order"
        ).fetchall()
        return _rows_to_list(rows)


def get_module(module_id):
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM onboarding_modules WHERE id = ?", (module_id,)
        ).fetchone()
        return _row_to_dict(row)


def create_module(day, title, slug, content_html="", content_type="text",
                  loom_url=None, track="all", is_required=1,
                  estimated_minutes=15, sort_order=0):
    """Create a new onboarding module. Returns the new row dict."""
    now = _now()
    with _db() as conn:
        conn.execute(
            """INSERT INTO onboarding_modules
               (day, sort_order, title, slug, content_html, content_type,
                loom_url, track, is_required, estimated_minutes,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (day, sort_order, title, slug, content_html, content_type,
             loom_url, track, is_required, estimated_minutes, now, now),
        )
        row = conn.execute(
            "SELECT * FROM onboarding_modules WHERE slug = ?", (slug,)
        ).fetchone()
        return _row_to_dict(row)


def update_module(module_id, **fields):
    """Update arbitrary fields on a module."""
    allowed = {
        "day", "sort_order", "title", "slug", "content_html", "content_type",
        "loom_url", "track", "is_required", "estimated_minutes",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return None
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [module_id]
    with _db() as conn:
        conn.execute(
            f"UPDATE onboarding_modules SET {set_clause} WHERE id = ?",
            values,
        )
        return get_module(module_id)


def delete_module(module_id):
    with _db() as conn:
        conn.execute("DELETE FROM onboarding_modules WHERE id = ?", (module_id,))


def module_count():
    """Return total number of modules."""
    with _db() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM onboarding_modules").fetchone()
        return row["cnt"] if row else 0


# ---------------------------------------------------------------------------
# Progress CRUD
# ---------------------------------------------------------------------------

def get_progress_for_participant(participant_id):
    """Get all progress records for a participant."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM onboarding_progress WHERE participant_id = ?",
            (participant_id,),
        ).fetchall()
        return _rows_to_list(rows)


def get_progress(participant_id, module_id):
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM onboarding_progress WHERE participant_id = ? AND module_id = ?",
            (participant_id, module_id),
        ).fetchone()
        return _row_to_dict(row)


def upsert_progress(participant_id, module_id, status, response_text=None,
                    response_data=None):
    """Create or update progress for a participant on a module."""
    now = _now()
    existing = get_progress(participant_id, module_id)
    rd_json = json.dumps(response_data) if response_data else None

    with _db() as conn:
        if existing:
            fields = {"status": status, "response_text": response_text}
            if rd_json:
                fields["response_data"] = rd_json
            if status == "completed" and not existing.get("completed_at"):
                fields["completed_at"] = now
            if status == "in_progress" and not existing.get("started_at"):
                fields["started_at"] = now
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            values = list(fields.values()) + [participant_id, module_id]
            conn.execute(
                f"UPDATE onboarding_progress SET {set_clause} "
                "WHERE participant_id = ? AND module_id = ?",
                values,
            )
        else:
            started = now if status in ("in_progress", "completed") else None
            completed = now if status == "completed" else None
            conn.execute(
                """INSERT INTO onboarding_progress
                   (participant_id, module_id, status, response_text,
                    response_data, started_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (participant_id, module_id, status, response_text,
                 rd_json, started, completed),
            )
    return get_progress(participant_id, module_id)


def calculate_progress(participant_id, track):
    """Calculate overall and per-day progress for a participant.

    Returns dict with overall_pct, per_day counts, and remaining minutes.
    """
    with _db() as conn:
        modules = conn.execute(
            """SELECT id, day, estimated_minutes FROM onboarding_modules
               WHERE track = 'all' OR track = ?""",
            (track,),
        ).fetchall()
        progress_rows = conn.execute(
            "SELECT module_id, status FROM onboarding_progress WHERE participant_id = ?",
            (participant_id,),
        ).fetchall()

    progress_map = {r["module_id"]: r["status"] for r in progress_rows}
    total = len(modules)
    completed = sum(1 for m in modules if progress_map.get(m["id"]) == "completed")
    remaining_mins = sum(
        m["estimated_minutes"] for m in modules
        if progress_map.get(m["id"]) != "completed"
    )

    days = {}
    for day_num in (1, 2, 3):
        day_modules = [m for m in modules if m["day"] == day_num]
        day_done = sum(
            1 for m in day_modules if progress_map.get(m["id"]) == "completed"
        )
        days[day_num] = {
            "total": len(day_modules),
            "completed": day_done,
            "pct": round(day_done / len(day_modules) * 100) if day_modules else 0,
        }

    return {
        "total": total,
        "completed": completed,
        "overall_pct": round(completed / total * 100) if total else 0,
        "remaining_minutes": remaining_mins,
        "days": days,
    }


# ---------------------------------------------------------------------------
# Tool Setup CRUD
# ---------------------------------------------------------------------------

def get_tool_setup(participant_id):
    """Get all tool setup records for a participant."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM onboarding_tool_setup WHERE participant_id = ?",
            (participant_id,),
        ).fetchall()
        return _rows_to_list(rows)


def confirm_tool(participant_id, tool_name):
    """Mark a tool as set up for a participant."""
    now = _now()
    with _db() as conn:
        conn.execute(
            """INSERT INTO onboarding_tool_setup
               (participant_id, tool_name, confirmed, confirmed_at)
               VALUES (?, ?, 1, ?)
               ON CONFLICT(participant_id, tool_name)
               DO UPDATE SET confirmed = 1, confirmed_at = ?""",
            (participant_id, tool_name, now, now),
        )
    return {"tool_name": tool_name, "confirmed": True, "confirmed_at": now}
