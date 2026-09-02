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

    - id != name → {id}.{name}
    - id == name → {name}（不重复）
    - 若 id 或 name 为空 → 使用非空的那个，均空则返回空字符串
    """
    id_ = (entry.get('id', '') if isinstance(entry, dict) else '').strip()
    name = (entry.get('name', '') if isinstance(entry, dict) else '').strip()
    if not id_ and not name:
        return ''
    if not id_:
        return name
    if not name:
        return id_
    if id_ == name:
        return name
    return f"{id_}.{name}"
