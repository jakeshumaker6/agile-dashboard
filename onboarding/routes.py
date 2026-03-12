"""
New hire onboarding routes.

Provides the wizard page and progress-tracking API endpoints
for users going through the onboarding experience.
"""

import logging
from flask import Blueprint, render_template, jsonify, request, session

from auth import login_required
from onboarding.db import (
    get_active_participant_by_email,
    get_active_participant_by_user_id,
    get_modules_for_day,
    get_progress_for_participant,
    get_module,
    upsert_progress,
    calculate_progress,
    get_tool_setup,
    confirm_tool,
    link_participant_to_user,
    update_participant,
)

logger = logging.getLogger(__name__)

onboarding_bp = Blueprint("onboarding", __name__)


def _get_participant():
    """Resolve the current session user to an onboarding participant.

    Checks by user_id first, then by email. Auto-links if found by email
    but not yet linked to the auth user.
    """
    user_id = session.get("user_id")
    email = session.get("email")

    participant = get_active_participant_by_user_id(user_id)
    if participant:
        return participant

    participant = get_active_participant_by_email(email)
    if participant and user_id:
        link_participant_to_user(email, user_id)
    return participant


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------

@onboarding_bp.route("/onboarding")
@login_required
def onboarding_page():
    """Render the onboarding wizard page."""
    return render_template("onboarding.html")


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@onboarding_bp.route("/api/onboarding/my-progress")
@login_required
def api_my_progress():
    """Return the current user's onboarding state, modules, and progress."""
    participant = _get_participant()
    if not participant:
        return jsonify({"active": False}), 200

    track = participant["track"]
    progress_rows = get_progress_for_participant(participant["id"])
    progress_map = {p["module_id"]: p for p in progress_rows}

    days = {}
    for day_num in (1, 2, 3):
        modules = get_modules_for_day(day_num, track)
        enriched = []
        for mod in modules:
            prog = progress_map.get(mod["id"], {})
            enriched.append({
                **mod,
                "progress_status": prog.get("status", "not_started"),
                "response_text": prog.get("response_text"),
                "response_data": prog.get("response_data"),
                "completed_at": prog.get("completed_at"),
            })
        days[str(day_num)] = enriched

    stats = calculate_progress(participant["id"], track)
    tools = get_tool_setup(participant["id"])

    return jsonify({
        "active": True,
        "participant": {
            "id": participant["id"],
            "name": participant["name"],
            "track": participant["track"],
            "start_date": participant["start_date"],
            "status": participant["status"],
            "current_day": participant["current_day"],
            "first_ticket_url": participant["first_ticket_url"],
            "touchpoint_schedule": participant["touchpoint_schedule"],
            "welcome_message": participant["welcome_message"],
        },
        "days": days,
        "progress": stats,
        "tools": tools,
    })


@onboarding_bp.route("/api/onboarding/progress/<int:module_id>", methods=["POST"])
@login_required
def api_update_progress(module_id):
    """Mark a module as started, completed, or submit a response."""
    participant = _get_participant()
    if not participant:
        return jsonify({"error": "No active onboarding found"}), 404

    module = get_module(module_id)
    if not module:
        return jsonify({"error": "Module not found"}), 404

    data = request.get_json(silent=True) or {}
    status = data.get("status", "completed")
    if status not in ("not_started", "in_progress", "completed"):
        return jsonify({"error": "Invalid status"}), 400

    response_text = data.get("response_text")
    response_data = data.get("response_data")

    result = upsert_progress(
        participant["id"], module_id, status,
        response_text=response_text, response_data=response_data,
    )

    # Recalculate and check day advancement
    stats = calculate_progress(participant["id"], participant["track"])
    current_day = participant["current_day"]
    day_stats = stats["days"].get(current_day, {})
    if day_stats.get("pct") == 100 and current_day < 3:
        new_day = current_day + 1
        update_participant(participant["id"], current_day=new_day)
        logger.info(
            "Participant %s advanced to Day %d", participant["name"], new_day
        )

    # Check for full completion
    if stats["overall_pct"] == 100 and participant["status"] != "completed":
        from onboarding.db import _now
        from auth.db import update_user
        update_participant(
            participant["id"], status="completed", completed_at=_now()
        )
        logger.info("Participant %s completed onboarding", participant["name"])
        # Promote auth user from onboarding → regular
        user_id = session.get("user_id")
        if user_id and session.get("role") == "onboarding":
            update_user(user_id, role="regular")
            session["role"] = "regular"
            logger.info("Promoted user %s to regular role after onboarding", user_id)

    return jsonify({"progress": result, "stats": stats})


@onboarding_bp.route("/api/onboarding/tool-setup", methods=["GET"])
@login_required
def api_tool_setup_list():
    """Get all tool setup statuses for the current participant."""
    participant = _get_participant()
    if not participant:
        return jsonify({"error": "No active onboarding found"}), 404

    tools = get_tool_setup(participant["id"])
    return jsonify({"tools": tools})


@onboarding_bp.route("/api/onboarding/tool-setup/<tool_name>", methods=["POST"])
@login_required
def api_confirm_tool(tool_name):
    """Mark a specific tool as set up."""
    participant = _get_participant()
    if not participant:
        return jsonify({"error": "No active onboarding found"}), 404

    result = confirm_tool(participant["id"], tool_name)
    return jsonify(result)


@onboarding_bp.route("/api/onboarding/my-projects")
@login_required
def api_my_projects():
    """Return ClickUp tasks assigned to the current user, grouped by client/project."""
    from auth.db import get_user_by_id
    from onboarding.integrations import fetch_assigned_projects

    user = get_user_by_id(session.get("user_id"))
    if not user or not user["clickup_id"]:
        return jsonify({"projects": [], "message": "No ClickUp account linked yet"})

    projects = fetch_assigned_projects(user["clickup_id"])
    return jsonify({"projects": projects})


@onboarding_bp.route("/api/onboarding/satisfaction", methods=["POST"])
@login_required
def api_submit_satisfaction():
    """Submit the Day 3 satisfaction rating."""
    participant = _get_participant()
    if not participant:
        return jsonify({"error": "No active onboarding found"}), 404

    data = request.get_json(silent=True) or {}
    rating = data.get("rating")
    if not isinstance(rating, int) or rating < 1 or rating > 5:
        return jsonify({"error": "Rating must be 1-5"}), 400

    update_participant(participant["id"], satisfaction_rating=rating)
    return jsonify({"message": "Rating submitted", "rating": rating})
