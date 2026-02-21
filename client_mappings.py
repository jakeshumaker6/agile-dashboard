"""
Client Mappings - Email domain and Grain recording mappings for client matching.

Stores mappings in client_mappings.json for use by client health scoring.
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


def load_mappings():
    """Load client mappings from JSON file."""
    try:
        if os.path.exists(MAPPINGS_FILE):
            with open(MAPPINGS_FILE, 'r') as f:
                data = json.load(f)
                # Ensure both keys exist
                if "email_domains" not in data:
                    data["email_domains"] = {}
                if "grain_matches" not in data:
                    data["grain_matches"] = {}
                return data
    except Exception as e:
        logger.error(f"Error loading client mappings: {e}")
    return dict(_DEFAULT)


def save_mappings(data):
    """Save client mappings to JSON file."""
    try:
        with open(MAPPINGS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info("Client mappings saved")
        return True
    except Exception as e:
        logger.error(f"Error saving client mappings: {e}")
        return False


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
