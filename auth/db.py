"""
SQLite persistence layer for user authentication.

Follows the same patterns as client_health_cache.py:
- Thread-local connections
- WAL mode for concurrency
- Lazy initialization
"""

import json
import logging
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List

import bcrypt

logger = logging.getLogger(__name__)

# Use persistent disk on Render (/data), fall back to app directory for local dev
if os.path.isdir("/data"):
    DB_PATH = "/data/auth.db"
else:
    DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "auth.db")

logger.info(f"Auth database path: {DB_PATH}")

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection (created lazily)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row  # Enable dict-like access
        _local.conn = conn
    return conn


def init_db():
    """Create the users table if it doesn't exist."""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            clickup_id              TEXT UNIQUE,
            email                   TEXT UNIQUE NOT NULL,
            username                TEXT NOT NULL,
            password_hash           TEXT,
            role                    TEXT NOT NULL DEFAULT 'regular',
            totp_secret             TEXT,
            totp_enabled            INTEGER DEFAULT 0,
            last_2fa_at             TEXT,
            password_reset_token    TEXT,
            password_reset_expires  TEXT,
            invite_token            TEXT,
            invite_expires          TEXT,
            is_active               INTEGER DEFAULT 1,
            weekly_hours            INTEGER DEFAULT 40,
            created_at              TEXT NOT NULL,
            updated_at              TEXT NOT NULL
        )
    """)
    # Migration: add weekly_hours column if it doesn't exist
    try:
        conn.execute("ALTER TABLE users ADD COLUMN weekly_hours INTEGER DEFAULT 40")
        conn.commit()
    except Exception:
        pass  # Column already exists
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_clickup_id ON users(clickup_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_invite_token ON users(invite_token)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_reset_token ON users(password_reset_token)
    """)
    conn.commit()
    logger.info("Auth database initialized")


def _row_to_dict(row: sqlite3.Row) -> Optional[Dict]:
    """Convert a sqlite3.Row to a dictionary."""
    if row is None:
        return None
    return dict(row)


# ============ User CRUD Operations ============

def get_user_by_email(email: str) -> Optional[Dict]:
    """Get a user by their email address."""
    try:
        conn = _get_conn()
        init_db()
        row = conn.execute(
            "SELECT * FROM users WHERE email = ? AND is_active = 1",
            (email.lower(),)
        ).fetchone()
        return _row_to_dict(row)
    except Exception as e:
        logger.error(f"Error getting user by email: {e}")
        return None


def get_user_by_id(user_id: int) -> Optional[Dict]:
    """Get a user by their ID."""
    try:
        conn = _get_conn()
        init_db()
        row = conn.execute(
            "SELECT * FROM users WHERE id = ? AND is_active = 1",
            (user_id,)
        ).fetchone()
        return _row_to_dict(row)
    except Exception as e:
        logger.error(f"Error getting user by id: {e}")
        return None


def get_user_by_clickup_id(clickup_id: str) -> Optional[Dict]:
    """Get a user by their ClickUp ID."""
    try:
        conn = _get_conn()
        init_db()
        row = conn.execute(
            "SELECT * FROM users WHERE clickup_id = ?",
            (clickup_id,)
        ).fetchone()
        return _row_to_dict(row)
    except Exception as e:
        logger.error(f"Error getting user by clickup_id: {e}")
        return None


def get_user_by_invite_token(token: str) -> Optional[Dict]:
    """Get a user by their invite token (for password setup)."""
    try:
        conn = _get_conn()
        init_db()
        row = conn.execute(
            "SELECT * FROM users WHERE invite_token = ? AND is_active = 1",
            (token,)
        ).fetchone()
        return _row_to_dict(row)
    except Exception as e:
        logger.error(f"Error getting user by invite token: {e}")
        return None


def get_user_by_reset_token(token: str) -> Optional[Dict]:
    """Get a user by their password reset token."""
    try:
        conn = _get_conn()
        init_db()
        row = conn.execute(
            "SELECT * FROM users WHERE password_reset_token = ? AND is_active = 1",
            (token,)
        ).fetchone()
        return _row_to_dict(row)
    except Exception as e:
        logger.error(f"Error getting user by reset token: {e}")
        return None


def get_all_users() -> List[Dict]:
    """Get all active users."""
    try:
        conn = _get_conn()
        init_db()
        rows = conn.execute(
            "SELECT * FROM users WHERE is_active = 1 ORDER BY username"
        ).fetchall()
        return [_row_to_dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Error getting all users: {e}")
        return []


def create_user(
    email: str,
    username: str,
    clickup_id: str = None,
    role: str = "regular",
    password: str = None,
) -> Optional[Dict]:
    """
    Create a new user.

    If password is provided, hash it immediately.
    Otherwise, generate an invite token for password setup.
    """
    try:
        conn = _get_conn()
        init_db()
        now = datetime.now(timezone.utc).isoformat()

        password_hash = None
        invite_token = None
        invite_expires = None

        if password:
            password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        else:
            # Generate invite token for password setup
            invite_token = secrets.token_urlsafe(32)
            invite_expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        conn.execute(
            """INSERT INTO users (
                clickup_id, email, username, password_hash, role,
                invite_token, invite_expires, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (clickup_id, email.lower(), username, password_hash, role,
             invite_token, invite_expires, now, now)
        )
        conn.commit()

        logger.info(f"Created user: {email} (role: {role})")
        return get_user_by_email(email)
    except sqlite3.IntegrityError as e:
        logger.warning(f"User already exists: {email} - {e}")
        return None
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        return None


def update_user(user_id: int, **kwargs) -> bool:
    """
    Update user fields.

    Supported fields: username, role, totp_secret, totp_enabled, last_2fa_at,
    password_reset_token, password_reset_expires, invite_token, invite_expires, is_active, weekly_hours
    """
    allowed_fields = {
        'username', 'role', 'totp_secret', 'totp_enabled', 'last_2fa_at',
        'password_reset_token', 'password_reset_expires',
        'invite_token', 'invite_expires', 'is_active', 'clickup_id', 'weekly_hours'
    }

    # Filter to allowed fields only
    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
    if not updates:
        return False

    try:
        conn = _get_conn()
        init_db()
        now = datetime.now(timezone.utc).isoformat()
        updates['updated_at'] = now

        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [user_id]

        conn.execute(
            f"UPDATE users SET {set_clause} WHERE id = ?",
            values
        )
        conn.commit()
        logger.info(f"Updated user {user_id}: {list(updates.keys())}")
        return True
    except Exception as e:
        logger.error(f"Error updating user: {e}")
        return False


def set_user_password(user_id: int, password: str) -> bool:
    """Set a user's password (hashes it with bcrypt)."""
    try:
        conn = _get_conn()
        init_db()
        now = datetime.now(timezone.utc).isoformat()

        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        conn.execute(
            """UPDATE users SET
                password_hash = ?,
                invite_token = NULL,
                invite_expires = NULL,
                updated_at = ?
            WHERE id = ?""",
            (password_hash, now, user_id)
        )
        conn.commit()
        logger.info(f"Password set for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Error setting password: {e}")
        return False


def verify_password(user: Dict, password: str) -> bool:
    """Verify a user's password against the stored hash."""
    if not user or not user.get('password_hash'):
        return False
    try:
        return bcrypt.checkpw(
            password.encode('utf-8'),
            user['password_hash'].encode('utf-8')
        )
    except Exception as e:
        logger.error(f"Error verifying password: {e}")
        return False


def generate_password_reset_token(user_id: int) -> Optional[str]:
    """Generate a password reset token (valid for 1 hour)."""
    try:
        conn = _get_conn()
        init_db()
        now = datetime.now(timezone.utc).isoformat()
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        conn.execute(
            """UPDATE users SET
                password_reset_token = ?,
                password_reset_expires = ?,
                updated_at = ?
            WHERE id = ?""",
            (token, expires, now, user_id)
        )
        conn.commit()
        logger.info(f"Password reset token generated for user {user_id}")
        return token
    except Exception as e:
        logger.error(f"Error generating reset token: {e}")
        return None


def clear_password_reset_token(user_id: int) -> bool:
    """Clear the password reset token after use."""
    try:
        conn = _get_conn()
        init_db()
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """UPDATE users SET
                password_reset_token = NULL,
                password_reset_expires = NULL,
                updated_at = ?
            WHERE id = ?""",
            (now, user_id)
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error clearing reset token: {e}")
        return False


def is_reset_token_valid(user: Dict) -> bool:
    """Check if the password reset token is still valid (not expired)."""
    if not user or not user.get('password_reset_token') or not user.get('password_reset_expires'):
        return False
    try:
        expires = datetime.fromisoformat(user['password_reset_expires'])
        return datetime.now(timezone.utc) < expires
    except Exception:
        return False


def is_invite_token_valid(user: Dict) -> bool:
    """Check if the invite token is still valid (not expired)."""
    if not user or not user.get('invite_token') or not user.get('invite_expires'):
        return False
    try:
        expires = datetime.fromisoformat(user['invite_expires'])
        return datetime.now(timezone.utc) < expires
    except Exception:
        return False


# ============ 2FA Operations ============

def set_totp_secret(user_id: int, secret: str) -> bool:
    """Set the TOTP secret for a user (during 2FA setup)."""
    try:
        conn = _get_conn()
        init_db()
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """UPDATE users SET
                totp_secret = ?,
                updated_at = ?
            WHERE id = ?""",
            (secret, now, user_id)
        )
        conn.commit()
        logger.info(f"TOTP secret set for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Error setting TOTP secret: {e}")
        return False


def enable_totp(user_id: int) -> bool:
    """Enable TOTP for a user (after successful verification)."""
    try:
        conn = _get_conn()
        init_db()
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """UPDATE users SET
                totp_enabled = 1,
                last_2fa_at = ?,
                updated_at = ?
            WHERE id = ?""",
            (now, now, user_id)
        )
        conn.commit()
        logger.info(f"TOTP enabled for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Error enabling TOTP: {e}")
        return False


def update_last_2fa(user_id: int) -> bool:
    """Update the last 2FA verification timestamp."""
    try:
        conn = _get_conn()
        init_db()
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """UPDATE users SET
                last_2fa_at = ?,
                updated_at = ?
            WHERE id = ?""",
            (now, now, user_id)
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error updating last_2fa: {e}")
        return False


def needs_2fa_verification(user: Dict) -> bool:
    """
    Check if user needs to verify 2FA.

    Returns True if:
    - 2FA is enabled AND (never verified OR last verification > 24 hours ago)
    """
    if not user or not user.get('totp_enabled'):
        return False

    last_2fa = user.get('last_2fa_at')
    if not last_2fa:
        return True

    try:
        last_2fa_dt = datetime.fromisoformat(last_2fa)
        hours_since = (datetime.now(timezone.utc) - last_2fa_dt).total_seconds() / 3600
        return hours_since > 24
    except Exception:
        return True


def needs_2fa_setup(user: Dict) -> bool:
    """
    Check if user needs to set up 2FA (mandatory for all users).

    Returns True if totp_enabled is False.
    """
    if not user:
        return True
    return not user.get('totp_enabled')


# ============ User Deactivation ============

def deactivate_user(user_id: int) -> bool:
    """Soft-delete a user by setting is_active = 0."""
    return update_user(user_id, is_active=0)


def reactivate_user(user_id: int) -> bool:
    """Reactivate a soft-deleted user."""
    return update_user(user_id, is_active=1)
