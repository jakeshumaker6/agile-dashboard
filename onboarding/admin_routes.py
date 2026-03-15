"""
Admin routes for the Onboarding module.

Provides the admin panel page and API endpoints for managing
participants, modules, and viewing progress.
"""

import json
import logging
from flask import Blueprint, render_template, jsonify, request, session

from auth import admin_required
from auth.db import get_user_by_email, update_user
from onboarding.db import (
    create_participant,
    get_participant,
    list_participants,
    update_participant,
    get_all_modules,
    get_module,
    create_module,
    update_module,
    delete_module,
    get_progress_for_participant,
    get_tool_setup,
    calculate_progress,
)
from onboarding.integrations import (
    create_first_ticket,
    send_welcome_email,
)
from onboarding.seed_content import seed_all_modules

logger = logging.getLogger(__name__)

onboarding_admin_bp = Blueprint("onboarding_admin", __name__)


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------

@onboarding_admin_bp.route("/admin/onboarding")
@admin_required
def admin_onboarding_page():
    """Render the onboarding admin panel."""
    return render_template("admin/onboarding.html")


# ---------------------------------------------------------------------------
# Participant management API
# ---------------------------------------------------------------------------

@onboarding_admin_bp.route("/api/admin/onboarding/participants", methods=["GET"])
@admin_required
def api_list_participants():
    """List all onboarding participants with progress summaries."""
    participants = list_participants()
    enriched = []
    for p in participants:
        stats = calculate_progress(p["id"], p["track"])
        tools = get_tool_setup(p["id"])
        tools_confirmed = sum(1 for t in tools if t["confirmed"])
        enriched.append({
            **p,
            "progress": stats,
            "tools_confirmed": tools_confirmed,
            "tools_total": len(tools),
        })
    return jsonify({"participants": enriched})


@onboarding_admin_bp.route("/api/admin/onboarding/participants", methods=["POST"])
@admin_required
def api_create_participant():
    """Initiate onboarding for a new hire."""
    data = request.get_json(silent=True) or {}

    name = data.get("name", "").strip()
    email = data.get("email", "").strip()
    track = data.get("track", "general")
    start_date = data.get("start_date", "")
    touchpoints = data.get("touchpoint_schedule")

    if not name or not email or not start_date:
        return jsonify({"error": "name, email, and start_date are required"}), 400

    if track not in ("engineer", "general"):
        return jsonify({"error": "track must be 'engineer' or 'general'"}), 400

    try:
        participant = create_participant(
            name=name,
            email=email,
            track=track,
            start_date=start_date,
            touchpoint_schedule=touchpoints,
        )
    except Exception as exc:
        logger.error("Failed to create participant: %s", exc)
        if "UNIQUE constraint" in str(exc):
            return jsonify({"error": "A participant with this email already exists"}), 409
        raise

    # Set matching auth user to onboarding role (if account exists)
    auth_user = get_user_by_email(email)
    if auth_user and auth_user["role"] not in ("admin",):
        update_user(auth_user["id"], role="onboarding")
        logger.info("Set onboarding role for auth user: %s", email)

    touchpoints = participant.get("touchpoint_schedule")
    send_welcome_email(participant, touchpoints)

    logger.info("Created onboarding participant: %s (%s)", name, email)
    return jsonify({"participant": participant}), 201


@onboarding_admin_bp.route(
    "/api/admin/onboarding/participants/<int:participant_id>", methods=["GET"]
)
@admin_required
def api_get_participant(participant_id):
    """Get detailed info for a single participant."""
    participant = get_participant(participant_id)
    if not participant:
        return jsonify({"error": "Participant not found"}), 404

    stats = calculate_progress(participant["id"], participant["track"])
    progress = get_progress_for_participant(participant["id"])
    tools = get_tool_setup(participant["id"])

    return jsonify({
        "participant": participant,
        "progress": stats,
        "module_progress": progress,
        "tools": tools,
    })


@onboarding_admin_bp.route(
    "/api/admin/onboarding/participants/<int:participant_id>/create-ticket",
    methods=["POST"],
)
@admin_required
def api_create_ticket(participant_id):
    """Auto-create a ClickUp first-contribution task for a participant."""
    participant = get_participant(participant_id)
    if not participant:
        return jsonify({"error": "Participant not found"}), 404

    task_url = create_first_ticket(participant)
    if task_url:
        update_participant(participant_id, first_ticket_url=task_url)

    return jsonify({"first_ticket_url": task_url})


@onboarding_admin_bp.route(
    "/api/admin/onboarding/participants/<int:participant_id>", methods=["PUT"]
)
@admin_required
def api_update_participant(participant_id):
    """Update participant fields (override status, day, ticket URL, etc.)."""
    participant = get_participant(participant_id)
    if not participant:
        return jsonify({"error": "Participant not found"}), 404

    data = request.get_json(silent=True) or {}
    updated = update_participant(participant_id, **data)
    return jsonify({"participant": updated})


# ---------------------------------------------------------------------------
# Module management API
# ---------------------------------------------------------------------------

@onboarding_admin_bp.route("/api/admin/onboarding/modules", methods=["GET"])
@admin_required
def api_list_modules():
    """List all onboarding modules."""
    modules = get_all_modules()
    return jsonify({"modules": modules})


@onboarding_admin_bp.route("/api/admin/onboarding/modules", methods=["POST"])
@admin_required
def api_create_module():
    """Create a new onboarding module."""
    data = request.get_json(silent=True) or {}

    required = ("day", "title", "slug")
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    try:
        mod = create_module(
            day=data["day"],
            title=data["title"],
            slug=data["slug"],
            content_html=data.get("content_html", ""),
            content_type=data.get("content_type", "text"),
            loom_url=data.get("loom_url"),
            track=data.get("track", "all"),
            is_required=data.get("is_required", 1),
            estimated_minutes=data.get("estimated_minutes", 15),
            sort_order=data.get("sort_order", 0),
        )
    except Exception as exc:
        logger.error("Failed to create module: %s", exc)
        if "UNIQUE constraint" in str(exc):
            return jsonify({"error": "A module with this slug already exists"}), 409
        raise

    return jsonify({"module": mod}), 201


@onboarding_admin_bp.route(
    "/api/admin/onboarding/modules/<int:module_id>", methods=["PUT"]
)
@admin_required
def api_update_module(module_id):
    """Update an onboarding module's content or settings."""
    mod = get_module(module_id)
    if not mod:
        return jsonify({"error": "Module not found"}), 404

    data = request.get_json(silent=True) or {}
    updated = update_module(module_id, **data)
    return jsonify({"module": updated})


@onboarding_admin_bp.route(
    "/api/admin/onboarding/modules/<int:module_id>", methods=["DELETE"]
)
@admin_required
def api_delete_module(module_id):
    """Delete an onboarding module."""
    mod = get_module(module_id)
    if not mod:
        return jsonify({"error": "Module not found"}), 404

    delete_module(module_id)
    return jsonify({"message": "Module deleted"})


@onboarding_admin_bp.route(
    "/api/admin/onboarding/modules/reorder", methods=["POST"]
)
@admin_required
def api_reorder_modules():
    """Reorder modules within a day. Expects {\"order\": [id, id, ...]}."""
    data = request.get_json(silent=True) or {}
    order = data.get("order", [])

    if not order:
        return jsonify({"error": "order array is required"}), 400

    for idx, module_id in enumerate(order):
        update_module(module_id, sort_order=idx)

    return jsonify({"message": "Modules reordered"})


# ---------------------------------------------------------------------------
# Content seeding
# ---------------------------------------------------------------------------

@onboarding_admin_bp.route("/api/onboarding/seed", methods=["POST"])
@admin_required
def api_seed_modules():
    """Seed the onboarding modules with default content. Idempotent."""
    count = seed_all_modules()
    if count == 0:
        return jsonify({"message": "Modules already seeded", "created": 0})
    return jsonify({"message": f"Seeded {count} modules", "created": count}), 201


# ---------------------------------------------------------------------------
# Analytics API (Phase 4 — stub for now)
# ---------------------------------------------------------------------------

@onboarding_admin_bp.route("/api/admin/onboarding/analytics", methods=["GET"])
@admin_required
def api_analytics():
    """Return aggregated onboarding analytics."""
    participants = list_participants()
    total = len(participants)
    completed = sum(1 for p in participants if p["status"] == "completed")
    active = sum(1 for p in participants if p["status"] == "active")

    ratings = [
        p["satisfaction_rating"] for p in participants
        if p["satisfaction_rating"] is not None
    ]
    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else None

    return jsonify({
        "total_participants": total,
        "active": active,
        "completed": completed,
        "avg_satisfaction": avg_rating,
        "total_ratings": len(ratings),
    })
