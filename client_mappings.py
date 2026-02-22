"""
Client Mappings - Email domain and Grain recording mappings for client matching.

Uses SQLite (via client_health_cache) as primary storage so data persists on
ephemeral filesystems like Render. Falls back to client_mappings.json as seed data.
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

MAPPINGS_FILE = os.path.join(os.path.dirname(__file__), "client_mappings.json")

_DEFAULT = {
    "email_domains": {},
    "grain_matches": {}
}


def _read_db():
    """Read mappings from SQLite."""
    try:
        from client_health_cache import _get_conn, init_db
        init_db()
        conn = _get_conn()
        row = conn.execute(
            "SELECT data_json FROM client_mappings WHERE id = 1"
        ).fetchone()
        if row:
            return json.loads(row[0])
    except Exception as e:
        logger.debug(f"Could not read mappings from SQLite: {e}")
    return None


def _write_db(data):
    """Write mappings to SQLite."""
    try:
        from client_health_cache import _get_conn, init_db
        from datetime import datetime, timezone
        init_db()
        conn = _get_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO client_mappings (id, data_json, updated_at)
               VALUES (1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json,
                                              updated_at=excluded.updated_at""",
            (json.dumps(data), now),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error writing mappings to SQLite: {e}")
        return False


def load_mappings():
    """Load client mappings. Priority: SQLite > JSON file > empty default."""
    # Try SQLite first
    data = _read_db()
    if data:
        if "email_domains" not in data:
            data["email_domains"] = {}
        if "grain_matches" not in data:
            data["grain_matches"] = {}
        return data

    # Fall back to JSON file (seed data)
    try:
        if os.path.exists(MAPPINGS_FILE):
            with open(MAPPINGS_FILE, 'r') as f:
                data = json.load(f)
                if "email_domains" not in data:
                    data["email_domains"] = {}
                if "grain_matches" not in data:
                    data["grain_matches"] = {}
                # Seed SQLite from JSON file
                _write_db(data)
                logger.info("Seeded SQLite mappings from client_mappings.json")
                return data
    except Exception as e:
        logger.error(f"Error loading client mappings from file: {e}")

    return dict(_DEFAULT)


def save_mappings(data):
    """Save client mappings to SQLite (primary), JSON file, and optionally push to git."""
    ok = _write_db(data)

    # Also write to JSON file
    try:
        with open(MAPPINGS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

    # Push to git so mappings survive Render deploys
    _push_mappings_to_git()

    if ok:
        logger.info("Client mappings saved to SQLite + git")
    return ok


def _push_mappings_to_git():
    """Commit and push client_mappings.json to git (best effort)."""
    import subprocess
    try:
        repo_dir = os.path.dirname(MAPPINGS_FILE)
        env = os.environ.copy()
        # Use GitHub PAT if available
        pat = os.environ.get("GITHUB_PAT", "")

        # Configure git identity
        subprocess.run(["git", "config", "user.email", "bot@pulsemarketing.co"],
                       cwd=repo_dir, capture_output=True, timeout=5)
        subprocess.run(["git", "config", "user.name", "Pulse Bot"],
                       cwd=repo_dir, capture_output=True, timeout=5)

        subprocess.run(["git", "add", "client_mappings.json"],
                       cwd=repo_dir, capture_output=True, timeout=10)
        result = subprocess.run(
            ["git", "commit", "-m", "Auto-save client mappings"],
            cwd=repo_dir, capture_output=True, timeout=10, text=True
        )
        if result.returncode == 0:
            # Use PAT in remote URL for auth
            if pat:
                push_url = f"https://{pat}@github.com/jakeshumaker6/agile-dashboard.git"
                subprocess.run(["git", "push", push_url, "HEAD:main"],
                               cwd=repo_dir, capture_output=True, timeout=30)
            else:
                subprocess.run(["git", "push"],
                               cwd=repo_dir, capture_output=True, timeout=30)
            logger.info("Client mappings pushed to git")
        else:
            logger.debug("No mapping changes to commit")
    except Exception as e:
        logger.debug(f"Git push failed (non-critical): {e}")


def save_email_mapping(client, domains, keywords):
    """Save email domain mapping for a specific client."""
    data = load_mappings()
    data["email_domains"][client] = {
        "domains": domains,
        "keywords": keywords
    }
    return save_mappings(data)


def save_grain_match(recording_id, client):
    """Save a Grain recording -> client match."""
    data = load_mappings()
    data["grain_matches"][recording_id] = client
    return save_mappings(data)


def get_email_domains(client):
    """Get email domains configured for a client, or None."""
    data = load_mappings()
    entry = data["email_domains"].get(client)
    if entry and entry.get("domains"):
        return entry["domains"]
    return None


def get_grain_match(recording_id):
    """Get the client matched to a Grain recording, or None."""
    data = load_mappings()
    return data["grain_matches"].get(recording_id)
