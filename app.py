"""
Pulse Agile Dashboard - ClickUp Sprint Metrics

A custom dashboard for viewing Agile/sprint progress from ClickUp,
including Fibonacci points, time tracking, and efficiency analysis.
"""

import os
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from collections import defaultdict
from functools import wraps
from flask import Flask, render_template, jsonify, request, session, redirect, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "pulse-agile-dashboard-secret-key-change-in-prod")

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


def clickup_request(endpoint: str) -> dict:
    """Make a request to ClickUp API."""
    url = f"https://api.clickup.com/api/v2{endpoint}"
    req = urllib.request.Request(url, headers={"Authorization": CLICKUP_API_TOKEN})

    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        print(f"ClickUp API error: {e.code}")
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
    """Fetch all tasks from ClickUp with their Fibonacci scores."""
    tasks = []

    # Get spaces
    spaces = clickup_request(f"/team/{CLICKUP_TEAM_ID}/space").get("spaces", [])

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

    return tasks


def parse_task(task: dict, folder_name: str, list_name: str) -> dict:
    """Parse a task and extract relevant fields."""
    # Get Fibonacci score
    score = None
    for cf in task.get("custom_fields", []):
        if cf.get("id") == FIBONACCI_FIELD_ID and cf.get("value"):
            score = SCORE_OPTIONS.get(cf["value"])
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
    }


def get_time_entries(start_ts: int, end_ts: int, assignee_id: int = None):
    """Fetch time entries for a date range."""
    url = f"/team/{CLICKUP_TEAM_ID}/time_entries?start_date={start_ts}&end_date={end_ts}"
    if assignee_id:
        url += f"&assignee={assignee_id}"

    data = clickup_request(url)
    return data.get("data", [])


def get_team_members():
    """Get all team members."""
    # Use time entries to find unique users (more reliable than team endpoint)
    entries = clickup_request(
        f"/team/{CLICKUP_TEAM_ID}/time_entries?start_date=0&end_date=9999999999999"
    ).get("data", [])

    members = {}
    for entry in entries:
        user = entry.get("user", {})
        if user.get("id") and user["id"] not in members:
            members[user["id"]] = {
                "id": user["id"],
                "username": user.get("username", "Unknown"),
            }

    return list(members.values())


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

    # Calculate points
    points_completed = sum(t["score"] or 0 for t in completed_this_week)
    points_next_week = sum(t["score"] or 0 for t in tasks_next_week)
    tasks_completed_count = len(completed_this_week)

    # Get time entries for this week
    start_ts = int(monday.timestamp() * 1000)
    end_ts = int(sunday.timestamp() * 1000)
    time_entries = get_time_entries(start_ts, end_ts, assignee_id)

    # Total time tracked (convert ms to hours)
    total_time_ms = sum(int(e.get("duration", 0)) for e in time_entries)
    total_time_hours = total_time_ms / (1000 * 60 * 60)

    # Time by task (to correlate with scores)
    time_by_task = defaultdict(int)
    for entry in time_entries:
        task_id = entry.get("task", {}).get("id")
        if task_id:
            time_by_task[task_id] += int(entry.get("duration", 0))

    # Calculate average time per score
    time_per_score = defaultdict(list)
    for task in completed_this_week:
        if task["score"] and task["id"] in time_by_task:
            hours = time_by_task[task["id"]] / (1000 * 60 * 60)
            time_per_score[task["score"]].append(hours)

    # Average and efficiency by score
    score_metrics = {}
    for score in [1, 2, 3, 5, 8, 13]:
        times = time_per_score.get(score, [])
        avg_hours = sum(times) / len(times) if times else None
        expected = EXPECTED_HOURS[score]

        efficiency = None
        efficiency_status = "no_data"
        if avg_hours is not None:
            if avg_hours <= expected["max"]:
                efficiency = avg_hours / expected["mid"]
                efficiency_status = "on_track" if avg_hours <= expected["max"] else "over"
            else:
                efficiency = avg_hours / expected["mid"]
                efficiency_status = "over"

        score_metrics[score] = {
            "expected_min": expected["min"],
            "expected_max": expected["max"],
            "expected_mid": expected["mid"],
            "actual_avg": round(avg_hours, 2) if avg_hours else None,
            "task_count": len(times),
            "efficiency": round(efficiency, 2) if efficiency else None,
            "status": efficiency_status,
        }

    # Score distribution of completed tasks
    score_distribution = defaultdict(int)
    for task in completed_this_week:
        if task["score"]:
            score_distribution[task["score"]] += 1

    return {
        "week": {
            "start": monday.strftime("%Y-%m-%d"),
            "end": sunday.strftime("%Y-%m-%d"),
            "label": f"{monday.strftime('%b %d')} - {sunday.strftime('%b %d, %Y')}",
        },
        "summary": {
            "points_completed": points_completed,
            "points_next_week": points_next_week,
            "tasks_completed": tasks_completed_count,
            "total_time_hours": round(total_time_hours, 1),
        },
        "score_metrics": score_metrics,
        "score_distribution": dict(score_distribution),
        "expected_hours_reference": EXPECTED_HOURS,
    }


def get_velocity_history(weeks: int = 8, assignee_id: int = None):
    """Get velocity data for the last N weeks."""
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

    return list(reversed(history))


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
    for week in velocity_history[-8:]:
        prompt += f"- {week['week']}: {week['points']} points, {week['tasks']} tasks, {week['hours']}hrs\n"

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

@app.route("/login", methods=["GET", "POST"])
def login():
    """Login page."""
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == DASHBOARD_PASSWORD:
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
    week_offset = int(request.args.get("week_offset", 0))
    assignee_id = request.args.get("assignee_id")
    if assignee_id:
        assignee_id = int(assignee_id)

    metrics = calculate_metrics(week_offset=week_offset, assignee_id=assignee_id)
    return jsonify(metrics)


@app.route("/api/velocity")
@login_required
def api_velocity():
    """Get velocity history."""
    weeks = int(request.args.get("weeks", 8))
    assignee_id = request.args.get("assignee_id")
    if assignee_id:
        assignee_id = int(assignee_id)

    history = get_velocity_history(weeks=weeks, assignee_id=assignee_id)
    return jsonify(history)


@app.route("/api/team")
@login_required
def api_team():
    """Get team members."""
    members = get_team_members()
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=True, port=port, host="127.0.0.1")
