"""
SQLite persistence layer for Client Health Dashboard data.

Stores the full client health JSON in a single-row table so page loads
never trigger live API calls.  A background job (APScheduler) rebuilds
the cache nightly at midnight EST.
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "client_health_cache.db")

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection (created lazily)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    return conn


def init_db():
    """Create the cache table if it doesn't exist."""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS client_health (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            data_json   TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS client_mappings (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            data_json   TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL
        )
    """)
    conn.commit()


def read_cache() -> Optional[dict]:
    """Return the cached health payload, or None if empty."""
    try:
        conn = _get_conn()
        init_db()
        row = conn.execute(
            "SELECT data_json, updated_at FROM client_health WHERE id = 1"
        ).fetchone()
        if row:
            data = json.loads(row[0])
            data["last_updated"] = row[1]
            return data
    except Exception as e:
        logger.error(f"Error reading client health cache: {e}")
    return None


def write_cache(data: dict):
    """Upsert the health payload into SQLite."""
    try:
        conn = _get_conn()
        init_db()
        now = datetime.now(timezone.utc).isoformat()
        # Store last_updated inside the JSON as well
        data["last_updated"] = now
        conn.execute(
            """INSERT INTO client_health (id, data_json, updated_at)
               VALUES (1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json,
                                              updated_at=excluded.updated_at""",
            (json.dumps(data), now),
        )
        conn.commit()
        logger.info(f"Client health cache written at {now}")
    except Exception as e:
        logger.error(f"Error writing client health cache: {e}")


def is_cache_empty() -> bool:
    """Check whether the cache has any data (for first-run detection)."""
    try:
        conn = _get_conn()
        init_db()
        row = conn.execute("SELECT COUNT(*) FROM client_health").fetchone()
        return row[0] == 0
    except Exception:
        return True
