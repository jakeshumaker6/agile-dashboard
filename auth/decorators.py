"""
Authentication decorators for route protection.

Provides @login_required and @admin_required decorators with
automatic 2FA verification checking.
"""

from functools import wraps
from flask import session, redirect, url_for, jsonify, request
from datetime import datetime, timezone, timedelta


def _needs_2fa_reverification() -> bool:
    """
    Check if the current session needs 2FA re-verification.

    Returns True if:
    - User has 2FA enabled AND
    - (Never verified in this session OR last verification > 24 hours ago)
    """
    if not session.get('totp_enabled'):
        return False

    last_2fa = session.get('last_2fa_at')
    if not last_2fa:
        return True

    try:
        last_2fa_dt = datetime.fromisoformat(last_2fa)
        hours_since = (datetime.now(timezone.utc) - last_2fa_dt).total_seconds() / 3600
        return hours_since > 24
    except Exception:
        return True


def _needs_2fa_setup() -> bool:
    """
    Check if user needs to set up 2FA (mandatory for all users).

    Returns True if user is authenticated but 2FA is not enabled.
    """
    if not session.get('authenticated'):
        return False
    return not session.get('totp_enabled')


def login_required(f):
    """
    Decorator to require authenticated user.

    Checks:
    1. User is authenticated (has valid session)
    2. If 2FA is enabled, checks if re-verification is needed (24-hour rule)
    3. If 2FA is not set up, redirects to 2FA setup (mandatory)
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check basic authentication
        if not session.get('authenticated'):
            # For API routes, return JSON error
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('auth.login'))

        # Check if 2FA setup is needed (mandatory for all users)
        if _needs_2fa_setup():
            if request.path.startswith('/api/'):
                return jsonify({'error': '2FA setup required'}), 403
            return redirect(url_for('auth.setup_2fa'))

        # Check if 2FA re-verification is needed (24-hour rule)
        if _needs_2fa_reverification():
            if request.path.startswith('/api/'):
                return jsonify({'error': '2FA verification required'}), 403
            return redirect(url_for('auth.verify_2fa'))

        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """
    Decorator to require admin role.

    Applies all checks from @login_required plus verifies admin role.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check basic authentication
        if not session.get('authenticated'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('auth.login'))

        # Check if 2FA setup is needed
        if _needs_2fa_setup():
            if request.path.startswith('/api/'):
                return jsonify({'error': '2FA setup required'}), 403
            return redirect(url_for('auth.setup_2fa'))

        # Check if 2FA re-verification is needed
        if _needs_2fa_reverification():
            if request.path.startswith('/api/'):
                return jsonify({'error': '2FA verification required'}), 403
            return redirect(url_for('auth.verify_2fa'))

        # Check admin role
        if session.get('role') != 'admin':
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Admin access required'}), 403
            # Redirect non-admins to dashboard with implicit "access denied"
            return redirect(url_for('dashboard'))

        return f(*args, **kwargs)
    return decorated_function


def get_current_user() -> dict:
    """
    Get the current authenticated user's session data.

    Returns a dict with user info or empty dict if not authenticated.
    """
    if not session.get('authenticated'):
        return {}

    return {
        'id': session.get('user_id'),
        'email': session.get('email'),
        'username': session.get('username'),
        'role': session.get('role'),
        'totp_enabled': session.get('totp_enabled'),
        'last_2fa_at': session.get('last_2fa_at'),
    }


def is_admin() -> bool:
    """Check if the current user is an admin."""
    return session.get('authenticated') and session.get('role') == 'admin'
