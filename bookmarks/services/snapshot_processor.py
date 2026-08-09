import logging
import os

from bookmarks.services import singlefile
from site_adapters.services.engine.script_runner import run_script
from site_adapters.services.auth.cookies import (
    verify_and_refresh,
)
from site_adapters.services.auth.credentials import get_shared_cookie
from site_adapters.services.config.resolver import get_snapshot_config

logger = logging.getLogger(__name__)




def _run_snapshot(url: str, filepath: str, config: dict | None):
    if config:
        script_path = config.get("script")
        if script_path:
            if os.path.exists(script_path):
                return run_script(script_path, url=url, config=config, output_path=filepath)
            logger.error("Snapshot script not found: %s", script_path)
        return _create_snapshot(url, filepath, config)
    return _create_snapshot(url, filepath, None)


def _verify_snapshot_cookie(url: str, filepath: str, config: dict) -> bool:
    cookie_config = config.get("cookie", {})
    if not cookie_config:
        return False
    domain_key = config.get("_domain_key")
    before = _cookie_string_from_config(config)
    after = verify_and_refresh(
        cookie_config,
        config.get("_request_url", url),
        domain_key,
        {"url": config.get("_request_url", url), "html_path": filepath},
    )
    if after and after != before:
        # 刷新成功后更新 _user_cookie 为新的 cookie 字符串
        config["_user_cookie"] = after
        return True
    return False


def _cookie_string_from_config(config: dict = None) -> str | None:
    if not config:
        return None
    user_cookie = config.get("_user_cookie")
    if user_cookie:
        return user_cookie
    domain_key = config.get("_domain_key")
    if domain_key:
        shared, _ = get_shared_cookie(domain_key)
        if shared:
            return shared
    return config.get("headers", {}).get("Cookie")


def create_snapshot(url: str, filepath: str, username: str = ''):
    config = get_snapshot_config(url, username=username)
    # Pre-flight: for anon-type cookie sites without cookies, acquire via browser first.
    try:
        cookie_config = config.get("cookie") if config else {}
        if cookie_config and cookie_config.get("type") == "anon":
            has_cookie = bool(
                config.get("_user_cookie") or
                (get_shared_cookie(config.get("_domain_key", ""))[0] if config.get("_domain_key") else None)
            )
            if not has_cookie and cookie_config.get("refresh"):
                domain_key = config.get("_domain_key")
                logger.info("No cookie for anon site %s, acquiring via browser refresh", domain_key)
                new_cookie = verify_and_refresh(
                    cookie_config,
                    config.get("_request_url", url),
                    domain_key,
                    {"url": config.get("_request_url", url), "title": "", "body_preview": ""},
                )
                if new_cookie:
                    config["_user_cookie"] = new_cookie
                    logger.info("Pre-flight acquired cookie for %s, proceeding with snapshot", domain_key)
                else:
                    logger.warning("Pre-flight failed to acquire cookie for %s", domain_key)
    except Exception as e:
        logger.error("Pre-flight cookie acquisition error for %s: %s", url, e)
    _run_snapshot(url, filepath, config)
    if config and _verify_snapshot_cookie(url, filepath, config):
        _run_snapshot(url, filepath, config)
    return None


def _create_snapshot(url: str, filepath, config: dict = None):
    return singlefile.create_snapshot(url, filepath, config)
