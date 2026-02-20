"""
Pulse Agile Dashboard - ClickUp Sprint Metrics

A custom dashboard for viewing Agile/sprint progress from ClickUp,
including Fibonacci points, time tracking, and efficiency analysis.
"""

import os
import json
import time
import logging
import urllib.request
import urllib.error
import atexit
from datetime import datetime, timedelta
from collections import defaultdict
from functools import wraps
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "pulse-agile-dashboard-secret-key-change-in-prod")
app.permanent_session_lifetime = timedelta(hours=24)  # Sessions last 24 hours

# Simple in-memory cache (for short-term API response caching)
_cache = {}
CACHE_TTL = 60  # 60 seconds cache

# Daily cache file for pre-computed dashboard data
DAILY_CACHE_FILE = os.path.join(os.path.dirname(__file__), "daily_cache.json")
_daily_cache = None  # In-memory copy of daily cache
_daily_cache_loaded = False

# Team capacity config file (shared across all users)
CAPACITY_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "team_capacity.json")
_capacity_config = None  # In-memory copy

# Configuration
CLICKUP_API_TOKEN = os.environ.get("CLICKUP_API_TOKEN", "pk_82316108_QR4V75ZD4QS2SQTBBM1U16LWQITIBQ14")
CLICKUP_TEAM_ID = os.environ.get("CLICKUP_TEAM_ID", "90132317968")
FIBONACCI_FIELD_ID = os.environ.get("FIBONACCI_FIELD_ID", "c88be994-51de-4bd3-b2f5-7850202b84bd")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Dashboard password (single password for all users)
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "pulse2024")


def login_required(f):
    """Decorator to require login for routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

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

# Excluded assignees (non-Pulse employees who may appear on tasks)
EXCLUDED_ASSIGNEES = ["Fazail Sabri"]

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
    if key in _cache:
        value, expiry = _cache[key]
        if time.time() < expiry:
            return value
    return None


def set_cached(key: str, value, ttl: int = CACHE_TTL):
    """Set value in cache with TTL."""
    _cache[key] = (value, time.time() + ttl)


# ============================================================================
# Daily Cache System - Refreshes at 2pm ET daily
# ============================================================================

def load_daily_cache():
    """Load daily cache from file into memory."""
    global _daily_cache, _daily_cache_loaded

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
        # Clear the short-term cache to force fresh API calls
        global _cache
        _cache = {}

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
    """Fetch all tasks from ClickUp with their Fibonacci scores (cached)."""
    cached = get_cached("all_tasks")
    if cached:
        logger.info(f"Returning {len(cached)} cached tasks")
        return cached

    logger.info("Fetching all tasks from ClickUp (no cache)")
    tasks = []

    # Get spaces
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
                list_tasks = clickup_request(
                    f"/list/{lst['id']}/task?include_closed=true&subtasks=true"
                ).get("tasks", [])

                for task in list_tasks:
                    task_data = parse_task(task, folder_name, lst["name"])
                    if task_data:
                        tasks.append(task_data)

        # Folderless lists
        folderless = clickup_request(f"/space/{space_id}/list").get("lists", [])
        for lst in folderless:
            list_tasks = clickup_request(
                f"/list/{lst['id']}/task?include_closed=true&subtasks=true"
            ).get("tasks", [])

            for task in list_tasks:
                task_data = parse_task(task, "(No Folder)", lst["name"])
                if task_data:
                    tasks.append(task_data)

    set_cached("all_tasks", tasks)
    return tasks


def has_subtasks(task: dict) -> bool:
    """Check if a task has subtasks (is a parent task)."""
    subtasks = task.get("subtasks")
    # subtasks can be a list of subtask objects or just a count
    if isinstance(subtasks, list) and len(subtasks) > 0:
        return True
    if isinstance(subtasks, int) and subtasks > 0:
        return True
    return False


def parse_task(task: dict, folder_name: str, list_name: str) -> dict:
    """Parse a task and extract relevant fields.

    Returns None for parent tasks with subtasks (to avoid double-counting).
    Only standalone tasks and subtasks are included in metrics.
    """
    # Skip parent tasks that have subtasks (to avoid double-counting)
    # The subtasks themselves will be counted individually
    if has_subtasks(task):
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
    """Calculate all dashboard metrics for a given week."""
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
            if assignee["username"] in EXCLUDED_ASSIGNEES:
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
                if a["username"] not in EXCLUDED_ASSIGNEES
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

    prompt = f"""Analyze this Agile sprint data and provide 2-3 brief, actionable insights for the engineering team.

## This Week's Metrics
- Points Completed: {metrics['summary']['points_completed']}
- Tasks Completed: {metrics['summary']['tasks_completed']}
- Total Time Tracked: {metrics['summary']['total_time_hours']} hours

## Time vs Estimate by Score
"""
    for score in [1, 2, 3, 5, 8, 13]:
        m = metrics['score_metrics'][score]
        if m['actual_avg']:
            prompt += f"- {score} points: Expected {m['expected_min']}-{m['expected_max']}hrs, Actual avg {m['actual_avg']}hrs ({m['task_count']} tasks)\n"
        else:
            prompt += f"- {score} points: No completed tasks with time tracked\n"

    prompt += f"""
## Velocity Trend (Last 8 Weeks)
"""
    history = velocity_history.get("history", velocity_history) if isinstance(velocity_history, dict) else velocity_history
    for week in history[-8:]:
        prompt += f"- {week['week']}: {week['points']} points, {week['tasks']} tasks, {week['hours']}hrs\n"

    if isinstance(velocity_history, dict):
        prompt += f"\nBaseline velocity: {velocity_history.get('baseline', 0)} points/week\n"
        prompt += f"Stretch goal: {velocity_history.get('stretch_goal', 0)} points/week\n"

    prompt += """
Provide insights in this format:
1. [Efficiency observation about time tracking vs estimates]
2. [Velocity trend observation]
3. [One specific recommendation for improvement]

Keep each insight to 1-2 sentences. Be direct and actionable."""

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


@app.route("/login", methods=["GET", "POST"])
def login():
    """Login page."""
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == DASHBOARD_PASSWORD:
            session.permanent = True  # Use the 24-hour lifetime
            session["authenticated"] = True
            return redirect(url_for("dashboard"))
        else:
            error = "Invalid password"

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    """Logout and clear session."""
    session.clear()
    return redirect(url_for("login"))


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
@login_required
def api_save_team_capacity():
    """Save team capacity configuration (shared across all users)."""
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
@login_required
def api_refresh_cache():
    """Manually trigger a cache refresh."""
    logger.info("Manual cache refresh triggered")
    success = refresh_daily_cache()
    if success:
        return jsonify({"status": "success", "message": "Cache refreshed successfully"})
    return jsonify({"status": "error", "message": "Cache refresh failed"}), 500


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

    scheduler.start()
    logger.info("Scheduler started - daily cache refresh scheduled for 2pm ET")

    # Ensure scheduler shuts down cleanly
    atexit.register(lambda: scheduler.shutdown())

    return scheduler


# Initialize scheduler when module loads (for production via gunicorn)
# Only start if not in debug reload mode
if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' or not app.debug:
    # Check if cache exists, if not do initial load
    if not os.path.exists(DAILY_CACHE_FILE):
        logger.info("No daily cache found - will refresh on first request or at 2pm ET")

    _scheduler = init_scheduler()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=True, port=port, host="127.0.0.1")
