import logging
import os

from django.conf import settings

from bookmarks.utils import load_module, search_config_for_domain

logger = logging.getLogger(__name__)

# 缓存规则设置与解析规则（function）
_settings_cache = None
_processors_module_cache = {}  # {processor_path: (module, mtime)}

# DefuddleOptions 中可通过 Node.js 包装脚本传递的选项
_DEFUDDLE_OPTION_KEYS = {
    "contentSelector",
    "removeExactSelectors",
    "removePartialSelectors",
    "removeHiddenElements",
    "removeLowScoring",
    "removeSmallImages",
    "removeImages",
    "standardize",
    "removeContentPatterns",
    "includeReplies",
    "useAsync",
}


def _resolve_config(url: str):
    """查找域名配置，返回 (config, settings_path) 或 (None, settings_path)。"""
    settings_path = settings.LD_CUSTOM_READER_PROCESSOR_SETTINGS
    config = search_config_for_domain(url, settings_path, _settings_cache)
    return config, settings_path


def _try_custom_processor(config: dict, settings_path: str, func_name: str, *args):
    """
    尝试加载自定义 processor 模块并调用指定函数。
    返回结果 dict，或 None（未配置 processor 或文件不存在）。
    """
    processor_file = config.get("processor")
    if not processor_file:
        return None

    processor_path = os.path.join(os.path.dirname(settings_path), processor_file)
    if not os.path.exists(processor_path):
        logger.error(f"Custom reader processor not found: {processor_path}")
        return None

    module = load_module(processor_path, _processors_module_cache)
    func = getattr(module, func_name, None)
    if func is None:
        logger.error(f"Custom reader processor missing function: {func_name}")
        return None

    return func(*args, config)


def _extract_defuddle_options(config: dict) -> dict:
    """从配置中提取 defuddle 选项。"""
    return {k: v for k, v in config.items() if k in _DEFUDDLE_OPTION_KEYS}


def parse_html(html_content: str, url: str = "") -> dict:
    """
    从 HTML 内容中提取正文。

    调度逻辑：
    1. 有 processor 字段 → 自定义 Python 模块
    2. 有 defuddle 选项（如 contentSelector）→ Node.js 包装脚本
    3. 无配置 → defuddle CLI（默认，行为不变）
    """
    from bookmarks.services import defuddle

    config, settings_path = _resolve_config(url)

    if config:
        # 1. 自定义 processor 模块
        result = _try_custom_processor(
            config, settings_path, "_parse_html", html_content, url
        )
        if result is not None:
            return result

        # 2. 有 defuddle 选项 → Node.js 包装脚本
        defuddle_opts = _extract_defuddle_options(config)
        if defuddle_opts:
            return _parse_html_with_options(html_content, url, defuddle_opts)

    # 3. 默认：defuddle CLI
    return defuddle.parse_html(html_content, url=url)


def parse_url(url: str) -> dict:
    """
    从 URL 直接提取正文。

    调度逻辑同 parse_html。
    """
    from bookmarks.services import defuddle

    config, settings_path = _resolve_config(url)

    if config:
        result = _try_custom_processor(config, settings_path, "_parse_url", url)
        if result is not None:
            return result

        defuddle_opts = _extract_defuddle_options(config)
        if defuddle_opts:
            return _parse_url_with_options(url, defuddle_opts)

    return defuddle.parse_url(url)


def _parse_html_with_options(html_content: str, url: str, options: dict) -> dict:
    """通过 Node.js 包装脚本调用 defuddle 模块 API（支持 contentSelector 等）。"""
    from bookmarks.services.defuddle import _inject_base_tag, _run_defuddle
    import tempfile

    html_content = _inject_base_tag(html_content, url)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(html_content)
        tmp_path = tmp.name

    try:
        return _run_defuddle({"htmlPath": tmp_path, "url": url}, options=options, timeout=30)
    finally:
        os.unlink(tmp_path)


def _parse_url_with_options(url: str, options: dict) -> dict:
    """通过 Node.js 包装脚本直接解析 URL（支持 contentSelector 等）。"""
    from bookmarks.services.defuddle import _run_defuddle

    return _run_defuddle({"url": url}, options=options)
