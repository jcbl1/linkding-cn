import logging
import os

from bookmarks.services import singlefile
from site_adapters.services.engine.script_runner import run_script
from site_adapters.services.auth.cookies import (
    load_cookie_file,
    verify_and_refresh,
)
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
    if not cookie_config or not cookie_config.get("file") or config.get("_user_cookie"):
        return False
    domain_key = config.get("_domain_key")
    before = load_cookie_file(cookie_config.get("file"))
    after = verify_and_refresh(
        cookie_config,
        config.get("_request_url", url),
        domain_key,
        {"url": config.get("_request_url", url), "html_path": filepath},
    )
    return bool(after and after != before)


def create_snapshot(url: str, filepath: str, username: str = ''):
    config = get_snapshot_config(url, username=username)
    _run_snapshot(url, filepath, config)
    if config and _verify_snapshot_cookie(url, filepath, config):
        _run_snapshot(url, filepath, config)
    return None


def _create_snapshot(url: str, filepath, config: dict = None):
    return singlefile.create_snapshot(url, filepath, config)
