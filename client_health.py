"""
Client Health Dashboard - Data aggregation from ClickUp, Grain, and Gmail.

Tracks Red/Yellow/Green health flags for all active client accounts.
"""

import os
import json
import time
import logging
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from collections import defaultdict

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

OPERATIONS_SPACE_ID = "90139881259"
RECURRING_CLIENTS_FOLDER_ID = "901313653969"

EXCLUDED_FOLDERS = [
    "Client Template", "2-Day AI POCs", "Internal Projects", "Recurring Clients"
]

ACTIVE_CLIENTS = [
    "ANAD", "BRE Law", "City of Decatur", "DCC", "Family Home & Patio",
    "FEAST", "GAAPP", "Give Them Wings", "Hungerford", "Main Place",
    "National Concerts", "Premier Fund Solutions", "S40S",
    "St. Louis Crossing Church", "Strategic Wealth Group"
]

# Cache for client health data (30 min TTL)
_client_health_cache = {"data": None, "expires": 0}
CLIENT_HEALTH_CACHE_TTL = 1800  # 30 minutes

# ============================================================================
# Grain API
# ============================================================================

def load_grain_api_key():
    """Load Grain API key from .env.grain file."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.grain")
    try:
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith("GRAIN_API_KEY="):
                    return line.split("=", 1)[1].strip()
    except Exception as e:
        logger.error(f"Error loading Grain API key: {e}")
    return None


def grain_request(endpoint, params=None):
    """Make a request to Grain API."""
    grain_key = load_grain_api_key()
    if not grain_key:
        logger.warning("No Grain API key found")
        return {}

    url = f"https://api.grain.com/_/public-api{endpoint}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url += f"?{query}"

    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {grain_key}",
        "Accept": "application/json"
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        logger.error(f"Grain API error: {e.code} for {endpoint}")
        try:
            logger.error(f"Response: {e.read().decode()[:300]}")
        except:
            pass
        return {}
    except Exception as e:
        logger.error(f"Grain API error: {e}")
        return {}


def fetch_grain_recordings():
    """Fetch all recordings from Grain with pagination."""
    all_recordings = []
    cursor = None

    for _ in range(10):  # Max 10 pages (1000 recordings)
        params = {"limit": "100"}
        if cursor:
            params["cursor"] = cursor

        data = grain_request("/recordings", params)
        recordings = data.get("recordings", data.get("data", []))
        if not recordings:
            break

        all_recordings.extend(recordings)

        # Check for pagination cursor
        cursor = data.get("cursor", data.get("nextCursor", data.get("next_cursor")))
        if not cursor:
            break

    logger.info(f"Fetched {len(all_recordings)} Grain recordings")
    return all_recordings


def match_client_to_recording(recording, client_names):
    """Match a recording title to a client name. Returns client name or None."""
    title = (recording.get("title") or recording.get("name") or "").lower()

    for client in client_names:
        # Check various forms of the client name
        client_lower = client.lower()
        if client_lower in title:
            return client
        # Also check abbreviations / short forms
        words = client_lower.split()
        if len(words) > 1 and all(w in title for w in words):
            return client

    return None


# ============================================================================
# Gmail API
# ============================================================================

def get_gmail_service():
    """Build Gmail API service using service account with domain-wide delegation."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        sa_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                               ".env.google-service-account.json")

        credentials = service_account.Credentials.from_service_account_file(
            sa_path,
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            subject="jake@pulsemarketing.co"
        )

        return build("gmail", "v1", credentials=credentials, cache_discovery=False)
    except ImportError:
        logger.warning("google-auth or google-api-python-client not installed")
        return None
    except Exception as e:
        logger.error(f"Error building Gmail service: {e}")
        return None


def search_client_emails(gmail_service, client_name, max_results=5):
    """Search Gmail for recent emails mentioning a client."""
    if not gmail_service:
        return []

    try:
        # Search in subject and body for client name
        query = f'"{client_name}"'
        results = gmail_service.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()

        messages = results.get("messages", [])
        emails = []

        for msg_ref in messages:
            msg = gmail_service.users().messages().get(
                userId="me", id=msg_ref["id"], format="metadata",
                metadataHeaders=["Subject", "From", "Date"]
            ).execute()

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            internal_date = int(msg.get("internalDate", 0)) / 1000  # ms to seconds

            emails.append({
                "id": msg_ref["id"],
                "subject": headers.get("Subject", "(no subject)"),
                "from": headers.get("From", ""),
                "date": datetime.fromtimestamp(internal_date, tz=timezone.utc).isoformat() if internal_date else None,
                "date_ts": internal_date,
                "snippet": msg.get("snippet", "")[:200],
            })

        return emails
    except Exception as e:
        logger.error(f"Gmail search error for '{client_name}': {e}")
        return []


def simple_sentiment(text):
    """Very basic keyword-based sentiment analysis. Returns 'positive', 'neutral', or 'negative'."""
    text_lower = text.lower()

    negative_words = [
        "unhappy", "frustrated", "disappointed", "upset", "complaint", "issue",
        "problem", "cancel", "termination", "concerned", "delay", "overdue",
        "late", "missed", "wrong", "error", "fail", "poor", "bad", "worse",
        "urgent", "asap", "immediately", "unacceptable", "behind schedule"
    ]
    positive_words = [
        "thank", "great", "awesome", "excellent", "happy", "pleased", "love",
        "perfect", "amazing", "wonderful", "impressive", "good job", "well done",
        "appreciate", "fantastic", "excited", "thrilled"
    ]

    neg_count = sum(1 for w in negative_words if w in text_lower)
    pos_count = sum(1 for w in positive_words if w in text_lower)

    if neg_count > pos_count and neg_count >= 2:
        return "negative"
    elif neg_count > pos_count:
        return "mildly_negative"
    elif pos_count > neg_count:
        return "positive"
    return "neutral"


# ============================================================================
# ClickUp Data
# ============================================================================

def fetch_client_tasks(clickup_request_fn):
    """Fetch tasks for each active client from ClickUp Operations space.

    Returns dict: {client_name: [tasks]}
    """
    client_tasks = defaultdict(list)

    # 1. Get folders from Operations space
    folders_data = clickup_request_fn(f"/space/{OPERATIONS_SPACE_ID}/folder")
    folders = folders_data.get("folders", [])

    for folder in folders:
        folder_name = folder["name"]
        if folder_name in EXCLUDED_FOLDERS:
            continue

        # Check if this folder is an active client
        matched_client = None
        for client in ACTIVE_CLIENTS:
            if client.lower() == folder_name.lower() or client.lower() in folder_name.lower():
                matched_client = client
                break

        if not matched_client:
            continue

        # Get all lists in this folder
        for lst in folder.get("lists", []):
            tasks_data = clickup_request_fn(
                f"/list/{lst['id']}/task?include_closed=true&subtasks=true"
            )
            for task in tasks_data.get("tasks", []):
                client_tasks[matched_client].append(task)

    # 2. Get Recurring Clients folder
    recurring_data = clickup_request_fn(f"/folder/{RECURRING_CLIENTS_FOLDER_ID}")
    recurring_lists = recurring_data.get("lists", [])
    if not recurring_lists:
        # Try getting lists from folder
        recurring_lists_data = clickup_request_fn(f"/folder/{RECURRING_CLIENTS_FOLDER_ID}/list")
        recurring_lists = recurring_lists_data.get("lists", [])

    for lst in recurring_lists:
        list_name = lst["name"]
        # Match list name to active client
        matched_client = None
        for client in ACTIVE_CLIENTS:
            if client.lower() in list_name.lower() or list_name.lower() in client.lower():
                matched_client = client
                break

        if not matched_client:
            continue

        tasks_data = clickup_request_fn(
            f"/list/{lst['id']}/task?include_closed=true&subtasks=true"
        )
        for task in tasks_data.get("tasks", []):
            client_tasks[matched_client].append(task)

    logger.info(f"Fetched tasks for {len(client_tasks)} clients")
    return dict(client_tasks)


def analyze_client_tasks(tasks):
    """Analyze a client's task list. Returns metrics dict."""
    now = datetime.now(timezone.utc)
    open_tasks = []
    overdue_tasks = []
    completed_tasks = []
    assignees = set()

    for task in tasks:
        status_type = task.get("status", {}).get("type", "")
        is_closed = status_type == "closed"

        # Collect assignees
        for assignee in task.get("assignees", []):
            name = assignee.get("username", "")
            if name:
                assignees.add(name)

        if is_closed:
            completed_tasks.append(task)
        else:
            open_tasks.append(task)
            # Check if overdue
            due_date_ms = task.get("due_date")
            if due_date_ms:
                try:
                    due_date = datetime.fromtimestamp(int(due_date_ms) / 1000, tz=timezone.utc)
                    if due_date < now:
                        days_overdue = (now - due_date).days
                        overdue_tasks.append({
                            "id": task["id"],
                            "name": task["name"],
                            "due_date": due_date.isoformat(),
                            "days_overdue": days_overdue,
                            "url": task.get("url", ""),
                            "status": task.get("status", {}).get("status", ""),
                            "assignees": [a.get("username", "") for a in task.get("assignees", [])],
                        })
                except (ValueError, TypeError):
                    pass

    total = len(tasks)
    completion_rate = (len(completed_tasks) / total * 100) if total > 0 else 0
    avg_days_overdue = (
        sum(t["days_overdue"] for t in overdue_tasks) / len(overdue_tasks)
        if overdue_tasks else 0
    )

    return {
        "open_count": len(open_tasks),
        "overdue_count": len(overdue_tasks),
        "completed_count": len(completed_tasks),
        "total_count": total,
        "completion_rate": round(completion_rate, 1),
        "avg_days_overdue": round(avg_days_overdue, 1),
        "overdue_tasks": sorted(overdue_tasks, key=lambda x: x["days_overdue"], reverse=True),
        "assignees": sorted(list(assignees)),
    }


# ============================================================================
# Health Scoring
# ============================================================================

def calculate_health(task_metrics, days_since_email, days_since_call, email_sentiment):
    """
    Calculate health status based on scoring logic.
    Returns: {"status": "green"|"yellow"|"red", "reasons": [...]}
    """
    yellow_signals = []
    red_signals = []

    # Task-based signals
    overdue = task_metrics.get("overdue_count", 0)
    if overdue >= 4:
        red_signals.append(f"{overdue} overdue tasks")
    elif 1 <= overdue <= 3:
        yellow_signals.append(f"{overdue} overdue task{'s' if overdue > 1 else ''}")

    # Communication signals - email
    if days_since_email is not None:
        if days_since_email > 14:
            red_signals.append(f"No email in {days_since_email} days")
        elif days_since_email > 7:
            yellow_signals.append(f"Last email {days_since_email} days ago")

    # Communication signals - call
    if days_since_call is not None:
        if days_since_call > 14:
            red_signals.append(f"No call in {days_since_call} days")
        elif days_since_call > 7:
            yellow_signals.append(f"Last call {days_since_call} days ago")

    # Sentiment signals
    if email_sentiment == "negative":
        red_signals.append("Negative email sentiment")
    elif email_sentiment == "mildly_negative":
        yellow_signals.append("Mildly negative email tone")

    # Determine status
    if red_signals:
        return {"status": "red", "reasons": red_signals + yellow_signals}
    elif len(yellow_signals) >= 2:
        # 2+ yellow signals = red
        return {"status": "red", "reasons": yellow_signals, "escalated": True}
    elif yellow_signals:
        return {"status": "yellow", "reasons": yellow_signals}
    else:
        return {"status": "green", "reasons": ["All healthy"]}


# ============================================================================
# Main Aggregation
# ============================================================================

def get_client_health_data(clickup_request_fn, force_refresh=False):
    """
    Aggregate client health data from all sources.
    Caches for 30 minutes.
    """
    global _client_health_cache

    now = time.time()
    if not force_refresh and _client_health_cache["data"] and now < _client_health_cache["expires"]:
        logger.info("Returning cached client health data")
        return _client_health_cache["data"]

    logger.info("Building client health data from APIs...")

    # 1. ClickUp tasks
    client_tasks = fetch_client_tasks(clickup_request_fn)

    # 2. Grain recordings
    recordings = fetch_grain_recordings()
    client_last_call = {}
    client_recent_calls = defaultdict(list)
    for rec in recordings:
        matched = match_client_to_recording(rec, ACTIVE_CLIENTS)
        if matched:
            rec_date_str = rec.get("date") or rec.get("created_at") or rec.get("start_time") or rec.get("timestamp")
            if rec_date_str:
                try:
                    # Handle various date formats
                    if isinstance(rec_date_str, (int, float)):
                        rec_date = datetime.fromtimestamp(rec_date_str / 1000 if rec_date_str > 1e12 else rec_date_str, tz=timezone.utc)
                    else:
                        # Try ISO format
                        rec_date_str = rec_date_str.replace("Z", "+00:00")
                        rec_date = datetime.fromisoformat(rec_date_str)
                        if rec_date.tzinfo is None:
                            rec_date = rec_date.replace(tzinfo=timezone.utc)

                    if matched not in client_last_call or rec_date > client_last_call[matched]:
                        client_last_call[matched] = rec_date

                    client_recent_calls[matched].append({
                        "title": rec.get("title") or rec.get("name", ""),
                        "date": rec_date.isoformat(),
                        "url": rec.get("url") or rec.get("link", ""),
                    })
                except Exception as e:
                    logger.debug(f"Could not parse Grain date '{rec_date_str}': {e}")

    # Sort recent calls by date
    for client in client_recent_calls:
        client_recent_calls[client].sort(key=lambda x: x["date"], reverse=True)
        client_recent_calls[client] = client_recent_calls[client][:5]  # Keep top 5

    # 3. Gmail
    gmail_service = get_gmail_service()
    client_email_data = {}
    for client in ACTIVE_CLIENTS:
        emails = search_client_emails(gmail_service, client, max_results=5)
        if emails:
            latest = max(emails, key=lambda e: e.get("date_ts", 0))
            # Aggregate sentiment from recent emails
            all_text = " ".join(e.get("snippet", "") + " " + e.get("subject", "") for e in emails)
            sentiment = simple_sentiment(all_text)
            client_email_data[client] = {
                "last_date": latest.get("date"),
                "last_date_ts": latest.get("date_ts"),
                "sentiment": sentiment,
                "recent_emails": [{
                    "subject": e["subject"],
                    "from": e["from"],
                    "date": e["date"],
                    "snippet": e["snippet"],
                } for e in emails],
            }
        else:
            client_email_data[client] = {
                "last_date": None,
                "last_date_ts": None,
                "sentiment": "neutral",
                "recent_emails": [],
            }

    # 4. Build per-client health data
    now_dt = datetime.now(timezone.utc)
    clients = []

    for client_name in ACTIVE_CLIENTS:
        tasks = client_tasks.get(client_name, [])
        task_metrics = analyze_client_tasks(tasks)

        # Days since last email
        email_data = client_email_data.get(client_name, {})
        days_since_email = None
        if email_data.get("last_date_ts"):
            last_email_dt = datetime.fromtimestamp(email_data["last_date_ts"], tz=timezone.utc)
            days_since_email = (now_dt - last_email_dt).days

        # Days since last call
        days_since_call = None
        if client_name in client_last_call:
            days_since_call = (now_dt - client_last_call[client_name]).days

        # Health scoring
        health = calculate_health(
            task_metrics, days_since_email, days_since_call,
            email_data.get("sentiment", "neutral")
        )

        clients.append({
            "name": client_name,
            "health": health,
            "tasks": {
                "open": task_metrics["open_count"],
                "overdue": task_metrics["overdue_count"],
                "completed": task_metrics["completed_count"],
                "total": task_metrics["total_count"],
                "completion_rate": task_metrics["completion_rate"],
                "avg_days_overdue": task_metrics["avg_days_overdue"],
                "overdue_list": task_metrics["overdue_tasks"][:10],
                "assignees": task_metrics["assignees"],
            },
            "communication": {
                "days_since_email": days_since_email,
                "days_since_call": days_since_call,
                "email_sentiment": email_data.get("sentiment", "neutral"),
                "last_email_date": email_data.get("last_date"),
                "last_call_date": client_last_call.get(client_name, "").isoformat() if client_name in client_last_call else None,
                "recent_emails": email_data.get("recent_emails", [])[:5],
                "recent_calls": client_recent_calls.get(client_name, []),
            },
        })

    # Sort: red first, then yellow, then green
    status_order = {"red": 0, "yellow": 1, "green": 2}
    clients.sort(key=lambda c: (status_order.get(c["health"]["status"], 3), c["name"]))

    result = {
        "clients": clients,
        "summary": {
            "total": len(clients),
            "red": sum(1 for c in clients if c["health"]["status"] == "red"),
            "yellow": sum(1 for c in clients if c["health"]["status"] == "yellow"),
            "green": sum(1 for c in clients if c["health"]["status"] == "green"),
        },
        "last_updated": now_dt.isoformat(),
    }

    _client_health_cache["data"] = result
    _client_health_cache["expires"] = now + CLIENT_HEALTH_CACHE_TTL
    logger.info(f"Client health data built: {result['summary']}")

    return result
