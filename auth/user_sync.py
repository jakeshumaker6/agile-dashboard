"""
User synchronization with ClickUp team members.

Syncs users from ClickUp (filtered to @pulsemarketing.co emails)
to the local auth database. Handles:
- Creating new users with invite emails
- Updating existing users (username changes)
- Deactivating users no longer in ClickUp
"""

import logging
import os
from typing import List, Dict, Set

from auth.db import (
    init_db,
    get_all_users,
    get_user_by_email,
    get_user_by_clickup_id,
    create_user,
    update_user,
    deactivate_user,
)
from auth.email import send_invite_email

logger = logging.getLogger(__name__)

# Comma-separated list of emails that get admin role on first sync
INITIAL_ADMIN_EMAILS = os.environ.get(
    "INITIAL_ADMIN_EMAILS",
    "jake@pulsemarketing.co,sean@pulsemarketing.co"
)


def get_initial_admin_emails() -> Set[str]:
    """Parse the INITIAL_ADMIN_EMAILS environment variable."""
    if not INITIAL_ADMIN_EMAILS:
        return set()
    return {email.strip().lower() for email in INITIAL_ADMIN_EMAILS.split(",") if email.strip()}


def sync_users_from_clickup(pulse_members: List[Dict], send_invites: bool = True) -> Dict:
    """
    Sync users from ClickUp team members to local database.

    Args:
        pulse_members: List of team members from get_pulse_team_members()
                      Each member has: id, username, email, initials
        send_invites: If True, send invite emails to new users

    Returns:
        Dict with sync results: created, updated, deactivated counts
    """
    init_db()

    results = {
        "created": 0,
        "updated": 0,
        "deactivated": 0,
        "errors": [],
        "new_users": [],
    }

    admin_emails = get_initial_admin_emails()
    existing_users = get_all_users()

    # Build lookup dictionaries
    existing_by_clickup_id = {
        u['clickup_id']: u for u in existing_users if u.get('clickup_id')
    }
    existing_by_email = {
        u['email'].lower(): u for u in existing_users
    }
    current_clickup_ids = set()

    for member in pulse_members:
        clickup_id = str(member.get('id', ''))
        email = member.get('email', '').lower()
        username = member.get('username', 'Unknown')

        if not email or not clickup_id:
            logger.warning(f"Skipping member with missing data: {member}")
            continue

        current_clickup_ids.add(clickup_id)

        # Determine role for new users
        role = 'admin' if email in admin_emails else 'regular'

        # Check if user exists by ClickUp ID
        if clickup_id in existing_by_clickup_id:
            existing = existing_by_clickup_id[clickup_id]
            # Update username if changed
            if existing.get('username') != username:
                update_user(existing['id'], username=username)
                results['updated'] += 1
                logger.info(f"Updated user {email}: username -> {username}")

        # Check if user exists by email (maybe ClickUp ID not linked yet)
        elif email in existing_by_email:
            existing = existing_by_email[email]
            # Link ClickUp ID if missing
            if not existing.get('clickup_id'):
                update_user(existing['id'], clickup_id=clickup_id, username=username)
                results['updated'] += 1
                logger.info(f"Linked ClickUp ID for {email}")

        # New user - create and optionally send invite
        else:
            new_user = create_user(
                email=email,
                username=username,
                clickup_id=clickup_id,
                role=role,
            )

            if new_user:
                results['created'] += 1
                results['new_users'].append({
                    'email': email,
                    'username': username,
                    'role': role,
                })
                logger.info(f"Created new user: {email} (role: {role})")

                # Send invite email
                if send_invites and new_user.get('invite_token'):
                    email_sent = send_invite_email(
                        email=email,
                        username=username,
                        invite_token=new_user['invite_token']
                    )
                    if not email_sent:
                        results['errors'].append(f"Failed to send invite to {email}")
            else:
                results['errors'].append(f"Failed to create user {email}")

    # Optionally deactivate users no longer in ClickUp
    # (Only if they have a ClickUp ID - manual users without ClickUp ID are preserved)
    for user in existing_users:
        if user.get('clickup_id') and user['clickup_id'] not in current_clickup_ids:
            if user.get('is_active'):
                deactivate_user(user['id'])
                results['deactivated'] += 1
                logger.info(f"Deactivated user no longer in ClickUp: {user['email']}")

    logger.info(
        f"User sync complete: {results['created']} created, "
        f"{results['updated']} updated, {results['deactivated']} deactivated"
    )

    return results


def resend_invite(user_id: int) -> bool:
    """
    Resend an invite email to a user who hasn't set up their password yet.

    Args:
        user_id: The user's database ID

    Returns:
        True if invite was sent successfully
    """
    from auth.db import get_user_by_id, generate_invite_token

    user = get_user_by_id(user_id)
    if not user:
        logger.error(f"User {user_id} not found")
        return False

    if user.get('password_hash'):
        logger.warning(f"User {user_id} already has a password set")
        return False

    # Generate new invite token
    from auth.db import _get_conn, init_db
    import secrets
    from datetime import datetime, timezone, timedelta

    try:
        conn = _get_conn()
        init_db()
        now = datetime.now(timezone.utc).isoformat()
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        conn.execute(
            """UPDATE users SET
                invite_token = ?,
                invite_expires = ?,
                updated_at = ?
            WHERE id = ?""",
            (token, expires, now, user_id)
        )
        conn.commit()

        # Send the invite email
        return send_invite_email(
            email=user['email'],
            username=user['username'],
            invite_token=token
        )
    except Exception as e:
        logger.error(f"Error resending invite: {e}")
        return False
