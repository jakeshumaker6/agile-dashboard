"""
Authentication module for Pulse Agile Dashboard.

Provides user-based authentication with role-based access control (RBAC),
two-factor authentication (TOTP), and ClickUp user sync.
"""

from auth.decorators import login_required, admin_required, get_current_user, is_admin
from auth.routes import auth_bp
from auth.db import init_db, get_user_by_email, get_user_by_id, create_user, update_user

__all__ = [
    'auth_bp',
    'login_required',
    'admin_required',
    'get_current_user',
    'is_admin',
    'init_db',
    'get_user_by_email',
    'get_user_by_id',
    'create_user',
    'update_user',
]
