"""
Pulse Agile Dashboard - ClickUp Sprint Metrics

A custom dashboard for viewing Agile/sprint progress from ClickUp,
including Fibonacci points, time tracking, and efficiency analysis.
"""

import os
import json
import time
import sqlite3
import logging
import urllib.request
import urllib.error
import atexit
from datetime import datetime, timedelta
from collections import defaultdict
from functools import wraps
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from client_health import build_client_health_data, fetch_grain_recordings, fetch_active_accounts, match_client_to_recording
from client_health_cache import read_cache, write_cache, is_cache_empty
from sentiment_overrides import load_overrides, save_override, delete_override, apply_overrides
from client_mappings import load_mappings, save_email_mapping, save_grain_match
from auth import auth_bp, login_required, admin_required, init_db as init_auth_db
from eos import eos_bp, init_eos_db
from auth.user_sync import sync_users_from_clickup
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import pytz

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]  # Required — set in Render env vars
app.permanent_session_lifetime = timedelta(hours=24)  # Sessions last 24 hours

# Thread locks for shared mutable state (scheduler + request threads)
import threading
_cache_lock = threading.Lock()
_daily_cache_lock = threading.Lock()
_capacity_lock = threading.Lock()
_grain_cache_lock = threading.Lock()
_excluded_assignees_lock = threading.Lock()

# Simple in-memory cache (for short-term API response caching)
_cache = {}
CACHE_TTL = 60  # 60 seconds cache

# Request-scoped metrics cache (cleared between requests)
# Keyed by (week_offset, assignee_id) to avoid redundant calculate_metrics calls
_metrics_request_cache = {}

# SQLite task cache for persistent storage between refreshes
def _persistent_path(filename):
    """Use /data on Render (persistent disk), fall back to app dir for local dev."""
    if os.path.isdir("/data"):
        return os.path.join("/data", filename)
    return os.path.join(os.path.dirname(__file__), filename)

TASK_CACHE_DB = _persistent_path("task_cache.db")

# Daily cache file for pre-computed dashboard data
DAILY_CACHE_FILE = _persistent_path("daily_cache.json")
_daily_cache = None  # In-memory copy of daily cache
_daily_cache_loaded = False

# Team capacity config file (shared across all users)
CAPACITY_CONFIG_FILE = _persistent_path("team_capacity.json")
_capacity_config = None  # In-memory copy

# Configuration
CLICKUP_API_TOKEN = os.environ["CLICKUP_API_TOKEN"]  # Required — set in Render env vars
CLICKUP_TEAM_ID = os.environ.get("CLICKUP_TEAM_ID", "90132317968")
FIBONACCI_FIELD_ID = os.environ.get("FIBONACCI_FIELD_ID", "c88be994-51de-4bd3-b2f5-7850202b84bd")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Register auth blueprint
app.register_blueprint(auth_bp)
app.register_blueprint(eos_bp)


@app.before_request
def _clear_request_caches():
    """Clear request-scoped caches at the start of each HTTP request."""
    global _metrics_request_cache
    _metrics_request_cache = {}

# Initialize auth database on startup
init_auth_db()
init_eos_db()

# Fibonacci score option IDs (for reverse lookup)
SCORE_OPTIONS = {
    "86763539-3c8e-497a-8995-e4349917bc80": 1,
    "20341d9b-5f30-4d78-97c0-ad17a5f3a04c": 2,
    "5db17019-f2d7-417b-9cc5-fe727f3d29f1": 3,
    "8973f792-78cc-4fed-90f5-a88354fe881c": 5,
    "c57e955d-5247-494f-80e7-110e88ac5c89": 8,
    "ef195667-5b4f-4ae5-b878-7d55e0176fd3": 13,
}

# Expected hours per Fibonacci score (for efficiency calculation)
EXPECTED_HOURS = {
    1: {"min": 0.5, "max": 1, "mid": 0.75},
    2: {"min": 1, "max": 2, "mid": 1.5},
    3: {"min": 2, "max": 4, "mid": 3},
    5: {"min": 4, "max": 8, "mid": 6},
    8: {"min": 8, "max": 16, "mid": 12},
    13: {"min": 16, "max": 32, "mid": 24},
}

# Excluded folders
EXCLUDED_FOLDERS = ["Client Template"]

# Excluded assignees config file (persistent)
EXCLUDED_ASSIGNEES_FILE = _persistent_path("excluded_assignees.json")
_excluded_assignees_cache = None  # In-memory copy


def load_excluded_assignees() -> list:
    """Load excluded assignees from persistent JSON config."""
    global _excluded_assignees_cache
    with _excluded_assignees_lock:
        if _excluded_assignees_cache is not None:
            return _excluded_assignees_cache
        try:
            if os.path.exists(EXCLUDED_ASSIGNEES_FILE):
                with open(EXCLUDED_ASSIGNEES_FILE, 'r') as f:
                    _excluded_assignees_cache = json.load(f)
                    return _excluded_assignees_cache
        except Exception as e:
            logger.error(f"Error loading excluded assignees: {e}")
        # Default value (migrated from hardcoded list)
        _excluded_assignees_cache = ["Fazail Sabri"]
        return _excluded_assignees_cache


def save_excluded_assignees(names: list) -> bool:
    """Save excluded assignees to persistent JSON config."""
    global _excluded_assignees_cache
    with _excluded_assignees_lock:
        try:
            with open(EXCLUDED_ASSIGNEES_FILE, 'w') as f:
                json.dump(names, f, indent=2)
            _excluded_assignees_cache = names
            logger.info(f"Excluded assignees saved: {names}")
            return True
        except Exception as e:
            logger.error(f"Error saving excluded assignees: {e}")
            return False

# Default hours for new team members
DEFAULT_MEMBER_HOURS = 40

# Known hour overrides for specific team members (non-40 hour weeks)
# These are applied when a member first appears; user can customize in UI
KNOWN_HOUR_OVERRIDES = {
    "Luke Shumaker": 20,
    "Razvan Crisan": 10,
    "Adri Andika": 30,
}


def load_capacity_config():
    """Load saved team capacity configuration from file."""
    global _capacity_config

    with _capacity_lock:
        if _capacity_config is not None:
            return _capacity_config

        try:
            if os.path.exists(CAPACITY_CONFIG_FILE):
                with open(CAPACITY_CONFIG_FILE, 'r') as f:
                    _capacity_config = json.load(f)
                    logger.info(f"Capacity config loaded: {len(_capacity_config)} members")
                    return _capacity_config
        except Exception as e:
            logger.error(f"Error loading capacity config: {e}")

    return {}


def save_capacity_config(capacity: dict):
    """Save team capacity configuration to file (shared across all users)."""
    global _capacity_config

    with _capacity_lock:
        try:
            with open(CAPACITY_CONFIG_FILE, 'w') as f:
                json.dump(capacity, f, indent=2)
            _capacity_config = capacity
            logger.info(f"Capacity config saved: {len(capacity)} members")
            return True
        except Exception as e:
            logger.error(f"Error saving capacity config: {e}")
            return False


def get_pulse_team_members():
    """
    Get team members filtered to only @pulsemarketing.co emails.
    Returns list of team members from ClickUp who are internal Pulse employees.
    """
    all_members = get_team_members()
    pulse_members = []

    for member in all_members:
        email = member.get("email", "").lower()
        if email.endswith("@pulsemarketing.co"):
            pulse_members.append(member)

    return pulse_members


def build_team_capacity():
    """
    Build team capacity dict merging:
    1. Current Pulse team members from ClickUp
    2. Saved capacity config (user customizations)
    3. Known hour overrides for new members
    """
    pulse_members = get_pulse_team_members()
    saved_config = load_capacity_config()
    capacity = {}

    for member in pulse_members:
        name = member.get("username", "Unknown")
        if name in saved_config:
            # Use saved hours
            capacity[name] = saved_config[name]
        elif name in KNOWN_HOUR_OVERRIDES:
            # New member with known override
            capacity[name] = KNOWN_HOUR_OVERRIDES[name]
        else:
            # New member with default hours
            capacity[name] = DEFAULT_MEMBER_HOURS

    return capacity

def calculate_expected_points_from_hours(total_hours: float) -> float:
    """
    Calculate expected story points based on available team hours.

    Based on the Fibonacci scoring scale:
    - 1 pt = 0.5-1 hr (avg 0.75) → 0.75 hrs/pt
    - 2 pt = 1-2 hrs (avg 1.5) → 0.75 hrs/pt
    - 3 pt = 2-4 hrs (avg 3) → 1.0 hrs/pt
    - 5 pt = 4-8 hrs (avg 6) → 1.2 hrs/pt
    - 8 pt = 8-16 hrs (avg 12) → 1.5 hrs/pt
    - 13 pt = 16-32 hrs (avg 24) → 1.85 hrs/pt

    Using 1.5 hrs/pt to reflect team's typical 5-8 point task distribution.
    """
    HOURS_PER_POINT = 1.5

    return round(total_hours / HOURS_PER_POINT, 0)


def get_cached(key: str):
    """Get value from cache if not expired."""
    with _cache_lock:
        if key in _cache:
            value, expiry = _cache[key]
            if time.time() < expiry:
                return value
    return None


def set_cached(key: str, value, ttl: int = CACHE_TTL):
    """Set value in cache with TTL."""
    with _cache_lock:
        _cache[key] = (value, time.time() + ttl)


# ============================================================================
# SQLite Task Cache - Persistent storage so users never wait on ClickUp API
# ============================================================================

def _init_task_cache_db():
    """Create the task cache SQLite table if it doesn't exist."""
    conn = sqlite3.connect(TASK_CACHE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_cache (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_cache_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _write_task_cache(tasks: list):
    """Write all tasks to SQLite cache (replaces existing data)."""
    conn = sqlite3.connect(TASK_CACHE_DB)
    conn.execute("DELETE FROM task_cache")
    for task in tasks:
        # Serialize datetimes for JSON storage
        serializable = dict(task)
        for k in ("date_closed", "date_created", "due_date"):
            if serializable.get(k) and isinstance(serializable[k], datetime):
                serializable[k] = serializable[k].isoformat()
        conn.execute("INSERT OR REPLACE INTO task_cache (id, data) VALUES (?, ?)",
                      (task["id"], json.dumps(serializable)))
    conn.execute("INSERT OR REPLACE INTO task_cache_meta (key, value) VALUES (?, ?)",
                  ("last_updated", datetime.now().isoformat()))
    conn.commit()
    conn.close()
    logger.info(f"Task cache written: {len(tasks)} tasks to SQLite")


def _read_task_cache() -> list:
    """Read all tasks from SQLite cache. Returns empty list if no cache."""
    if not os.path.exists(TASK_CACHE_DB):
        return []
    try:
        conn = sqlite3.connect(TASK_CACHE_DB)
        rows = conn.execute("SELECT data FROM task_cache").fetchall()
        conn.close()
        tasks = []
        for (data_str,) in rows:
            task = json.loads(data_str)
            # Deserialize datetimes
            for k in ("date_closed", "date_created", "due_date"):
                if task.get(k) and isinstance(task[k], str):
                    try:
                        task[k] = datetime.fromisoformat(task[k])
                    except (ValueError, TypeError):
                        task[k] = None
            tasks.append(task)
        return tasks
    except Exception as e:
        logger.error(f"Error reading task cache: {e}")
        return []


# Initialize task cache DB on import
_init_task_cache_db()


def clickup_request_paginated(endpoint: str, result_key: str = "tasks", page_size: int = 100) -> list:
    """Make paginated requests to ClickUp API, fetching all pages.

    Args:
        endpoint: API endpoint (may already contain query params)
        result_key: Key in response that contains the list of items
        page_size: Number of items per page (ClickUp default is 100)

    Returns:
        Combined list of all items across all pages.
    """
    separator = "&" if "?" in endpoint else "?"
    all_items = []
    page = 0

    while True:
        paginated_endpoint = f"{endpoint}{separator}page={page}"
        data = clickup_request(paginated_endpoint)
        items = data.get(result_key, [])
        all_items.extend(items)

        # If we got fewer items than page_size, we've reached the last page
        if len(items) < page_size:
            break
        page += 1

    if page > 0:
        logger.info(f"Paginated fetch: {len(all_items)} total items across {page + 1} pages from {endpoint}")
    return all_items


# ============================================================================
# Daily Cache System - Refreshes at 2pm ET daily
# ============================================================================

def load_daily_cache():
    """Load daily cache from file into memory."""
    global _daily_cache, _daily_cache_loaded

    with _daily_cache_lock:
        if _daily_cache_loaded and _daily_cache:
            return _daily_cache

        try:
            if os.path.exists(DAILY_CACHE_FILE):
                with open(DAILY_CACHE_FILE, 'r') as f:
                    _daily_cache = json.load(f)
                    _daily_cache_loaded = True
                    logger.info(f"Daily cache loaded from file, last updated: {_daily_cache.get('last_updated', 'unknown')}")
                    return _daily_cache
        except Exception as e:
            logger.error(f"Error loading daily cache: {e}")

    return None


def save_daily_cache(data: dict):
    """Save daily cache to file."""
    global _daily_cache, _daily_cache_loaded

    with _daily_cache_lock:
        try:
            data['last_updated'] = datetime.now(pytz.timezone('US/Eastern')).isoformat()
            with open(DAILY_CACHE_FILE, 'w') as f:
                json.dump(data, f)
            _daily_cache = data
            _daily_cache_loaded = True
            logger.info(f"Daily cache saved at {data['last_updated']}")
        except Exception as e:
            logger.error(f"Error saving daily cache: {e}")


def refresh_daily_cache():
    """
    Refresh the daily cache with fresh data from ClickUp.
    This is called by the scheduler at 2pm ET daily.
    """
    logger.info("Starting daily cache refresh...")

    try:
        # Clear caches to force fresh API calls
        global _cache, _metrics_request_cache
        with _cache_lock:
            _cache = {}
        _metrics_request_cache = {}

        # Fetch fresh tasks from API (bypasses cache, writes to SQLite)
        tasks = _fetch_all_tasks_from_api()
        set_cached("all_tasks", tasks)

        cache_data = {
            'metrics': {},
            'velocity': {},
            'daily_averages': {},
            'team_members': None,
        }

        # Pre-compute metrics for current week and several weeks back
        # (for velocity history chart)
        for week_offset in range(0, -9, -1):  # Current week + 8 weeks history
            try:
                metrics = calculate_metrics(week_offset=week_offset, assignee_id=None)
                cache_data['metrics'][f'all_{week_offset}'] = metrics
            except Exception as e:
                logger.error(f"Error caching metrics for week {week_offset}: {e}")

        # Pre-compute velocity history
        try:
            velocity = get_velocity_history(weeks=8, assignee_id=None)
            cache_data['velocity']['all'] = velocity
        except Exception as e:
            logger.error(f"Error caching velocity: {e}")

        # Pre-compute daily averages
        try:
            daily_avg = get_daily_averages(weeks=8, assignee_id=None)
            cache_data['daily_averages']['all'] = daily_avg
        except Exception as e:
            logger.error(f"Error caching daily averages: {e}")

        # Cache team members
        try:
            cache_data['team_members'] = get_team_members()
        except Exception as e:
            logger.error(f"Error caching team members: {e}")

        # Pre-compute per-assignee data for each team member
        if cache_data['team_members']:
            for member in cache_data['team_members']:
                member_id = member['id']
                try:
                    # Current week metrics for each member
                    metrics = calculate_metrics(week_offset=0, assignee_id=member_id)
                    cache_data['metrics'][f'{member_id}_0'] = metrics

                    # Velocity for each member
                    velocity = get_velocity_history(weeks=8, assignee_id=member_id)
                    cache_data['velocity'][str(member_id)] = velocity

                    # Daily averages for each member
                    daily_avg = get_daily_averages(weeks=8, assignee_id=member_id)
                    cache_data['daily_averages'][str(member_id)] = daily_avg
                except Exception as e:
                    logger.error(f"Error caching data for member {member_id}: {e}")

        save_daily_cache(cache_data)
        logger.info("Daily cache refresh completed successfully")
        return True

    except Exception as e:
        logger.error(f"Daily cache refresh failed: {e}")
        return False


def get_from_daily_cache(data_type: str, key: str):
    """
    Get data from daily cache.
    Returns None if not found (will fall back to live data).
    """
    cache = load_daily_cache()
    if not cache:
        return None

    if data_type in cache and key in cache[data_type]:
        return cache[data_type][key]

    return None


def clickup_request(endpoint: str) -> dict:
    """Make a request to ClickUp API."""
    url = f"https://api.clickup.com/api/v2{endpoint}"
    req = urllib.request.Request(url, headers={"Authorization": CLICKUP_API_TOKEN})

    logger.info(f"ClickUp API request: {endpoint}")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())
            logger.info(f"ClickUp API success: {endpoint} - returned {len(str(data))} chars")
            return data
    except urllib.error.HTTPError as e:
        logger.error(f"ClickUp API HTTP error: {e.code} - {e.reason} for {endpoint}")
        try:
            error_body = e.read().decode()
            logger.error(f"Error response: {error_body[:500]}")
        except:
            pass
        return {}
    except urllib.error.URLError as e:
        logger.error(f"ClickUp API URL error: {e.reason} for {endpoint}")
        return {}
    except Exception as e:
        logger.error(f"ClickUp API unexpected error: {str(e)} for {endpoint}")
        return {}


def get_week_bounds(date: datetime = None, week_offset: int = 0):
    """Get Monday 00:00 and Sunday 23:59 for a given week."""
    if date is None:
        date = datetime.now()

    # Apply week offset
    date = date + timedelta(weeks=week_offset)

    # Find Monday of this week
    monday = date - timedelta(days=date.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)

    # Sunday end
    sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)

    return monday, sunday


def get_all_tasks():
    """Fetch all tasks from ClickUp with their Fibonacci scores.

    Uses a three-tier caching strategy:
    1. In-memory cache (60s TTL) — instant, for repeated calls within same request cycle
    2. SQLite persistent cache — fast, populated by daily 2pm refresh
    3. Live ClickUp API — only used during refresh, never blocks users

    Uses a two-pass approach to identify parent tasks:
    1. First pass: collect all tasks and identify parent IDs (from subtask's parent field)
    2. Second pass: parse tasks, excluding parent tasks that have subtasks
    """
    # Tier 1: in-memory cache
    cached = get_cached("all_tasks")
    if cached:
        logger.info(f"Returning {len(cached)} in-memory cached tasks")
        return cached

    # Tier 2: SQLite persistent cache
    sqlite_tasks = _read_task_cache()
    if sqlite_tasks:
        logger.info(f"Returning {len(sqlite_tasks)} tasks from SQLite cache")
        set_cached("all_tasks", sqlite_tasks)
        return sqlite_tasks

    # Tier 3: Live API fetch (only happens on first-ever load before any refresh)
    logger.info("No cache available — fetching all tasks from ClickUp API")
    tasks = _fetch_all_tasks_from_api()
    set_cached("all_tasks", tasks)
    return tasks


def _fetch_all_tasks_from_api():
    """Fetch all tasks from ClickUp API with pagination.

    Called by refresh_daily_cache and as fallback when no cache exists.
    Results are stored in SQLite for persistence.
    """
    # First pass: collect all raw tasks and identify parent task IDs
    raw_tasks = []
    parent_task_ids = set()

    spaces_response = clickup_request(f"/team/{CLICKUP_TEAM_ID}/space")
    spaces = spaces_response.get("spaces", [])
    logger.info(f"Found {len(spaces)} spaces")

    for space in spaces:
        space_id = space["id"]

        # Get folders
        folders = clickup_request(f"/space/{space_id}/folder").get("folders", [])

        for folder in folders:
            folder_name = folder["name"]
            if folder_name in EXCLUDED_FOLDERS:
                continue

            for lst in folder.get("lists", []):
                list_tasks = clickup_request_paginated(
                    f"/list/{lst['id']}/task?include_closed=true&subtasks=true"
                )

                for task in list_tasks:
                    parent_id = task.get("parent")
                    if parent_id:
                        parent_task_ids.add(parent_id)

                    raw_tasks.append({
                        "task": task,
                        "folder_name": folder_name,
                        "list_name": lst["name"]
                    })

        # Folderless lists
        folderless = clickup_request(f"/space/{space_id}/list").get("lists", [])
        for lst in folderless:
            list_tasks = clickup_request_paginated(
                f"/list/{lst['id']}/task?include_closed=true&subtasks=true"
            )

            for task in list_tasks:
                parent_id = task.get("parent")
                if parent_id:
                    parent_task_ids.add(parent_id)

                raw_tasks.append({
                    "task": task,
                    "folder_name": "(No Folder)",
                    "list_name": lst["name"]
                })

    logger.info(f"Found {len(parent_task_ids)} parent tasks with subtasks (will be excluded)")

    # Second pass: parse tasks, excluding parent tasks
    tasks = []
    for item in raw_tasks:
        task_data = parse_task(item["task"], item["folder_name"], item["list_name"], parent_task_ids)
        if task_data:
            tasks.append(task_data)

    logger.info(f"Fetched {len(tasks)} tasks from API (after excluding parent tasks)")

    # Persist to SQLite
    _write_task_cache(tasks)

    return tasks


def parse_task(task: dict, folder_name: str, list_name: str, parent_task_ids: set) -> dict:
    """Parse a task and extract relevant fields.

    Returns None for parent tasks with subtasks (to avoid double-counting).
    Only standalone tasks and subtasks are included in metrics.

    Args:
        task: The task dict from ClickUp API
        folder_name: The folder name
        list_name: The list name
        parent_task_ids: Set of task IDs that are known to have subtasks
    """
    # Skip parent tasks that have subtasks (to avoid double-counting)
    # The subtasks themselves will be counted individually
    if task.get("id") in parent_task_ids:
        return None

    # Orderindex to Fibonacci score mapping (ClickUp returns orderindex as value)
    ORDERINDEX_TO_SCORE = {0: 1, 1: 2, 2: 3, 3: 5, 4: 8, 5: 13}

    # Get Fibonacci score
    score = None
    for cf in task.get("custom_fields", []):
        if cf.get("id") == FIBONACCI_FIELD_ID and cf.get("value") is not None:
            value = cf["value"]
            # Handle both UUID string format and integer orderindex format
            if isinstance(value, str):
                score = SCORE_OPTIONS.get(value)
            elif isinstance(value, int):
                score = ORDERINDEX_TO_SCORE.get(value)
            break

    # Get status info
    status = task.get("status", {})
    is_complete = status.get("type") == "closed"

    # Get dates
    date_closed = None
    if task.get("date_closed"):
        date_closed = datetime.fromtimestamp(int(task["date_closed"]) / 1000)

    date_created = None
    if task.get("date_created"):
        date_created = datetime.fromtimestamp(int(task["date_created"]) / 1000)

    due_date = None
    if task.get("due_date"):
        due_date = datetime.fromtimestamp(int(task["due_date"]) / 1000)

    # Get assignees
    assignees = []
    for assignee in task.get("assignees", []):
        assignees.append({
            "id": assignee.get("id"),
            "username": assignee.get("username"),
        })

    # Get time spent (manual time logging on task)
    time_spent_ms = task.get("time_spent") or 0

    return {
        "id": task["id"],
        "name": task["name"],
        "folder": folder_name,
        "list": list_name,
        "score": score,
        "status": status.get("status", ""),
        "is_complete": is_complete,
        "date_created": date_created,
        "date_closed": date_closed,
        "due_date": due_date,
        "assignees": assignees,
        "url": task.get("url", ""),
        "time_spent_ms": time_spent_ms,
    }


def get_time_entries(start_ts: int, end_ts: int, assignee_id: int = None):
    """Fetch time entries for a date range (cached)."""
    cache_key = f"time_entries_{start_ts}_{end_ts}_{assignee_id}"
    cached = get_cached(cache_key)
    if cached:
        return cached

    url = f"/team/{CLICKUP_TEAM_ID}/time_entries?start_date={start_ts}&end_date={end_ts}"
    if assignee_id:
        url += f"&assignee={assignee_id}"

    data = clickup_request(url)
    result = data.get("data", [])
    set_cached(cache_key, result)
    return result


def get_team_members():
    """Get all team members from ClickUp workspace."""
    cached = get_cached("team_members")
    if cached:
        return cached

    # Use the team endpoint to get all workspace members
    team_data = clickup_request(f"/team/{CLICKUP_TEAM_ID}")
    members = []

    if team_data.get("team"):
        for member in team_data["team"].get("members", []):
            user = member.get("user", {})
            if user.get("id"):
                members.append({
                    "id": user["id"],
                    "username": user.get("username") or user.get("email", "Unknown"),
                    "email": user.get("email", ""),
                    "initials": user.get("initials", ""),
                })

    set_cached("team_members", members, ttl=300)  # Cache for 5 minutes
    return members


def calculate_metrics(week_offset: int = 0, assignee_id: int = None):
    """Calculate all dashboard metrics for a given week.

    Results are cached per (week_offset, assignee_id) within the same request
    cycle to avoid redundant computation (e.g., velocity + daily averages
    both call this for the same weeks).
    """
    cache_key = (week_offset, assignee_id)
    if cache_key in _metrics_request_cache:
        return _metrics_request_cache[cache_key]

    result = _calculate_metrics_impl(week_offset, assignee_id)
    _metrics_request_cache[cache_key] = result
    return result


def _calculate_metrics_impl(week_offset: int = 0, assignee_id: int = None):
    """Internal implementation of calculate_metrics."""
    monday, sunday = get_week_bounds(week_offset=week_offset)
    next_monday, next_sunday = get_week_bounds(week_offset=week_offset + 1)

    # Get all tasks
    all_tasks = get_all_tasks()

    # Filter by assignee if specified
    if assignee_id:
        all_tasks = [
            t for t in all_tasks
            if any(a["id"] == assignee_id for a in t["assignees"])
        ]

    # Tasks completed this week
    completed_this_week = [
        t for t in all_tasks
        if t["is_complete"] and t["date_closed"]
        and monday <= t["date_closed"] <= sunday
    ]

    # Tasks planned for next week (backlog status, not complete)
    tasks_next_week = [
        t for t in all_tasks
        if not t["is_complete"] and t["status"].lower() == "backlog"
    ]

    # Tasks currently in progress (not complete, not backlog/to do)
    # Include: in progress, in review, waiting response, doing, active, working
    active_statuses = ["in progress", "in review", "waiting response", "doing", "active", "working"]
    tasks_in_progress = [
        t for t in all_tasks
        if not t["is_complete"] and t["status"].lower() in active_statuses
    ]

    # Calculate points
    points_completed = sum(t["score"] or 0 for t in completed_this_week)
    points_next_week = sum(t["score"] or 0 for t in tasks_next_week)
    points_in_progress = sum(t["score"] or 0 for t in tasks_in_progress)
    tasks_completed_count = len(completed_this_week)
    tasks_in_progress_count = len(tasks_in_progress)

    # Get time from tasks' time_spent field (manual time logging)
    # This is more reliable than time_entries API which only captures timer-based entries
    total_time_ms = sum(t.get("time_spent_ms", 0) for t in completed_this_week)
    total_time_hours = total_time_ms / (1000 * 60 * 60)

    # Calculate average time per score using time_spent_ms from tasks
    time_per_score = defaultdict(list)
    for task in completed_this_week:
        if task["score"] and task.get("time_spent_ms", 0) > 0:
            hours = task["time_spent_ms"] / (1000 * 60 * 60)
            time_per_score[task["score"]].append(hours)

    # Average and efficiency by score
    # Also track tasks without time tracking for context
    score_metrics = {}
    for score in [1, 2, 3, 5, 8, 13]:
        times = time_per_score.get(score, [])
        avg_hours = sum(times) / len(times) if times else None
        expected = EXPECTED_HOURS[score]

        # Count completed tasks at this score (with or without time tracking)
        total_at_score = sum(1 for t in completed_this_week if t["score"] == score)
        tasks_with_time = len(times)

        efficiency = None
        efficiency_status = "no_data"
        if avg_hours is not None:
            efficiency = avg_hours / expected["mid"]
            if avg_hours < expected["min"]:
                efficiency_status = "exceeding"  # Faster than expected
            elif avg_hours <= expected["max"]:
                efficiency_status = "on_track"  # Within expected range
            else:
                efficiency_status = "over"  # Taking longer than expected

        score_metrics[score] = {
            "expected_min": expected["min"],
            "expected_max": expected["max"],
            "expected_mid": expected["mid"],
            "actual_avg": round(avg_hours, 2) if avg_hours else None,
            "task_count": tasks_with_time,
            "total_completed": total_at_score,
            "efficiency": round(efficiency, 2) if efficiency else None,
            "status": efficiency_status,
        }

    # Score distribution of completed tasks
    score_distribution = defaultdict(int)
    for task in completed_this_week:
        if task["score"]:
            score_distribution[task["score"]] += 1

    # Daily breakdown (points completed per day)
    daily_breakdown = {}
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for i in range(7):
        day_date = monday + timedelta(days=i)
        daily_breakdown[day_names[i]] = {
            "date": day_date.strftime("%Y-%m-%d"),
            "points": 0,
            "tasks": 0,
        }
    for task in completed_this_week:
        if task["date_closed"]:
            day_idx = task["date_closed"].weekday()
            day_name = day_names[day_idx]
            daily_breakdown[day_name]["points"] += task["score"] or 0
            daily_breakdown[day_name]["tasks"] += 1

    # Assignee breakdown (points by person, excluding non-Pulse employees)
    assignee_breakdown = defaultdict(lambda: {"points": 0, "tasks": 0, "username": ""})
    for task in completed_this_week:
        for assignee in task["assignees"]:
            # Skip excluded assignees
            if assignee["username"] in load_excluded_assignees():
                continue
            aid = assignee["id"]
            assignee_breakdown[aid]["username"] = assignee["username"]
            assignee_breakdown[aid]["points"] += task["score"] or 0
            assignee_breakdown[aid]["tasks"] += 1
    # Convert to list sorted by points
    assignee_list = [
        {"id": k, **v} for k, v in assignee_breakdown.items()
    ]
    assignee_list.sort(key=lambda x: x["points"], reverse=True)

    # Underestimated tasks (actual time > expected max for their score)
    underestimated_tasks = []
    for task in completed_this_week:
        if task["score"] and task.get("time_spent_ms", 0) > 0:
            actual_hours = task["time_spent_ms"] / (1000 * 60 * 60)
            expected_max = EXPECTED_HOURS[task["score"]]["max"]
            if actual_hours > expected_max:
                underestimated_tasks.append({
                    "id": task["id"],
                    "name": task["name"],
                    "score": task["score"],
                    "actual_hours": round(actual_hours, 1),
                    "expected_max": expected_max,
                    "overage": round(actual_hours - expected_max, 1),
                    "url": task["url"],
                })
    # Sort by overage (worst first)
    underestimated_tasks.sort(key=lambda x: x["overage"], reverse=True)

    # Format task details for the modal popups
    def format_task_for_modal(task, include_assignees=False):
        hours = round(task.get("time_spent_ms", 0) / (1000 * 60 * 60), 1)
        result = {
            "id": task["id"],
            "name": task["name"],
            "score": task["score"],
            "status": task["status"],
            "hours": hours,
            "url": task["url"],
        }
        if include_assignees:
            # Filter out excluded assignees from the list
            result["assignees"] = [
                a["username"] for a in task["assignees"]
                if a["username"] not in load_excluded_assignees()
            ]
        return result

    completed_tasks_detail = [format_task_for_modal(t, include_assignees=True) for t in completed_this_week]
    in_progress_tasks_detail = [format_task_for_modal(t, include_assignees=True) for t in tasks_in_progress]

    return {
        "week": {
            "start": monday.strftime("%Y-%m-%d"),
            "end": sunday.strftime("%Y-%m-%d"),
            "label": f"{monday.strftime('%b %d')} - {sunday.strftime('%b %d, %Y')}",
        },
        "summary": {
            "points_completed": points_completed,
            "points_in_progress": points_in_progress,
            "points_next_week": points_next_week,
            "tasks_completed": tasks_completed_count,
            "tasks_in_progress": tasks_in_progress_count,
            "total_time_hours": round(total_time_hours, 1),
        },
        "score_metrics": score_metrics,
        "score_distribution": dict(score_distribution),
        "daily_breakdown": daily_breakdown,
        "assignee_breakdown": assignee_list,
        "underestimated_tasks": underestimated_tasks[:10],  # Top 10 worst
        "expected_hours_reference": EXPECTED_HOURS,
        # Task details for modal popups
        "tasks": {
            "completed": completed_tasks_detail,
            "in_progress": in_progress_tasks_detail,
        },
    }


def get_velocity_history(weeks: int = 8, assignee_id: int = None):
    """Get velocity data for the last N weeks.

    Note: Team Capacity and 10x Goal lines are now calculated on the frontend
    based on the configurable team hours, not historical averages.
    """
    history = []

    for offset in range(0, -weeks, -1):
        metrics = calculate_metrics(week_offset=offset, assignee_id=assignee_id)
        history.append({
            "week": metrics["week"]["label"],
            "week_start": metrics["week"]["start"],
            "points": metrics["summary"]["points_completed"],
            "tasks": metrics["summary"]["tasks_completed"],
            "hours": metrics["summary"]["total_time_hours"],
        })

    history = list(reversed(history))

    return {
        "history": history,
    }


def get_daily_averages(weeks: int = 8, assignee_id: int = None):
    """Calculate average points completed per day of week across historical data."""
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_totals = {day: [] for day in day_names}

    for offset in range(0, -weeks, -1):
        metrics = calculate_metrics(week_offset=offset, assignee_id=assignee_id)
        daily = metrics["daily_breakdown"]
        for day in day_names:
            if daily[day]["points"] > 0:
                day_totals[day].append(daily[day]["points"])

    # Calculate averages
    averages = {}
    for day in day_names:
        values = day_totals[day]
        averages[day] = round(sum(values) / len(values), 1) if values else 0

    return averages


def generate_ai_insights(metrics: dict, velocity_history: list) -> str:
    """Generate AI insights using Claude API."""
    if not ANTHROPIC_API_KEY:
        return "AI insights require an Anthropic API key to be configured."

    history = velocity_history.get("history", velocity_history) if isinstance(velocity_history, dict) else velocity_history

    # Week-over-week comparison
    this_week = history[-1] if history else {}
    last_week = history[-2] if len(history) >= 2 else {}
    wow_section = ""
    if this_week and last_week:
        def _delta(cur, prev, key):
            c, p = cur.get(key, 0), prev.get(key, 0)
            diff = c - p
            pct = round(diff / p * 100) if p else 0
            return c, p, diff, pct
        pts_c, pts_p, pts_d, pts_pct = _delta(this_week, last_week, 'points')
        tsk_c, tsk_p, tsk_d, _ = _delta(this_week, last_week, 'tasks')
        hrs_c, hrs_p, hrs_d, _ = _delta(this_week, last_week, 'hours')
        wow_section = f"""
## Week-over-Week Comparison
- Points: {pts_c} this week vs {pts_p} last week ({'+' if pts_d >= 0 else ''}{pts_d}, {'+' if pts_pct >= 0 else ''}{pts_pct}%)
- Tasks: {tsk_c} vs {tsk_p} ({'+' if tsk_d >= 0 else ''}{tsk_d})
- Hours tracked: {hrs_c} vs {hrs_p} ({'+' if hrs_d >= 0 else ''}{hrs_d})
"""

    # Per-contributor breakdown
    contributor_section = ""
    assignee_list = metrics.get('assignee_breakdown', [])
    if assignee_list:
        contributor_section = "\n## Per-Contributor Breakdown\n"
        for a in assignee_list:
            contributor_section += f"- {a['username']}: {a['points']} pts across {a['tasks']} tasks\n"

    # Underestimated tasks
    underest_section = ""
    underestimated = metrics.get('underestimated_tasks', [])
    if underestimated:
        underest_section = "\n## Underestimated Tasks (actual > expected max)\n"
        for t in underestimated[:5]:
            underest_section += f"- \"{t['name']}\" ({t['score']}pt): took {t['actual_hours']}hrs, expected max {t['expected_max']}hrs (+{t['overage']}hrs over)\n"

    # Team capacity
    capacity_section = ""
    try:
        capacity = build_team_capacity()
        if capacity:
            total_hrs = sum(capacity.values())
            expected_pts = calculate_expected_points_from_hours(total_hrs)
            capacity_section = f"\n## Team Capacity\nTotal weekly hours: {total_hrs}hrs across {len(capacity)} members → expected ~{expected_pts} pts/week\n"
            for name, hrs in sorted(capacity.items()):
                capacity_section += f"- {name}: {hrs}hrs/week\n"
    except Exception:
        pass

    prompt = f"""Analyze this Agile sprint data for a small agency engineering team. Provide 3-4 specific, actionable insights.

## This Week's Metrics ({metrics['week']['label']})
- Points Completed: {metrics['summary']['points_completed']}
- Tasks Completed: {metrics['summary']['tasks_completed']}
- Hours Tracked: {metrics['summary']['total_time_hours']}
- In Progress: {metrics['summary']['points_in_progress']} pts ({metrics['summary']['tasks_in_progress']} tasks)
{wow_section}
## Time vs Estimate by Score
"""
    for score in [1, 2, 3, 5, 8, 13]:
        m = metrics['score_metrics'][score]
        if m['actual_avg']:
            prompt += f"- {score}pt: expected {m['expected_min']}-{m['expected_max']}hrs, actual avg {m['actual_avg']}hrs ({m['task_count']} tasks, {m['status']})\n"
        elif m['total_completed']:
            prompt += f"- {score}pt: {m['total_completed']} completed but no time tracked\n"

    prompt += contributor_section + underest_section + capacity_section
    prompt += f"""
## Velocity Trend (Last 8 Weeks)
"""
    for week in history[-8:]:
        prompt += f"- {week['week']}: {week['points']} pts, {week['tasks']} tasks, {week['hours']}hrs\n"

    prompt += """
Be specific — name people, tasks, and numbers. Provide:
1. Week-over-week performance analysis (improving/declining and why)
2. Contributor highlights (who delivered most, who may need support)
3. Estimation accuracy (call out specific underestimated tasks and patterns)
4. One concrete, specific recommendation (not generic advice like "improve communication")

Keep each insight to 2-3 sentences max. Reference actual data points."""

    request_body = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 500,
        "messages": [{"role": "user", "content": prompt}]
    }

    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01"
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(request_body).encode(),
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            return data["content"][0]["text"]
    except Exception as e:
        return f"Could not generate insights: {str(e)}"


# =============================================================================
# Routes
# =============================================================================

@app.route("/health")
def health():
    """Health check endpoint - tests ClickUp API connection."""
    logger.info("Health check started")
    logger.info(f"CLICKUP_API_TOKEN: {'set (' + CLICKUP_API_TOKEN[:10] + '...)' if CLICKUP_API_TOKEN else 'NOT SET'}")
    logger.info(f"CLICKUP_TEAM_ID: {CLICKUP_TEAM_ID}")
    logger.info(f"FIBONACCI_FIELD_ID: {FIBONACCI_FIELD_ID}")

    # Test ClickUp API connection
    result = {
        "status": "ok",
        "config": {
            "clickup_token_set": bool(CLICKUP_API_TOKEN),
            "clickup_token_prefix": CLICKUP_API_TOKEN[:10] + "..." if CLICKUP_API_TOKEN else None,
            "team_id": CLICKUP_TEAM_ID,
            "fibonacci_field_id": FIBONACCI_FIELD_ID,
        },
        "api_test": None,
    }

    try:
        # Test API with a simple call
        spaces = clickup_request(f"/team/{CLICKUP_TEAM_ID}/space")
        if spaces.get("spaces"):
            result["api_test"] = {
                "success": True,
                "spaces_count": len(spaces["spaces"]),
                "space_names": [s["name"] for s in spaces["spaces"][:3]],
            }
        else:
            result["api_test"] = {
                "success": False,
                "error": "No spaces returned - check API token and team ID",
                "raw_response": str(spaces)[:200],
            }
            result["status"] = "error"
    except Exception as e:
        result["api_test"] = {
            "success": False,
            "error": str(e),
        }
        result["status"] = "error"

    logger.info(f"Health check result: {result}")
    return jsonify(result)


@app.route("/health/integrations")
@login_required
def health_integrations():
    """Check which integrations are configured and working."""
    import os
    checks = {}

    # Grain
    grain_token = os.environ.get("GRAIN_API_TOKEN") or os.environ.get("GRAIN_API_KEY")
    if not grain_token:
        try:
            from client_health import load_grain_api_key
            grain_token = load_grain_api_key()
        except Exception:
            pass
    checks["grain"] = {
        "configured": bool(grain_token),
        "token_prefix": grain_token[:15] + "..." if grain_token else None,
    }

    # Google Service Account
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    sa_file = os.path.exists(os.path.join(os.path.dirname(__file__), ".env.google-service-account.json"))
    sa_email = None
    sa_parse_error = None
    if sa_json:
        try:
            sa_email = json.loads(sa_json).get("client_email")
        except json.JSONDecodeError:
            try:
                # Render may inject real newlines — escape them back
                fixed = sa_json.replace("\n", "\\n").replace("\r", "")
                sa_email = json.loads(fixed).get("client_email")
                sa_parse_error = "fixed_with_newline_escape"
            except Exception as e:
                sa_parse_error = str(e)
    checks["google_service_account"] = {
        "env_var_set": bool(sa_json),
        "env_var_length": len(sa_json) if sa_json else 0,
        "file_exists": sa_file,
        "client_email": sa_email,
        "parse_error": sa_parse_error,
    }

    # Anthropic
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    checks["anthropic"] = {
        "configured": bool(anthropic_key),
        "key_prefix": anthropic_key[:10] + "..." if anthropic_key else None,
    }

    # ClickUp
    checks["clickup"] = {
        "configured": bool(CLICKUP_API_TOKEN),
        "token_prefix": CLICKUP_API_TOKEN[:10] + "..." if CLICKUP_API_TOKEN else None,
    }

    return jsonify({"integrations": checks})


# Note: /login and /logout are now handled by the auth blueprint


@app.route("/")
@login_required
def dashboard():
    """Serve the dashboard page."""
    return render_template("dashboard.html")


@app.route("/api/metrics")
@login_required
def api_metrics():
    """Get metrics for a specific week."""
    logger.info("API metrics endpoint called")
    try:
        week_offset = int(request.args.get("week_offset", 0))
        assignee_id = request.args.get("assignee_id")
        if assignee_id:
            assignee_id = int(assignee_id)

        # Try daily cache first for common queries
        cache_key = f"{assignee_id or 'all'}_{week_offset}"
        cached = get_from_daily_cache('metrics', cache_key)
        if cached:
            logger.info(f"Returning cached metrics for {cache_key}")
            return jsonify(cached)

        logger.info(f"Calculating metrics for week_offset={week_offset}, assignee_id={assignee_id}")
        metrics = calculate_metrics(week_offset=week_offset, assignee_id=assignee_id)
        logger.info(f"Metrics calculated: points_completed={metrics['summary']['points_completed']}, tasks_completed={metrics['summary']['tasks_completed']}")
        return jsonify(metrics)
    except Exception as e:
        logger.error(f"Error in api_metrics: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/velocity")
@login_required
def api_velocity():
    """Get velocity history with baseline."""
    weeks = int(request.args.get("weeks", 8))
    assignee_id = request.args.get("assignee_id")
    if assignee_id:
        assignee_id = int(assignee_id)

    # Try daily cache first
    cache_key = str(assignee_id) if assignee_id else 'all'
    cached = get_from_daily_cache('velocity', cache_key)
    if cached:
        logger.info(f"Returning cached velocity for {cache_key}")
        return jsonify(cached)

    data = get_velocity_history(weeks=weeks, assignee_id=assignee_id)
    return jsonify(data)


@app.route("/api/daily-averages")
@login_required
def api_daily_averages():
    """Get average points per day of week."""
    weeks = int(request.args.get("weeks", 8))
    assignee_id = request.args.get("assignee_id")
    if assignee_id:
        assignee_id = int(assignee_id)

    # Try daily cache first
    cache_key = str(assignee_id) if assignee_id else 'all'
    cached = get_from_daily_cache('daily_averages', cache_key)
    if cached:
        logger.info(f"Returning cached daily averages for {cache_key}")
        return jsonify(cached)

    averages = get_daily_averages(weeks=weeks, assignee_id=assignee_id)
    return jsonify(averages)


@app.route("/api/team")
@login_required
def api_team():
    """Get team members (filtered to Pulse employees only)."""
    # Return only Pulse employees (@pulsemarketing.co) for the dropdown filter
    members = get_pulse_team_members()
    return jsonify(members)


@app.route("/api/insights")
@login_required
def api_insights():
    """Get AI-generated insights."""
    week_offset = int(request.args.get("week_offset", 0))
    assignee_id = request.args.get("assignee_id")
    if assignee_id:
        assignee_id = int(assignee_id)

    metrics = calculate_metrics(week_offset=week_offset, assignee_id=assignee_id)
    velocity = get_velocity_history(weeks=8, assignee_id=assignee_id)
    insights = generate_ai_insights(metrics, velocity)

    return jsonify({"insights": insights})


@app.route("/api/team-capacity")
@login_required
def api_team_capacity():
    """Get team capacity (merged from ClickUp + saved config)."""
    capacity = build_team_capacity()
    return jsonify(capacity)


@app.route("/api/team-capacity", methods=["POST"])
@admin_required
def api_save_team_capacity():
    """Save team capacity configuration (admin only)."""
    try:
        capacity = request.get_json()
        if not capacity or not isinstance(capacity, dict):
            return jsonify({"error": "Invalid capacity data"}), 400

        success = save_capacity_config(capacity)
        if success:
            return jsonify({"status": "success", "message": "Capacity saved"})
        return jsonify({"error": "Failed to save capacity"}), 500
    except Exception as e:
        logger.error(f"Error saving capacity: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/excluded-assignees")
@login_required
def api_get_excluded_assignees():
    """Get the list of excluded assignees."""
    return jsonify(load_excluded_assignees())


@app.route("/api/excluded-assignees", methods=["POST"])
@admin_required
def api_save_excluded_assignees():
    """Save the list of excluded assignees (admin only)."""
    try:
        names = request.get_json()
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            return jsonify({"error": "Expected a JSON array of name strings"}), 400
        # Strip whitespace and remove empties
        names = [n.strip() for n in names if n.strip()]
        success = save_excluded_assignees(names)
        if success:
            return jsonify({"status": "success", "excluded_assignees": names})
        return jsonify({"error": "Failed to save"}), 500
    except Exception as e:
        logger.error(f"Error saving excluded assignees: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/pulse-team")
@login_required
def api_pulse_team():
    """Get current Pulse team members from ClickUp (filtered by @pulsemarketing.co)."""
    members = get_pulse_team_members()
    return jsonify(members)


@app.route("/api/cache-status")
@login_required
def api_cache_status():
    """Get the status of the daily cache."""
    cache = load_daily_cache()
    if cache:
        return jsonify({
            "status": "loaded",
            "last_updated": cache.get("last_updated", "unknown"),
            "has_metrics": bool(cache.get("metrics")),
            "has_velocity": bool(cache.get("velocity")),
            "has_team_members": bool(cache.get("team_members")),
        })
    return jsonify({"status": "no_cache"})


@app.route("/api/refresh-cache", methods=["POST"])
@admin_required
def api_refresh_cache():
    """Manually trigger a cache refresh (admin only)."""
    logger.info("Manual cache refresh triggered")
    success = refresh_daily_cache()
    if success:
        return jsonify({"status": "success", "message": "Cache refreshed successfully"})
    return jsonify({"status": "error", "message": "Cache refresh failed"}), 500


# ============================================================================
# Client Health Routes
# ============================================================================

def refresh_client_health_cache():
    """Rebuild the client health SQLite cache (called by scheduler or manually)."""
    logger.info("Starting client health cache refresh...")
    try:
        data = build_client_health_data(clickup_request)
        # Apply sentiment overrides before caching
        apply_overrides(data)
        write_cache(data)
        logger.info("Client health cache refresh completed")
        return True
    except Exception as e:
        logger.error(f"Client health cache refresh failed: {e}", exc_info=True)
        return False


@app.route("/client-health")
@login_required
def client_health():
    """Serve the client health dashboard page."""
    return render_template("client_health.html")


@app.route("/api/client-health")
@login_required
def api_client_health():
    """Get client health data — always served from SQLite cache. Never makes user wait."""
    try:
        data = read_cache()
        if data is None:
            return jsonify({
                "status": "loading",
                "message": "Building client health data... this takes a few minutes on first load.",
                "clients": [],
                "summary": {"total": 0, "red": 0, "yellow": 0, "green": 0},
                "last_updated": None
            })
        # Apply live overrides on top of cached data (so overrides appear immediately)
        apply_overrides(data)
        return jsonify(data)
    except Exception as e:
        logger.error(f"Error in api_client_health: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/client-health/refresh", methods=["POST"])
@admin_required
def api_client_health_refresh():
    """Manually trigger a client health cache rebuild (admin only)."""
    import threading
    t = threading.Thread(target=refresh_client_health_cache, daemon=True)
    t.start()
    return jsonify({"status": "started", "message": "Refresh started in background. Reload in ~60s."})


@app.route("/api/client-health/sentiment-override", methods=["POST"])
@login_required
def api_sentiment_override_post():
    """Save a manual sentiment override for a client."""
    try:
        body = request.get_json()
        if not body or not body.get("client") or not body.get("rating"):
            return jsonify({"error": "client and rating are required"}), 400
        rating = body["rating"].lower()
        if rating not in ("positive", "neutral", "concerned", "negative"):
            return jsonify({"error": "rating must be positive/neutral/concerned/negative"}), 400
        save_override(body["client"], rating, body.get("reason", ""), body.get("overridden_by", "User"))
        return jsonify({"status": "saved"})
    except Exception as e:
        logger.error(f"Error saving sentiment override: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/client-health/sentiment-override", methods=["DELETE"])
@login_required
def api_sentiment_override_delete():
    """Remove a manual sentiment override for a client."""
    client = request.args.get("client")
    if not client:
        return jsonify({"error": "client parameter required"}), 400
    delete_override(client)
    return jsonify({"status": "deleted"})


@app.route("/api/client-health/sentiment-overrides")
@login_required
def api_sentiment_overrides_list():
    """Get all current sentiment overrides."""
    return jsonify(load_overrides())


# ============================================================================
# Client Mapping Routes
# ============================================================================

_grain_cache = {"data": None, "ts": 0}

def _get_cached_grain_recordings(ttl=600):
    """Return Grain recordings with a 10-min in-memory cache."""
    import time
    now = time.time()
    with _grain_cache_lock:
        if _grain_cache["data"] is not None and (now - _grain_cache["ts"]) < ttl:
            return _grain_cache["data"]
    try:
        recs = fetch_grain_recordings()
        with _grain_cache_lock:
            _grain_cache["data"] = recs
            _grain_cache["ts"] = now
        return recs
    except Exception as e:
        logger.error(f"Grain fetch failed: {e}")
        with _grain_cache_lock:
            return _grain_cache["data"] or []


@app.route("/client-mapping")
@admin_required
def client_mapping():
    """Serve the client mapping management page (admin only)."""
    return render_template("client_mapping.html")


@app.route("/api/client-mapping")
@admin_required
def api_client_mapping():
    """Get all client mappings, unmatched recordings, and overview data (admin only)."""
    try:
        mappings = load_mappings()
        accounts_data = fetch_active_accounts(clickup_request)
        clients = accounts_data["clients"]
        managers = accounts_data["managers"]

        # Fetch Grain recordings (cached for 10 min to avoid repeated slow API calls)
        recordings = _get_cached_grain_recordings()
        grain_matches = mappings.get("grain_matches", {})

        # Separate matched vs unmatched
        unmatched = []
        matched = []
        for rec in recordings:
            rec_id = rec.get("id") or rec.get("recording_id") or ""
            title = rec.get("title") or rec.get("name") or "Untitled"
            date = rec.get("start_datetime") or rec.get("date") or rec.get("created_at") or rec.get("start_time") or ""
            url = rec.get("url") or rec.get("public_url") or ""
            rec_info = {"id": rec_id, "title": title, "date": date, "url": url}

            if rec_id in grain_matches:
                if grain_matches[rec_id] == "_hidden":
                    continue  # Skip hidden recordings entirely
                rec_info["matched_client"] = grain_matches[rec_id]
                matched.append(rec_info)
            else:
                # Check auto-match
                auto = match_client_to_recording(rec, clients)
                if auto:
                    rec_info["matched_client"] = auto
                    matched.append(rec_info)
                else:
                    unmatched.append(rec_info)

        # Build overview
        email_domains = mappings.get("email_domains", {})
        # Count matched calls per client
        call_counts = {}
        for rec in matched:
            c = rec.get("matched_client", "")
            call_counts[c] = call_counts.get(c, 0) + 1

        overview = []
        for client in clients:
            entry = email_domains.get(client, {})
            overview.append({
                "client": client,
                "account_manager": managers.get(client, ""),
                "email_domains": entry.get("domains", []),
                "matched_calls": call_counts.get(client, 0),
            })

        return jsonify({
            "clients": clients,
            "email_domains": email_domains,
            "grain_matches": grain_matches,
            "unmatched_recordings": unmatched[:50],
            "matched_recordings": matched[:50],
            "overview": overview,
        })
    except Exception as e:
        logger.error(f"Error in api_client_mapping: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/client-mapping/email", methods=["POST"])
@admin_required
def api_client_mapping_email():
    """Save email domain mapping for a client (admin only)."""
    try:
        body = request.get_json()
        client = body.get("client")
        domains = body.get("domains", [])
        keywords = body.get("keywords", [])
        if not client:
            return jsonify({"error": "client is required"}), 400
        save_email_mapping(client, domains, keywords)
        return jsonify({"status": "saved"})
    except Exception as e:
        logger.error(f"Error saving email mapping: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/client-mapping/grain", methods=["POST"])
@admin_required
def api_client_mapping_grain():
    """Save Grain recording -> client match (admin only)."""
    try:
        body = request.get_json()
        recording_id = body.get("recording_id")
        client = body.get("client")
        if not recording_id or not client:
            return jsonify({"error": "recording_id and client are required"}), 400
        save_grain_match(recording_id, client)
        return jsonify({"status": "saved"})
    except Exception as e:
        logger.error(f"Error saving grain match: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/client-mapping/unmatched-recordings")
@admin_required
def api_unmatched_recordings():
    """Get Grain recordings not yet matched to a client (admin only)."""
    try:
        mappings = load_mappings()
        accounts_data = fetch_active_accounts(clickup_request)
        clients = accounts_data["clients"]
        recordings = fetch_grain_recordings()
        grain_matches = mappings.get("grain_matches", {})

        unmatched = []
        for rec in recordings:
            rec_id = rec.get("id") or rec.get("recording_id") or ""
            if rec_id in grain_matches:
                continue
            auto = match_client_to_recording(rec, clients)
            if auto:
                continue
            unmatched.append({
                "id": rec_id,
                "title": rec.get("title") or rec.get("name") or "Untitled",
                "date": rec.get("start_datetime") or rec.get("date") or rec.get("created_at") or "",
            })

        return jsonify({"unmatched": unmatched[:50]})
    except Exception as e:
        logger.error(f"Error fetching unmatched recordings: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================================
# One-Time Setup Route (for initial admin creation on Render)
# ============================================================================
# User Sync Routes (Admin Only)
# ============================================================================

@app.route("/api/admin/sync-users", methods=["POST"])
@admin_required
def api_admin_sync_users():
    """Trigger user sync from ClickUp (admin only)."""
    try:
        data = request.get_json() or {}
        send_invites = data.get("send_invites", False)  # Default to NOT sending invites

        logger.info(f"Starting user sync (send_invites={send_invites})")

        pulse_members = get_pulse_team_members()
        logger.info(f"Found {len(pulse_members)} Pulse team members from ClickUp")

        if not pulse_members:
            return jsonify({
                "status": "warning",
                "message": "No @pulsemarketing.co users found in ClickUp",
                "created": 0,
                "updated": 0,
                "deactivated": 0,
                "errors": [],
            })

        results = sync_users_from_clickup(pulse_members, send_invites=send_invites)
        logger.info(f"Sync complete: {results['created']} created, {results['updated']} updated")

        return jsonify({
            "status": "success",
            "created": results["created"],
            "updated": results["updated"],
            "deactivated": results["deactivated"],
            "errors": results["errors"],
            "invites_sent": send_invites,
        })
    except Exception as e:
        logger.error(f"Error syncing users: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500



# ============================================================================
# Team Performance Routes (Admin Only)
# ============================================================================

@app.route("/team-performance")
@admin_required
def team_performance():
    """Serve the team performance dashboard page."""
    return render_template("team_performance.html")


@app.route("/api/team-performance")
@admin_required
def api_team_performance():
    """Get performance data for all Pulse team members."""
    try:
        pulse_members = get_pulse_team_members()
        excluded = load_excluded_assignees()
        all_tasks = get_all_tasks()
        capacity = build_team_capacity()

        # Filter out excluded members
        pulse_members = [m for m in pulse_members if m["username"] not in excluded]

        now = datetime.now()
        results = []

        for member in pulse_members:
            mid = member["id"]
            name = member["username"]

            # Filter tasks for this member
            member_tasks = [t for t in all_tasks if any(a["id"] == mid for a in t["assignees"])]

            # --- 8-week velocity + tasks-per-week ---
            weekly_points = []
            weekly_tasks = []
            for offset in range(0, -8, -1):
                mon, sun = get_week_bounds(week_offset=offset)
                completed = [t for t in member_tasks if t["is_complete"] and t["date_closed"] and mon <= t["date_closed"] <= sun]
                pts = sum(t["score"] or 0 for t in completed)
                weekly_points.append({"week": mon.strftime("%b %d"), "points": pts})
                weekly_tasks.append({"week": mon.strftime("%b %d"), "tasks": len(completed)})
            weekly_points.reverse()
            weekly_tasks.reverse()

            # --- On-time vs overdue ---
            completed_all = [t for t in member_tasks if t["is_complete"] and t["date_closed"]]
            on_time = 0
            overdue = 0
            no_due = 0
            for t in completed_all:
                if t["due_date"]:
                    if t["date_closed"] <= t["due_date"]:
                        on_time += 1
                    else:
                        overdue += 1
                else:
                    no_due += 1

            # --- Average time-to-close ---
            close_times = []
            for t in completed_all:
                if t["date_created"] and t["date_closed"]:
                    delta = (t["date_closed"] - t["date_created"]).total_seconds() / 86400
                    close_times.append(delta)
            avg_close_days = round(sum(close_times) / len(close_times), 1) if close_times else None

            # --- Workload distribution ---
            active_statuses = ["in progress", "in review", "waiting response", "doing", "active", "working"]
            in_progress = [t for t in member_tasks if not t["is_complete"] and t["status"].lower() in active_statuses]
            backlog = [t for t in member_tasks if not t["is_complete"] and t["status"].lower() == "backlog"]

            # --- Score distribution ---
            score_dist = {1: 0, 2: 0, 3: 0, 5: 0, 8: 0, 13: 0}
            for t in completed_all:
                if t["score"] in score_dist:
                    score_dist[t["score"]] += 1

            # --- Current week points ---
            cur_mon, cur_sun = get_week_bounds(week_offset=0)
            cur_completed = [t for t in member_tasks if t["is_complete"] and t["date_closed"] and cur_mon <= t["date_closed"] <= cur_sun]
            current_week_points = sum(t["score"] or 0 for t in cur_completed)

            # --- Utilization rate ---
            member_hours = capacity.get(name, DEFAULT_MEMBER_HOURS)
            expected_pts = calculate_expected_points_from_hours(member_hours)
            # Average points over last 4 weeks for utilization
            last4_pts = [w["points"] for w in weekly_points[-4:]]
            avg_pts_4w = sum(last4_pts) / len(last4_pts) if last4_pts else 0
            utilization = round((avg_pts_4w / expected_pts) * 100) if expected_pts else 0

            results.append({
                "id": mid,
                "username": name,
                "email": member.get("email", ""),
                "initials": member.get("initials", ""),
                "current_week_points": current_week_points,
                "current_week_tasks": len(cur_completed),
                "weekly_points": weekly_points,
                "weekly_tasks": weekly_tasks,
                "on_time": on_time,
                "overdue": overdue,
                "no_due_date": no_due,
                "avg_close_days": avg_close_days,
                "in_progress": len(in_progress),
                "in_progress_points": sum(t["score"] or 0 for t in in_progress),
                "backlog": len(backlog),
                "backlog_points": sum(t["score"] or 0 for t in backlog),
                "score_distribution": score_dist,
                "capacity_hours": member_hours,
                "expected_points": expected_pts,
                "avg_points_4w": round(avg_pts_4w, 1),
                "utilization_pct": utilization,
            })

        # Sort by current week points desc
        results.sort(key=lambda x: x["current_week_points"], reverse=True)

        return jsonify({
            "members": results,
            "team_totals": {
                "total_members": len(results),
                "total_current_points": sum(r["current_week_points"] for r in results),
                "total_current_tasks": sum(r["current_week_tasks"] for r in results),
                "avg_utilization": round(sum(r["utilization_pct"] for r in results) / len(results)) if results else 0,
            }
        })
    except Exception as e:
        logger.error(f"Error in api_team_performance: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/team-performance/<int:member_id>")
@admin_required
def api_team_performance_member(member_id):
    """Get detailed performance data for a single team member."""
    try:
        all_tasks = get_all_tasks()
        capacity = build_team_capacity()
        member_tasks = [t for t in all_tasks if any(a["id"] == member_id for a in t["assignees"])]

        if not member_tasks:
            return jsonify({"error": "No tasks found for this member"}), 404

        # Get member name from first task
        member_name = "Unknown"
        for t in member_tasks:
            for a in t["assignees"]:
                if a["id"] == member_id:
                    member_name = a["username"]
                    break
            if member_name != "Unknown":
                break

        # 8-week detailed history
        weekly_data = []
        for offset in range(0, -8, -1):
            mon, sun = get_week_bounds(week_offset=offset)
            completed = [t for t in member_tasks if t["is_complete"] and t["date_closed"] and mon <= t["date_closed"] <= sun]
            pts = sum(t["score"] or 0 for t in completed)

            on_time = sum(1 for t in completed if t["due_date"] and t["date_closed"] <= t["due_date"])
            overdue_count = sum(1 for t in completed if t["due_date"] and t["date_closed"] > t["due_date"])

            weekly_data.append({
                "week": mon.strftime("%b %d"),
                "week_start": mon.strftime("%Y-%m-%d"),
                "points": pts,
                "tasks": len(completed),
                "on_time": on_time,
                "overdue": overdue_count,
            })
        weekly_data.reverse()

        # Recent completed tasks (last 2 weeks)
        two_weeks_ago = datetime.now() - timedelta(weeks=2)
        recent = [t for t in member_tasks if t["is_complete"] and t["date_closed"] and t["date_closed"] >= two_weeks_ago]
        recent.sort(key=lambda t: t["date_closed"], reverse=True)
        recent_tasks = [{
            "name": t["name"],
            "score": t["score"],
            "status": t["status"],
            "date_closed": t["date_closed"].strftime("%Y-%m-%d") if t["date_closed"] else None,
            "due_date": t["due_date"].strftime("%Y-%m-%d") if t["due_date"] else None,
            "was_on_time": (t["date_closed"] <= t["due_date"]) if t["due_date"] and t["date_closed"] else None,
            "url": t["url"],
            "time_spent_hours": round(t.get("time_spent_ms", 0) / 3600000, 1),
        } for t in recent[:20]]

        # Score distribution (all time)
        score_dist = {1: 0, 2: 0, 3: 0, 5: 0, 8: 0, 13: 0}
        completed_all = [t for t in member_tasks if t["is_complete"]]
        for t in completed_all:
            if t["score"] in score_dist:
                score_dist[t["score"]] += 1

        # Close times
        close_times = []
        for t in completed_all:
            if t["date_created"] and t["date_closed"]:
                delta = (t["date_closed"] - t["date_created"]).total_seconds() / 86400
                close_times.append(delta)

        member_hours = capacity.get(member_name, DEFAULT_MEMBER_HOURS)
        expected_pts = calculate_expected_points_from_hours(member_hours)

        return jsonify({
            "id": member_id,
            "username": member_name,
            "weekly_data": weekly_data,
            "recent_tasks": recent_tasks,
            "score_distribution": score_dist,
            "avg_close_days": round(sum(close_times) / len(close_times), 1) if close_times else None,
            "median_close_days": round(sorted(close_times)[len(close_times) // 2], 1) if close_times else None,
            "total_completed": len(completed_all),
            "capacity_hours": member_hours,
            "expected_points": expected_pts,
        })
    except Exception as e:
        logger.error(f"Error in api_team_performance_member: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def sync_users_scheduled():
    """Scheduled user sync (runs daily with other caches)."""
    try:
        pulse_members = get_pulse_team_members()
        results = sync_users_from_clickup(pulse_members, send_invites=True)
        logger.info(f"Scheduled user sync: {results['created']} created, {results['updated']} updated")
    except Exception as e:
        logger.error(f"Scheduled user sync failed: {e}")


# ============================================================================
# Sprint Planning Routes (Admin Only)
# ============================================================================

@app.route("/sprint-planning")
@admin_required
def sprint_planning():
    """Serve the sprint planning assistant page."""
    return render_template("sprint_planning.html")


@app.route("/api/sprint-planning")
@admin_required
def api_sprint_planning():
    """Get sprint planning data: backlog, capacity, workload, suggestions, history."""
    try:
        pulse_members = get_pulse_team_members()
        excluded = load_excluded_assignees()
        all_tasks = get_all_tasks()
        capacity = build_team_capacity()

        pulse_members = [m for m in pulse_members if m["username"] not in excluded]

        cur_mon, cur_sun = get_week_bounds(week_offset=0)

        # ── Backlog: unassigned or status=backlog, not complete ──
        backlog_tasks = []
        for t in all_tasks:
            if t["is_complete"]:
                continue
            status_lower = t["status"].lower()
            is_unassigned = len(t["assignees"]) == 0
            is_backlog = status_lower in ("backlog", "to do", "open", "pending")
            if is_unassigned or is_backlog:
                backlog_tasks.append({
                    "id": t["id"],
                    "name": t["name"],
                    "folder": t["folder"],
                    "list": t["list"],
                    "score": t["score"],
                    "status": t["status"],
                    "assignees": [a["username"] for a in t["assignees"]],
                    "url": t["url"],
                    "due_date": t["due_date"].isoformat() if t["due_date"] else None,
                    "priority": _task_priority_rank(t),
                })
        backlog_tasks.sort(key=lambda x: x["priority"])

        # ── Team capacity & workload ──
        active_statuses = ["in progress", "in review", "waiting response", "doing", "active", "working"]
        team_data = []
        for member in pulse_members:
            mid = member["id"]
            name = member["username"]
            member_tasks = [t for t in all_tasks if any(a["id"] == mid for a in t["assignees"])]

            # Current sprint assigned (not complete, active)
            sprint_tasks = [t for t in member_tasks if not t["is_complete"] and t["status"].lower() in active_statuses]
            sprint_points = sum(t["score"] or 0 for t in sprint_tasks)

            # Also count tasks completed this week
            completed_this_week = [t for t in member_tasks if t["is_complete"] and t["date_closed"] and cur_mon <= t["date_closed"] <= cur_sun]
            completed_points = sum(t["score"] or 0 for t in completed_this_week)

            # Capacity
            member_hours = capacity.get(name, DEFAULT_MEMBER_HOURS)
            expected_pts = calculate_expected_points_from_hours(member_hours)

            # Historical velocity (last 4 weeks)
            weekly_pts = []
            for offset in range(-4, 0):
                mon, sun = get_week_bounds(week_offset=offset)
                week_completed = [t for t in member_tasks if t["is_complete"] and t["date_closed"] and mon <= t["date_closed"] <= sun]
                weekly_pts.append(sum(t["score"] or 0 for t in week_completed))
            avg_velocity = round(sum(weekly_pts) / len(weekly_pts), 1) if weekly_pts else 0

            available_points = max(0, round(expected_pts - sprint_points - completed_points))

            team_data.append({
                "id": mid,
                "username": name,
                "initials": member.get("initials", ""),
                "capacity_hours": member_hours,
                "expected_points": expected_pts,
                "sprint_points": sprint_points,
                "completed_points": completed_points,
                "total_load": sprint_points + completed_points,
                "available_points": available_points,
                "avg_velocity": avg_velocity,
                "weekly_points": weekly_pts,
            })

        # ── Sprint suggestions: fit high-priority backlog into available capacity ──
        suggestions = []
        remaining_capacity = {m["username"]: m["available_points"] for m in team_data}
        for task in backlog_tasks:
            pts = task["score"] or 1
            # Find best-fit member (most remaining capacity)
            best_member = None
            best_remaining = -1
            for m in team_data:
                rem = remaining_capacity.get(m["username"], 0)
                if rem >= pts and rem > best_remaining:
                    best_member = m["username"]
                    best_remaining = rem
            if best_member:
                suggestions.append({
                    "task": task,
                    "suggested_assignee": best_member,
                    "remaining_after": best_remaining - pts,
                })
                remaining_capacity[best_member] -= pts
            if len(suggestions) >= 20:
                break

        # ── Historical sprint performance (last 4 weeks) ──
        history = []
        for offset in range(-4, 0):
            mon, sun = get_week_bounds(week_offset=offset)
            week_label = mon.strftime("%b %d")
            # All tasks that were in progress or completed during this week
            completed = [t for t in all_tasks if t["is_complete"] and t["date_closed"] and mon <= t["date_closed"] <= sun]
            actual_pts = sum(t["score"] or 0 for t in completed)
            task_count = len(completed)
            # Estimate planned = sum of expected points across team
            planned_pts = sum(calculate_expected_points_from_hours(capacity.get(m["username"], DEFAULT_MEMBER_HOURS)) for m in pulse_members)
            completion_rate = round((actual_pts / planned_pts) * 100) if planned_pts else 0
            history.append({
                "week": week_label,
                "planned_points": planned_pts,
                "actual_points": actual_pts,
                "tasks_completed": task_count,
                "completion_rate": completion_rate,
            })

        return jsonify({
            "backlog": backlog_tasks,
            "team": team_data,
            "suggestions": suggestions,
            "history": history,
            "current_sprint": {
                "start": cur_mon.strftime("%b %d"),
                "end": cur_sun.strftime("%b %d"),
            },
        })
    except Exception as e:
        logger.error(f"Error in api_sprint_planning: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def _task_priority_rank(task):
    """Return a numeric rank for sorting: lower = higher priority. Uses due date + score."""
    rank = 0
    if task.get("due_date"):
        days_until = (task["due_date"] - datetime.now()).days
        rank = days_until
    else:
        rank = 999
    # Higher score = more important
    score = task.get("score") or 0
    rank -= score * 2
    return rank


# ============================================================================
# Scheduler Setup - Runs daily at 2pm Eastern Time
# ============================================================================

def init_scheduler():
    """Initialize the background scheduler for daily cache refresh."""
    scheduler = BackgroundScheduler(daemon=True)

    # Schedule cache refresh at 2pm ET (14:00) every day
    eastern = pytz.timezone('US/Eastern')
    trigger = CronTrigger(hour=14, minute=0, timezone=eastern)

    scheduler.add_job(
        func=refresh_daily_cache,
        trigger=trigger,
        id='daily_cache_refresh',
        name='Refresh ClickUp data cache at 2pm ET',
        replace_existing=True
    )

    # Client health cache refresh daily at 2pm ET
    eastern = pytz.timezone('US/Eastern')
    client_health_trigger = CronTrigger(hour=14, minute=0, timezone=eastern)
    scheduler.add_job(
        func=refresh_client_health_cache,
        trigger=client_health_trigger,
        id='client_health_refresh',
        name='Refresh client health cache daily at 2pm ET',
        replace_existing=True
    )

    # User sync daily at 2pm ET
    user_sync_trigger = CronTrigger(hour=14, minute=0, timezone=eastern)
    scheduler.add_job(
        func=sync_users_scheduled,
        trigger=user_sync_trigger,
        id='user_sync',
        name='Sync users from ClickUp daily at 2pm ET',
        replace_existing=True
    )

    scheduler.start()
    logger.info("Scheduler started - agile cache, client health, user sync all at 2pm ET")

    # Ensure scheduler shuts down cleanly
    atexit.register(lambda: scheduler.shutdown())

    return scheduler


# Initialize scheduler when module loads (for production via gunicorn)
# Only start if not in debug reload mode
if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' or not app.debug:
    # Check if cache exists, if not do initial background build
    if not os.path.exists(DAILY_CACHE_FILE):
        import threading
        logger.info("No daily cache found — building in background on startup")
        threading.Thread(target=refresh_daily_cache, daemon=True).start()

    _scheduler = init_scheduler()

    # First-run: if client health DB is empty, do an immediate build in background
    if is_cache_empty():
        import threading
        logger.info("Client health cache is empty — triggering first-run build")
        threading.Thread(target=refresh_client_health_cache, daemon=True).start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=True, port=port, host="127.0.0.1")
