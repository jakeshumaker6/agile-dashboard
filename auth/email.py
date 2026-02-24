"""
Email integration using Resend.

Handles sending invite emails, password reset emails, and other
transactional emails for the authentication system.
"""

import logging
import os
from typing import Optional

import resend

logger = logging.getLogger(__name__)

# Configuration from environment
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5001")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "Pulse Dashboard <noreply@pulsemarketing.co>")


def _init_resend():
    """Initialize Resend with API key."""
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set - emails will not be sent")
        return False
    resend.api_key = RESEND_API_KEY
    return True


def send_invite_email(email: str, username: str, invite_token: str) -> bool:
    """
    Send welcome/invite email with password setup link.

    Args:
        email: User's email address
        username: User's display name
        invite_token: Token for password setup

    Returns:
        True if email sent successfully, False otherwise
    """
    if not _init_resend():
        logger.warning(f"Skipping invite email to {email} - Resend not configured")
        return False

    setup_url = f"{APP_BASE_URL}/setup-password?token={invite_token}"

    try:
        params = {
            "from": FROM_EMAIL,
            "to": [email],
            "subject": "Welcome to Pulse Agile Dashboard",
            "html": f"""
            <div style="font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; max-width: 600px; margin: 0 auto; padding: 40px 20px;">
                <div style="text-align: center; margin-bottom: 30px;">
                    <h1 style="color: #1E2A4A; margin: 0;">Pulse Agile Dashboard</h1>
                </div>

                <p style="color: #333; font-size: 16px; line-height: 1.6;">
                    Hi {username},
                </p>

                <p style="color: #333; font-size: 16px; line-height: 1.6;">
                    You've been invited to access the Pulse Agile Dashboard. Click the button below to set up your password and enable two-factor authentication.
                </p>

                <div style="text-align: center; margin: 40px 0;">
                    <a href="{setup_url}"
                       style="background-color: #E85A71; color: white; padding: 14px 32px; text-decoration: none; border-radius: 6px; font-weight: 600; display: inline-block;">
                        Set Up Your Account
                    </a>
                </div>

                <p style="color: #666; font-size: 14px; line-height: 1.6;">
                    This link will expire in 7 days. If you didn't expect this invitation, please ignore this email.
                </p>

                <p style="color: #666; font-size: 14px; line-height: 1.6;">
                    Or copy and paste this URL into your browser:<br>
                    <a href="{setup_url}" style="color: #E85A71; word-break: break-all;">{setup_url}</a>
                </p>

                <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">

                <p style="color: #999; font-size: 12px; text-align: center;">
                    Pulse Marketing | Agile Dashboard
                </p>
            </div>
            """,
        }

        response = resend.Emails.send(params)
        logger.info(f"Invite email sent to {email}: {response.get('id', 'unknown')}")
        return True
    except Exception as e:
        logger.error(f"Failed to send invite email to {email}: {e}")
        return False


def send_password_reset_email(email: str, username: str, reset_token: str) -> bool:
    """
    Send password reset email.

    Args:
        email: User's email address
        username: User's display name
        reset_token: Token for password reset

    Returns:
        True if email sent successfully, False otherwise
    """
    if not _init_resend():
        logger.warning(f"Skipping reset email to {email} - Resend not configured")
        return False

    reset_url = f"{APP_BASE_URL}/reset-password?token={reset_token}"

    try:
        params = {
            "from": FROM_EMAIL,
            "to": [email],
            "subject": "Reset Your Password - Pulse Agile Dashboard",
            "html": f"""
            <div style="font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; max-width: 600px; margin: 0 auto; padding: 40px 20px;">
                <div style="text-align: center; margin-bottom: 30px;">
                    <h1 style="color: #1E2A4A; margin: 0;">Pulse Agile Dashboard</h1>
                </div>

                <p style="color: #333; font-size: 16px; line-height: 1.6;">
                    Hi {username},
                </p>

                <p style="color: #333; font-size: 16px; line-height: 1.6;">
                    We received a request to reset your password. Click the button below to create a new password.
                </p>

                <div style="text-align: center; margin: 40px 0;">
                    <a href="{reset_url}"
                       style="background-color: #E85A71; color: white; padding: 14px 32px; text-decoration: none; border-radius: 6px; font-weight: 600; display: inline-block;">
                        Reset Password
                    </a>
                </div>

                <p style="color: #666; font-size: 14px; line-height: 1.6;">
                    This link will expire in 1 hour. If you didn't request a password reset, please ignore this email - your password will remain unchanged.
                </p>

                <p style="color: #666; font-size: 14px; line-height: 1.6;">
                    Or copy and paste this URL into your browser:<br>
                    <a href="{reset_url}" style="color: #E85A71; word-break: break-all;">{reset_url}</a>
                </p>

                <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">

                <p style="color: #999; font-size: 12px; text-align: center;">
                    Pulse Marketing | Agile Dashboard
                </p>
            </div>
            """,
        }

        response = resend.Emails.send(params)
        logger.info(f"Password reset email sent to {email}: {response.get('id', 'unknown')}")
        return True
    except Exception as e:
        logger.error(f"Failed to send reset email to {email}: {e}")
        return False


def send_2fa_enabled_email(email: str, username: str) -> bool:
    """
    Send confirmation email when 2FA is enabled.

    Args:
        email: User's email address
        username: User's display name

    Returns:
        True if email sent successfully, False otherwise
    """
    if not _init_resend():
        return False

    try:
        params = {
            "from": FROM_EMAIL,
            "to": [email],
            "subject": "Two-Factor Authentication Enabled - Pulse Agile Dashboard",
            "html": f"""
            <div style="font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; max-width: 600px; margin: 0 auto; padding: 40px 20px;">
                <div style="text-align: center; margin-bottom: 30px;">
                    <h1 style="color: #1E2A4A; margin: 0;">Pulse Agile Dashboard</h1>
                </div>

                <p style="color: #333; font-size: 16px; line-height: 1.6;">
                    Hi {username},
                </p>

                <p style="color: #333; font-size: 16px; line-height: 1.6;">
                    Two-factor authentication has been successfully enabled on your account.
                    You'll need to enter a verification code from your authenticator app
                    every 24 hours when logging in.
                </p>

                <p style="color: #666; font-size: 14px; line-height: 1.6;">
                    If you didn't enable 2FA, please contact an administrator immediately.
                </p>

                <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">

                <p style="color: #999; font-size: 12px; text-align: center;">
                    Pulse Marketing | Agile Dashboard
                </p>
            </div>
            """,
        }

        response = resend.Emails.send(params)
        logger.info(f"2FA enabled email sent to {email}: {response.get('id', 'unknown')}")
        return True
    except Exception as e:
        logger.error(f"Failed to send 2FA enabled email to {email}: {e}")
        return False
