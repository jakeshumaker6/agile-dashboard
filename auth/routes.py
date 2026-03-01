"""
Authentication routes for Pulse Agile Dashboard.

Provides routes for:
- Login/logout
- 2FA setup and verification
- Password reset
- Initial password setup (from invite)
- User settings
- Admin user management
"""

import io
import logging
from datetime import datetime, timezone

import pyotp
import qrcode
import qrcode.image.svg
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, make_response

from auth.db import (
    init_db,
    get_user_by_email,
    get_user_by_id,
    get_user_by_invite_token,
    get_user_by_reset_token,
    get_all_users,
    verify_password,
    set_user_password,
    set_totp_secret,
    enable_totp,
    update_last_2fa,
    generate_password_reset_token,
    clear_password_reset_token,
    is_reset_token_valid,
    is_invite_token_valid,
    needs_2fa_verification,
    needs_2fa_setup,
    update_user,
)
from auth.email import send_password_reset_email, send_2fa_enabled_email
from auth.decorators import login_required, admin_required, get_current_user

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)


# ============ Login/Logout ============

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login page with email and password."""
    error = None

    # If already authenticated, redirect to home
    if session.get('authenticated'):
        return redirect(url_for('home'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not email or not password:
            error = "Please enter your email and password"
        else:
            user = get_user_by_email(email)

            if not user:
                error = "Invalid email or password"
            elif not user.get('password_hash'):
                error = "Please complete your account setup first. Check your email for the invite link."
            elif not verify_password(user, password):
                error = "Invalid email or password"
            else:
                # Password valid - set up session
                _create_session(user)

                # Check if 2FA setup is needed (mandatory)
                if needs_2fa_setup(user):
                    return redirect(url_for('auth.setup_2fa'))

                # Check if 2FA verification is needed (24-hour rule)
                if needs_2fa_verification(user):
                    session['pending_2fa'] = True
                    return redirect(url_for('auth.verify_2fa'))

                logger.info(f"User logged in: {email}")
                return redirect(url_for('home'))

    return render_template('login.html', error=error)


@auth_bp.route('/logout')
def logout():
    """Logout and clear session."""
    email = session.get('email', 'unknown')
    session.clear()
    logger.info(f"User logged out: {email}")
    return redirect(url_for('auth.login'))


def _create_session(user: dict):
    """Create session variables for authenticated user."""
    session.permanent = True
    session['authenticated'] = True
    session['user_id'] = user['id']
    session['email'] = user['email']
    session['username'] = user['username']
    session['role'] = user['role']
    session['totp_enabled'] = bool(user.get('totp_enabled'))
    session['last_2fa_at'] = user.get('last_2fa_at')


# ============ 2FA Setup ============

@auth_bp.route('/2fa-setup', methods=['GET', 'POST'])
def setup_2fa():
    """Set up two-factor authentication (mandatory for all users)."""
    if not session.get('authenticated'):
        return redirect(url_for('auth.login'))

    user = get_user_by_id(session.get('user_id'))
    if not user:
        session.clear()
        return redirect(url_for('auth.login'))

    # Already has 2FA enabled
    if user.get('totp_enabled'):
        return redirect(url_for('home'))

    error = None

    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        secret = session.get('totp_secret')

        if not secret:
            error = "Session expired. Please refresh the page."
        elif not code:
            error = "Please enter the verification code"
        else:
            # Verify the code
            totp = pyotp.TOTP(secret)
            if totp.verify(code, valid_window=1):
                try:
                    # Save secret and enable 2FA
                    set_totp_secret(user['id'], secret)
                    enable_totp(user['id'])

                    # Update session
                    session['totp_enabled'] = True
                    session['last_2fa_at'] = datetime.now(timezone.utc).isoformat()
                    if 'totp_secret' in session:
                        del session['totp_secret']

                    # Send confirmation email (non-blocking, ignore failures)
                    try:
                        send_2fa_enabled_email(user['email'], user['username'])
                    except Exception as email_err:
                        logger.warning(f"Failed to send 2FA confirmation email: {email_err}")

                    logger.info(f"2FA enabled for user: {user['email']}")
                    return redirect(url_for('home'))
                except Exception as e:
                    logger.error(f"Error enabling 2FA: {e}")
                    error = "Failed to enable 2FA. Please try again."
            else:
                error = "Invalid verification code. Please try again."

    # Generate or retrieve TOTP secret
    if 'totp_secret' not in session:
        session['totp_secret'] = pyotp.random_base32()

    secret = session['totp_secret']

    # Generate QR code
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(
        name=user['email'],
        issuer_name="Pulse Agile Dashboard"
    )

    # Create QR code as SVG
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(provisioning_uri)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)

    import base64
    qr_code_data = base64.b64encode(buffer.getvalue()).decode('utf-8')

    return render_template(
        '2fa_setup.html',
        error=error,
        secret=secret,
        qr_code_data=qr_code_data,
        user=user,
    )


# ============ 2FA Verification ============

@auth_bp.route('/2fa-verify', methods=['GET', 'POST'])
def verify_2fa():
    """Verify 2FA code (required every 24 hours)."""
    if not session.get('authenticated'):
        return redirect(url_for('auth.login'))

    user = get_user_by_id(session.get('user_id'))
    if not user:
        session.clear()
        return redirect(url_for('auth.login'))

    # If 2FA not enabled, redirect to setup
    if not user.get('totp_enabled'):
        return redirect(url_for('auth.setup_2fa'))

    error = None

    if request.method == 'POST':
        code = request.form.get('code', '').strip()

        if not code:
            error = "Please enter the verification code"
        else:
            totp = pyotp.TOTP(user['totp_secret'])
            if totp.verify(code, valid_window=1):
                # Update last 2FA timestamp
                update_last_2fa(user['id'])
                now = datetime.now(timezone.utc).isoformat()
                session['last_2fa_at'] = now
                session.pop('pending_2fa', None)

                logger.info(f"2FA verified for user: {user['email']}")
                return redirect(url_for('home'))
            else:
                error = "Invalid verification code. Please try again."

    return render_template('2fa_verify.html', error=error, user=user)


# ============ Password Setup (from invite) ============

@auth_bp.route('/setup-password', methods=['GET', 'POST'])
def setup_password():
    """Set initial password from invite link."""
    token = request.args.get('token') or request.form.get('token')

    if not token:
        return render_template('setup_password.html', error="Invalid or missing invite link.")

    user = get_user_by_invite_token(token)

    if not user:
        return render_template('setup_password.html', error="Invalid invite link. Please contact an administrator.")

    if not is_invite_token_valid(user):
        return render_template('setup_password.html', error="This invite link has expired. Please contact an administrator.")

    if user.get('password_hash'):
        return render_template('setup_password.html', error="You have already set up your password. Please log in.")

    error = None

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if not password:
            error = "Please enter a password"
        elif len(password) < 8:
            error = "Password must be at least 8 characters"
        elif password != confirm:
            error = "Passwords do not match"
        else:
            # Set password
            if set_user_password(user['id'], password):
                logger.info(f"Password set for user: {user['email']}")
                # Log them in
                _create_session(user)
                # Redirect to 2FA setup (mandatory)
                return redirect(url_for('auth.setup_2fa'))
            else:
                error = "Failed to set password. Please try again."

    return render_template('setup_password.html', error=error, user=user, token=token)


# ============ Password Reset ============

@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Request password reset email."""
    message = None
    error = None

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()

        if not email:
            error = "Please enter your email address"
        else:
            user = get_user_by_email(email)

            # Always show success message to prevent email enumeration
            message = "If an account exists with that email, you will receive a password reset link shortly."

            if user and user.get('password_hash'):
                # Generate reset token and send email
                token = generate_password_reset_token(user['id'])
                if token:
                    send_password_reset_email(user['email'], user['username'], token)
                    logger.info(f"Password reset requested for: {email}")

    return render_template('forgot_password.html', message=message, error=error)


@auth_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    """Reset password with token from email."""
    token = request.args.get('token') or request.form.get('token')

    if not token:
        return render_template('reset_password.html', error="Invalid or missing reset link.")

    user = get_user_by_reset_token(token)

    if not user:
        return render_template('reset_password.html', error="Invalid reset link. Please request a new one.")

    if not is_reset_token_valid(user):
        return render_template('reset_password.html', error="This reset link has expired. Please request a new one.")

    error = None

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if not password:
            error = "Please enter a new password"
        elif len(password) < 8:
            error = "Password must be at least 8 characters"
        elif password != confirm:
            error = "Passwords do not match"
        else:
            # Set new password and clear token
            if set_user_password(user['id'], password):
                clear_password_reset_token(user['id'])
                logger.info(f"Password reset for user: {user['email']}")
                # Redirect to login
                return redirect(url_for('auth.login'))
            else:
                error = "Failed to reset password. Please try again."

    return render_template('reset_password.html', error=error, token=token)


# ============ User Settings ============

@auth_bp.route('/settings')
@login_required
def settings():
    """User settings page."""
    user = get_user_by_id(session.get('user_id'))
    return render_template('settings.html', user=user)


@auth_bp.route('/settings/change-password', methods=['POST'])
@login_required
def change_password():
    """Change password from settings."""
    user = get_user_by_id(session.get('user_id'))

    current = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm = request.form.get('confirm_password', '')

    if not verify_password(user, current):
        return jsonify({'error': 'Current password is incorrect'}), 400

    if len(new_password) < 8:
        return jsonify({'error': 'New password must be at least 8 characters'}), 400

    if new_password != confirm:
        return jsonify({'error': 'New passwords do not match'}), 400

    if set_user_password(user['id'], new_password):
        return jsonify({'message': 'Password changed successfully'})
    else:
        return jsonify({'error': 'Failed to change password'}), 500


@auth_bp.route('/settings/change-name', methods=['POST'])
@login_required
def change_name():
    """Change display name from settings."""
    user = get_user_by_id(session.get('user_id'))

    data = request.get_json()
    new_name = data.get('username', '').strip()

    if not new_name:
        return jsonify({'error': 'Name cannot be empty'}), 400

    if len(new_name) > 100:
        return jsonify({'error': 'Name is too long'}), 400

    if update_user(user['id'], username=new_name):
        session['username'] = new_name
        return jsonify({'message': 'Name updated successfully'})
    else:
        return jsonify({'error': 'Failed to update name'}), 500


# ============ Admin User Management ============

@auth_bp.route('/admin/users')
@admin_required
def admin_users():
    """Admin page for user management."""
    users = get_all_users()
    return render_template('admin/users.html', users=users)


@auth_bp.route('/api/admin/users')
@admin_required
def api_admin_users():
    """API endpoint for user list."""
    users = get_all_users()
    # Remove sensitive fields
    safe_users = []
    for user in users:
        safe_users.append({
            'id': user['id'],
            'email': user['email'],
            'username': user['username'],
            'role': user['role'],
            'totp_enabled': bool(user.get('totp_enabled')),
            'is_active': bool(user.get('is_active')),
            'has_password': bool(user.get('password_hash')),
            'created_at': user.get('created_at'),
        })
    return jsonify(safe_users)


@auth_bp.route('/api/admin/users/<int:user_id>/role', methods=['POST'])
@admin_required
def api_update_user_role(user_id):
    """Update a user's role (admin only)."""
    data = request.get_json()
    role = data.get('role')

    if role not in ['admin', 'regular']:
        return jsonify({'error': 'Invalid role'}), 400

    # Prevent removing your own admin role
    if user_id == session.get('user_id') and role != 'admin':
        return jsonify({'error': 'Cannot remove your own admin role'}), 400

    if update_user(user_id, role=role):
        user = get_user_by_id(user_id)
        logger.info(f"User {user['email']} role changed to {role} by {session.get('email')}")
        return jsonify({'message': f'Role updated to {role}'})
    else:
        return jsonify({'error': 'Failed to update role'}), 500


@auth_bp.route('/api/admin/users/<int:user_id>/deactivate', methods=['POST'])
@admin_required
def api_deactivate_user(user_id):
    """Deactivate a user (admin only)."""
    # Prevent deactivating yourself
    if user_id == session.get('user_id'):
        return jsonify({'error': 'Cannot deactivate your own account'}), 400

    if update_user(user_id, is_active=0):
        user = get_user_by_id(user_id)
        if user:
            logger.info(f"User {user['email']} deactivated by {session.get('email')}")
        return jsonify({'message': 'User deactivated'})
    else:
        return jsonify({'error': 'Failed to deactivate user'}), 500


@auth_bp.route('/api/admin/users/<int:user_id>/activate', methods=['POST'])
@admin_required
def api_activate_user(user_id):
    """Reactivate a deactivated user (admin only)."""
    if update_user(user_id, is_active=1):
        user = get_user_by_id(user_id)
        if user:
            logger.info(f"User {user['email']} reactivated by {session.get('email')}")
        return jsonify({'message': 'User reactivated'})
    else:
        return jsonify({'error': 'Failed to reactivate user'}), 500


@auth_bp.route('/api/admin/users/<int:user_id>/resend-invite', methods=['POST'])
@admin_required
def api_resend_invite(user_id):
    """Resend invite email to a user who hasn't set up their password."""
    from auth.user_sync import resend_invite

    result = resend_invite(user_id)
    if result.get('success'):
        return jsonify({'message': 'Invite email sent'})
    else:
        return jsonify({'error': result.get('error', 'Failed to send invite email')}), 500


# Note: /api/admin/sync-users is defined in app.py (needs access to get_pulse_team_members)
