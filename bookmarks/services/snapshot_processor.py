import logging
import os
from contextlib import suppress

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
        scripts = config.get("scripts")
        if scripts:
            return _run_snapshot_with_hooks(url, filepath, config, scripts)

        script_path = config.get("script")
        if script_path:
            if os.path.exists(script_path):
                return run_script(script_path, url=url, config=config, output_path=filepath)
            logger.error("Snapshot script not found: %s", script_path)
        return _create_snapshot(url, filepath, config)
    return _create_snapshot(url, filepath, None)


def _run_snapshot_with_hooks(url: str, filepath: str, config: dict, scripts: list):
    """Execute snapshot pipeline with hook scripts.

    Order: external before hooks → [replace or SingleFile with browser hooks]
           → external after hooks
    """
    import tempfile
    before_html_path = None
    external_before = []
    external_after = []
    replace_scripts = []
    browser_before = []
    browser_after = []

    for entry in scripts:
        script_path = entry.get('path', '')
        if not script_path or not os.path.exists(script_path):
            continue
        hook = entry.get('hook')
        if hook == 'replace':
            replace_scripts.append(script_path)
        elif hook == 'before':
            if script_path.endswith('.js') and singlefile.uses_builtin_engine(script_path, 'before'):
                browser_before.append(script_path)
            else:
                external_before.append(entry)
        elif hook == 'after':
            if script_path.endswith('.js') and singlefile.uses_builtin_engine(script_path, 'after'):
                browser_after.append(script_path)
            else:
                external_after.append(entry)

    # 1. Run external before hooks
    for entry in external_before:
        script_path = entry.get('path', '')
        logger.debug("Running snapshot before hook: %s", script_path)
        result = run_script(script_path, hook_name='before', url=url, config=dict(config))
        if isinstance(result, str):
            # before hook returned HTML — save to temp file for downstream
            with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
                tmp.write(result)
            before_html_path = tmp.name
            logger.debug("Before hook returned HTML, saved to: %s", before_html_path)

    # 2. Run replace hook or built-in engine
    if replace_scripts:
        if browser_before or browser_after:
            logger.warning(
                "Snapshot SingleFile browser hooks are ignored when a replace hook is present"
            )
        for script_path in replace_scripts:
            logger.debug("Running snapshot replace hook: %s", script_path)
            run_script(script_path, hook_name='replace', url=url,
                       config=dict(config), output_path=filepath)
            break  # Only one replace allowed
    else:
        # Built-in engine: SingleFile
        config_copy = dict(config)
        if browser_before:
            config_copy['_browser_before_scripts'] = browser_before
        if browser_after:
            config_copy['_browser_after_scripts'] = browser_after
        if before_html_path:
            config_copy['_before_html_path'] = before_html_path
            _create_snapshot(url, filepath, config_copy)
        else:
            _create_snapshot(url, filepath, config_copy)

    # 3. Run external after hooks
    for entry in external_after:
        script_path = entry.get('path', '')
        logger.debug("Running snapshot after hook: %s", script_path)
        run_script(script_path, hook_name='after', output_path=filepath,
                   config=dict(config))

    # Cleanup temp file
    if before_html_path:
        with suppress(OSError):
            os.unlink(before_html_path)



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
    # Pre-flight: for auto-type cookie sites without cookies, acquire via browser first.
    try:
        cookie_config = config.get("cookie") if config else {}
        if cookie_config and cookie_config.get("type") == "auto":
            has_cookie = bool(
                config.get("_user_cookie") or
                (get_shared_cookie(config.get("_domain_key", ""))[0] if config.get("_domain_key") else None)
            )
            if not has_cookie and cookie_config.get("refresh"):
                domain_key = config.get("_domain_key")
                logger.info("No cookie for auto site %s, acquiring via browser refresh", domain_key)
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
