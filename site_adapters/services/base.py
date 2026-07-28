"""
Base utilities — canonical _get_base_dir with no internal dependencies.
"""


def _get_base_dir() -> str:
    """Canonical base directory for site adapter data."""
    from django.conf import settings
    return settings.LD_SITE_ADAPTERS_DIR
