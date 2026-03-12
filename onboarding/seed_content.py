"""
Seed function for the 3-day onboarding curriculum.

Calls seed_all_modules() to populate the onboarding_modules table.
Content data lives in seed_data.py.
"""

import logging
from onboarding.db import create_module, module_count
from onboarding.seed_data import DAY_1_MODULES, DAY_2_MODULES, DAY_3_MODULES

logger = logging.getLogger(__name__)


def seed_all_modules():
    """Populate the onboarding_modules table with the full curriculum.

    Idempotent — skips if modules already exist.

    @returns The number of modules created (0 if already seeded).
    """
    if module_count() > 0:
        logger.info("Onboarding modules already seeded — skipping")
        return 0

    created = 0
    for day_num, modules in (
        (1, DAY_1_MODULES),
        (2, DAY_2_MODULES),
        (3, DAY_3_MODULES),
    ):
        for idx, mod in enumerate(modules):
            create_module(
                day=day_num,
                title=mod["title"],
                slug=mod["slug"],
                content_html=mod.get("content_html", ""),
                content_type=mod.get("content_type", "text"),
                loom_url=mod.get("loom_url"),
                track=mod.get("track", "all"),
                is_required=mod.get("is_required", 1),
                estimated_minutes=mod.get("estimated_minutes", 15),
                sort_order=idx,
            )
            created += 1

    logger.info("Seeded %d onboarding modules", created)
    return created
