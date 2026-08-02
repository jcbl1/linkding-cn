"""
Browser fallback — 浏览器兜底模式

当没有域名配置匹配时，使用浏览器加载页面并提取元数据。
默认关闭，通过 LD_BROWSER_FALLBACK_ENABLED=true 启用。

引擎由 LD_BROWSER_ENGINE 决定（构建期已固化），不再运行时回退。

资源控制：
- 按需启动浏览器实例（不常驻）
- 单次请求超时 LD_BROWSER_FALLBACK_TIMEOUT 秒
- LD_BROWSER_FALLBACK_MAX_CONCURRENT 控制并发
"""

import logging
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

_semaphore: threading.Semaphore | None = None


def _get_semaphore() -> threading.Semaphore:
    global _semaphore
    if _semaphore is None:
        max_concurrent = getattr(settings, 'LD_BROWSER_FALLBACK_MAX_CONCURRENT', 2)
        _semaphore = threading.Semaphore(max_concurrent)
    return _semaphore


def is_enabled() -> bool:
    return getattr(settings, 'LD_BROWSER_FALLBACK_ENABLED', False)


def load_metadata_via_browser(url: str, username: str = '') -> dict | None:
    """
    使用浏览器加载页面，用默认规则提取元数据。

    返回 {'title': ..., 'description': ..., 'preview_image': ...} 或 None。
    """
    if not is_enabled():
        return None

    sem = _get_semaphore()
    if not sem.acquire(timeout=5):
        logger.warning("Browser fallback: max concurrent reached, skipping %s", url)
        return None

    try:
        return _do_load(url, username)
    finally:
        sem.release()


def _do_load(url: str, username: str) -> dict | None:
    from site_adapters.services.engine.browser_provider import launch_browser

    timeout_ms = getattr(settings, 'LD_BROWSER_FALLBACK_TIMEOUT', 30) * 1000

    storage_state = None
    if username:
        storage_state = _get_storage_state(username, url)

    browser = None
    try:
        browser = launch_browser(headless=True)
        context_opts = {}
        if storage_state:
            context_opts['storage_state'] = storage_state
        context = browser.new_context(**context_opts)
        page = context.new_page()
        page.goto(url, timeout=timeout_ms, wait_until='domcontentloaded')

        title = page.title() or None

        description = None
        preview_image = None

        desc_el = page.query_selector('meta[property="og:description"], meta[name="description"]')
        if desc_el:
            description = desc_el.get_attribute('content')

        img_el = page.query_selector('meta[property="og:image"]')
        if img_el:
            preview_image = img_el.get_attribute('content')

        return {
            'title': title,
            'description': description,
            'preview_image': preview_image,
        }
    except Exception as e:
        logger.error("Browser fallback failed: %s: %s", url, e)
        return None
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass


def _get_storage_state(username: str, url: str) -> dict | None:
    """尝试获取用户的 Playwright storage state。"""
    from urllib.parse import urlparse

    from site_adapters.services.auth.cookies import cookie_string_to_playwright_list
    from site_adapters.services.auth.credentials import get_user_cookie

    domain = urlparse(url).netloc
    cookie_str, status = get_user_cookie(username, domain)
    if not cookie_str or status != 'ok':
        return None

    cookies = cookie_string_to_playwright_list(cookie_str, domain)

    return {
        'cookies': cookies,
        'origins': [],
    }
