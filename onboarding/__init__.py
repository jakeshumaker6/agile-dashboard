"""
Pulse Employee Onboarding Module.

Provides a structured 3-day onboarding experience for new hires,
with role-based tracks, progress tracking, and an admin panel
for content management and monitoring.
"""

from onboarding.db import init_onboarding_db
from onboarding.routes import onboarding_bp
from onboarding.admin_routes import onboarding_admin_bp

__all__ = ["onboarding_bp", "onboarding_admin_bp", "init_onboarding_db"]
