import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class SiteAdaptersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "site_adapters"
    verbose_name = "Site Adapters"

    def ready(self):
        try:
            from site_adapters.views.helpers import _ensure_base_dirs
            _ensure_base_dirs()
        except Exception:
            logger.exception("Failed to ensure site adapters base directories")
