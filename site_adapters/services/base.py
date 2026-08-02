"""
Base utilities — canonical directories with no internal dependencies.
"""


def _get_base_dir() -> str:
    """Canonical base directory for site adapter data."""
    from django.conf import settings
    return settings.LD_SITE_ADAPTERS_DIR


def _get_adapters_dir() -> str:
    """Canonical adapters directory (contains config.jsonc + adapter subdirs)."""
    import os
    return os.path.join(_get_base_dir(), 'adapters')

def _adapter_dir(entry: dict) -> str:
    """从适配器条目计算目录名。
    
    - 有 id：{id}.{name}
    - 无 id：{name}
    """
    id_ = entry.get('id', '') if isinstance(entry, dict) else ''
    name = entry.get('name', '') if isinstance(entry, dict) else ''
    if id_:
        return f"{id_}.{name}"
    return name
