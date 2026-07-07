"""
快捷标签图标本地缓存服务
逐图标：从 Iconify API 获取一次 → 存本地文件 → 永久使用
内存缓存避免重复磁盘读取
"""
import json
import logging
import re
from pathlib import Path

import requests
from django.conf import settings

from bookmarks.utils import sanitize_svg_body

logger = logging.getLogger(__name__)

ICON_FOLDER = settings.LD_ICON_FOLDER
PRESET_ICON_NAMES = settings.LD_PRESET_ICON_NAMES

# 内存缓存（进程生命周期，图标不可变无需失效）
_memory_cache: dict[str, dict] = {}


def _ensure_icon_folder():
    Path(ICON_FOLDER).mkdir(parents=True, exist_ok=True)


def _icon_name_to_filename(icon_name: str) -> str:
    """tabler:star → tabler_star.json"""
    return re.sub(r"\W+", "_", icon_name) + ".json"


def _get_icon_path(icon_name: str) -> Path:
    return Path(ICON_FOLDER) / _icon_name_to_filename(icon_name)


def _read_from_disk(icon_name: str) -> dict | None:
    """从本地文件读取图标数据"""
    icon_path = _get_icon_path(icon_name)
    if not icon_path.exists():
        return None
    try:
        with open(icon_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "body" in data:
            return {
                "body": sanitize_svg_body(data["body"]),
                "width": data.get("width", 24),
                "height": data.get("height", 24),
            }
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("Failed to read cached icon %s: %s", icon_name, exc)
    return None


def _fetch_and_cache_icon(icon_name: str) -> dict | None:
    """从 Iconify API 获取图标并缓存到本地文件"""
    if ":" not in icon_name:
        return None
    prefix, name = icon_name.split(":", 1)
    url = f"https://api.iconify.design/{prefix}.json?icons={name}"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        icons = data.get("icons", {})
        icon_info = icons.get(name)
        if not icon_info or "body" not in icon_info:
            return None
        icon_data = {
            "body": sanitize_svg_body(icon_info["body"]),
            "width": icon_info.get("width", data.get("width", 24)),
            "height": icon_info.get("height", data.get("height", 24)),
        }
        # 写入本地缓存
        _ensure_icon_folder()
        icon_path = _get_icon_path(icon_name)
        with open(icon_path, "w", encoding="utf-8") as f:
            json.dump(icon_data, f, ensure_ascii=False)
        logger.debug("Cached icon %s to %s", icon_name, icon_path)
        return icon_data
    except Exception as exc:
        logger.debug("Failed to fetch icon %s: %s", icon_name, exc)
        return None


def cleanup_unused_icons(used_icon_names: set[str], old_icon_names: set[str]):
    """清理不再使用的图标本地缓存文件"""
    removed = old_icon_names - used_icon_names
    if not removed:
        return
    for icon_name in removed:
        # 跳过仍在使用的图标
        if icon_name in used_icon_names:
            continue
        # 从内存缓存移除
        _memory_cache.pop(icon_name, None)
        # 删除本地文件
        icon_path = _get_icon_path(icon_name)
        if icon_path.exists():
            try:
                icon_path.unlink()
                logger.debug("Removed unused icon cache: %s", icon_name)
            except OSError as exc:
                logger.debug("Failed to remove icon cache %s: %s", icon_name, exc)


def load_quick_tags_icon(icon_name: str) -> dict | None:
    """
    加载快捷标签图标数据（内存缓存 → 本地文件 → API 获取并缓存）
    返回 {body, width, height} 或 None
    """
    if not icon_name:
        return None
    # 内存缓存命中 → 直接返回（零 IO）
    if icon_name in _memory_cache:
        return _memory_cache[icon_name]
    # 本地文件命中 → 写入内存缓存
    cached = _read_from_disk(icon_name)
    if cached:
        _memory_cache[icon_name] = cached
        return cached
    # 从 API 获取 → 写入文件 + 内存缓存
    fetched = _fetch_and_cache_icon(icon_name)
    if fetched:
        _memory_cache[icon_name] = fetched
    return fetched
