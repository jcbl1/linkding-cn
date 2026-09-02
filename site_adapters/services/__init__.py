"""
site_adapters — 声明式站点适配引擎

Public API:
    from site_adapters.services import get_metadata_config
    from site_adapters.services import get_snapshot_config
    from site_adapters.services import get_reader_config
    from site_adapters.services import validate_config
    from site_adapters.services import show_config
    from site_adapters.services import load_domain_config
"""

from site_adapters.services.config.loader import (
    load_domain_config,
    show_config,
)
from site_adapters.services.config.validator import (
    validate_config,
)
from site_adapters.services.config.resolver import (
    get_metadata_config,
    get_reader_config,
    get_snapshot_config,
)

__all__ = [
    "get_metadata_config",
    "get_snapshot_config",
    "get_reader_config",
    "load_domain_config",
    "validate_config",
    "show_config",
]
