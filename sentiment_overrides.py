"""
Sentiment Overrides — file-based manual override of Claude's sentiment ratings.

Stores overrides in sentiment_overrides.json. When applied to health data,
replaces the AI sentiment with the manual one and annotates the client record.
"""

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

OVERRIDES_FILE = os.path.join(os.path.dirname(__file__), "sentiment_overrides.json")


def load_overrides() -> dict:
    """Load overrides from disk. Returns {} on missing/corrupt file."""
    try:
        if os.path.exists(OVERRIDES_FILE):
            with open(OVERRIDES_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading sentiment overrides: {e}")
    return {}


def _save_overrides(data: dict):
    """Write overrides dict to disk."""
    with open(OVERRIDES_FILE, "w") as f:
        json.dump(data, f, indent=2)


def save_override(client: str, rating: str, reason: str = "", overridden_by: str = "User"):
    """Add or update an override for a client."""
    overrides = load_overrides()
    overrides[client] = {
        "rating": rating,
        "reason": reason,
        "overridden_by": overridden_by,
        "overridden_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_overrides(overrides)
    logger.info(f"Sentiment override saved: {client} → {rating} by {overridden_by}")


def delete_override(client: str):
    """Remove an override for a client."""
    overrides = load_overrides()
    if client in overrides:
        del overrides[client]
        _save_overrides(overrides)
        logger.info(f"Sentiment override removed: {client}")


def apply_overrides(health_data: dict):
    """Mutate health_data in place — swap sentiment for any overridden clients."""
    overrides = load_overrides()
    if not overrides:
        return

    for client in health_data.get("clients", []):
        name = client.get("name", "")
        if name in overrides:
            ov = overrides[name]
            comm = client.get("communication", {})
            # Preserve AI sentiment before overwriting
            client["ai_sentiment"] = comm.get("email_sentiment", "neutral")
            client["ai_sentiment_reason"] = comm.get("sentiment_reason", "")
            # Apply override
            comm["email_sentiment"] = ov["rating"]
            comm["sentiment_reason"] = ov.get("reason", "")
            client["sentiment_override"] = ov
