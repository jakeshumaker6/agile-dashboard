"""
External integrations for the Onboarding module.

Handles ClickUp first-ticket creation, Anthropic welcome message
generation, and Resend welcome email delivery.
"""

import json
import logging
import os
import urllib.error
import urllib.request

import resend

logger = logging.getLogger(__name__)

CLICKUP_API_TOKEN = os.environ.get("CLICKUP_API_TOKEN", "")
CLICKUP_LIST_ID = os.environ.get("ONBOARDING_CLICKUP_LIST_ID", "901323857895")
CLICKUP_TEAM_ID = os.environ.get("CLICKUP_TEAM_ID", "90132317968")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5001")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "Pulse Dashboard <noreply@pulsemarketing.co>")

_ENGINEER_DESC = (
    "Starter bug fix or small feature addition. "
    "Your first real contribution to a Pulse codebase."
)
_GENERAL_DESC = (
    "Content update, design tweak, or process documentation task. "
    "Your first real contribution to Pulse."
)

_WELCOME_SYSTEM = (
    "You are writing a brief, personal welcome message for a new team member "
    "joining Pulse — a creative marketing agency focused on live events and "
    "national concert tours. Pulse values are: Unwavering Integrity, "
    "Trailblazing Creativity, and Speed as a superpower. "
    "The team is small (under 10 people), tight-knit, and moves fast. "
    "Write 2-3 warm, specific sentences in plain HTML (no wrapper tags). "
    "Reference their track and start date. Be genuinely encouraging, not generic."
)


def create_first_ticket(participant: dict) -> str | None:
    """
    Create a ClickUp onboarding task for a new hire's first contribution.

    Args:
        participant: Participant dict from onboarding DB.

    Returns:
        ClickUp task URL (https://app.clickup.com/t/{id}) or None on failure.
    """
    if not CLICKUP_API_TOKEN:
        logger.warning("CLICKUP_API_TOKEN not set — skipping first-ticket creation")
        return None

    track = participant.get("track", "general")
    name = participant.get("name", "New Hire")
    description = _ENGINEER_DESC if track == "engineer" else _GENERAL_DESC

    body = {
        "name": f"[Onboarding] First Task \u2014 {name}",
        "description": description,
        "status": "to do",
    }

    url = f"https://api.clickup.com/api/v2/list/{CLICKUP_LIST_ID}/task"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": CLICKUP_API_TOKEN,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode())
            task_id = data.get("id")
            if not task_id:
                logger.error("ClickUp response missing task id: %s", data)
                return None
            task_url = f"https://app.clickup.com/t/{task_id}"
            logger.info("Created ClickUp first-ticket for %s: %s", name, task_url)
            return task_url
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode() if exc.fp else ""
        logger.error(
            "ClickUp API error %d creating task for %s: %s",
            exc.code, name, body_text,
        )
        return None
    except Exception as exc:
        logger.error("Unexpected error creating ClickUp task for %s: %s", name, exc)
        return None


def generate_welcome_message(participant: dict) -> str | None:
    """
    Generate a personalised AI welcome message via Anthropic.

    Called once when a participant is created. The result is cached
    in onboarding_participants.welcome_message and never regenerated.

    Args:
        participant: Participant dict from onboarding DB.

    Returns:
        HTML string (2-3 sentences) or None on failure.
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set — skipping welcome message generation")
        return None

    name = participant.get("name", "")
    track = participant.get("track", "general")
    start_date = participant.get("start_date", "")
    track_label = "Engineer" if track == "engineer" else "General team member"

    user_prompt = (
        f"Write a welcome message for {name}, joining as a {track_label} "
        f"starting on {start_date}. Make it feel personal, specific, and exciting."
    )

    request_body = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 300,
        "system": _WELCOME_SYSTEM,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(request_body).encode(),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            data = json.loads(response.read().decode())
            text = data["content"][0]["text"]
            logger.info("Generated welcome message for %s", name)
            return text
    except Exception as exc:
        logger.error("Failed to generate welcome message for %s: %s", name, exc)
        return None


def send_welcome_email(participant: dict, touchpoint_schedule=None) -> bool:
    """
    Send a branded welcome email to the new hire via Resend.

    Fire-and-forget — email failure does not block participant creation.

    Args:
        participant: Participant dict from onboarding DB.
        touchpoint_schedule: Optional list of touchpoint dicts for the email body.

    Returns:
        True if sent successfully, False otherwise.
    """
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — skipping welcome email")
        return False

    resend.api_key = RESEND_API_KEY

    name = participant.get("name", "")
    first_name = name.split()[0] if name else "there"
    email = participant.get("email", "")
    start_date = participant.get("start_date", "")
    track = participant.get("track", "general")

    touchpoint_html = _build_touchpoint_html(touchpoint_schedule)
    track_note = (
        "You're joining as an <strong>Engineer</strong> — Day 2 includes a "
        "technical deep-dive specific to your role."
        if track == "engineer"
        else "You're joining as a <strong>General team member</strong> — "
        "your track is crafted around your contribution area."
    )

    html = f"""
<div style="font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;
            max-width:600px;margin:0 auto;padding:40px 20px;background:#0F1318;color:#E8EAED;">
  <h1 style="color:#E85A71;margin:0 0 8px;">Welcome to Pulse, {first_name}!</h1>
  <p style="color:#A0AEC0;margin:0 0 24px;font-size:14px;">Your onboarding starts {start_date}</p>

  <p style="font-size:16px;line-height:1.6;">
    We're thrilled to have you on the team. Your structured 3-day onboarding
    is ready and waiting at the link below.
  </p>

  <p style="font-size:14px;color:#A0AEC0;">{track_note}</p>

  <h3 style="color:#E85A71;margin:24px 0 8px;">What to expect</h3>
  <ul style="padding-left:20px;line-height:1.8;font-size:15px;">
    <li><strong>Day 1 — Culture &amp; Identity:</strong> Our values, team, and what makes Pulse tick.</li>
    <li><strong>Day 2 — Your Toolkit:</strong> Tools, processes, and how we communicate.</li>
    <li><strong>Day 3 — Your Impact:</strong> Clients, projects, and your first contribution.</li>
  </ul>

  {touchpoint_html}

  <div style="text-align:center;margin:32px 0;">
    <a href="{APP_BASE_URL}/onboarding"
       style="background:#E85A71;color:#fff;padding:14px 32px;text-decoration:none;
              border-radius:6px;font-weight:600;display:inline-block;">
      Start Your Onboarding
    </a>
  </div>

  <hr style="border:none;border-top:1px solid #2A3344;margin:30px 0;">
  <p style="color:#4A5568;font-size:12px;text-align:center;">
    Pulse Marketing | Internal Application
  </p>
</div>
"""

    try:
        params = {
            "from": FROM_EMAIL,
            "to": [email],
            "subject": f"Welcome to Pulse, {first_name} \u2014 Your onboarding starts {start_date}",
            "html": html,
        }
        response = resend.Emails.send(params)
        logger.info("Welcome email sent to %s: %s", email, response.get("id", "unknown"))
        return True
    except Exception as exc:
        logger.error("Failed to send welcome email to %s: %s", email, exc)
        return False


def _build_touchpoint_html(touchpoint_schedule) -> str:
    """Return HTML for scheduled touchpoints, or empty string if none."""
    if not touchpoint_schedule:
        return ""

    schedule = touchpoint_schedule
    if isinstance(schedule, str):
        try:
            schedule = json.loads(schedule)
        except Exception:
            return ""

    if not isinstance(schedule, list) or not schedule:
        return ""

    rows = "".join(
        f"<li style='line-height:1.8;'>"
        f"<strong>Day {tp.get('day', '?')} — {tp.get('title', 'Call')}:</strong> "
        f"{tp.get('time', '')} {(' \u2014 ' + tp.get('participants', '')) if tp.get('participants') else ''}"
        f"</li>"
        for tp in schedule
    )

    return (
        f"<h3 style='color:#E85A71;margin:24px 0 8px;'>Scheduled touchpoints</h3>"
        f"<ul style='padding-left:20px;font-size:15px;'>{rows}</ul>"
    )


def fetch_assigned_projects(clickup_user_id) -> list:
    """
    Return ClickUp tasks assigned to a user, grouped by client and project.

    Uses the "Get Filtered Team Tasks" endpoint:
    GET /api/v2/team/{team_id}/task?assignees[]={user_id}

    Args:
        clickup_user_id: The ClickUp numeric user ID (from auth.users.clickup_id).

    Returns:
        List of dicts: [{client, project, tasks: [{name, url, status}]}]
        Returns [] on any failure — never raises.
    """
    if not CLICKUP_API_TOKEN:
        logger.warning("CLICKUP_API_TOKEN not set — skipping project fetch")
        return []

    url = (
        f"https://api.clickup.com/api/v2/team/{CLICKUP_TEAM_ID}/task"
        f"?assignees[]={clickup_user_id}&subtasks=true&include_closed=false&page=0"
    )
    req = urllib.request.Request(url, headers={"Authorization": CLICKUP_API_TOKEN})

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode())
            return _group_tasks_by_project(data.get("tasks", []))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        logger.error(
            "ClickUp API error %d fetching projects for user %s: %s",
            exc.code, clickup_user_id, body,
        )
        return []
    except Exception as exc:
        logger.error(
            "Unexpected error fetching projects for user %s: %s", clickup_user_id, exc
        )
        return []


def _group_tasks_by_project(tasks: list) -> list:
    """Group a flat ClickUp task list into client/project buckets.

    Args:
        tasks: Raw task dicts from ClickUp API response.

    Returns:
        List of dicts: [{client, project, tasks: [{name, url, status}]}]
    """
    groups: dict = {}
    for task in tasks:
        client = (task.get("folder") or {}).get("name", "General")
        project = (task.get("list") or {}).get("name", "Tasks")
        key = (client, project)
        if key not in groups:
            groups[key] = {"client": client, "project": project, "tasks": []}
        groups[key]["tasks"].append({
            "name": task.get("name", ""),
            "url": task.get("url", ""),
            "status": (task.get("status") or {}).get("status", ""),
        })
    return list(groups.values())
