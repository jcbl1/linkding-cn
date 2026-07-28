"""
Shared script runner for user-defined scripts (Python / JavaScript).

All user scripts receive a sanitized config (no _ prefixed keys).
Python: def extract(url, config, ...) 
JavaScript: stdin {"url", "config", ...}, stdout JSON
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import suppress

from site_adapters.services.execution_log import log_execution
from site_adapters.services.subscriptions import is_allowed_script_path

logger = logging.getLogger(__name__)


def run_script(script_path: str, *, url: str = '', config: dict = None,
               html_content: str = None, output_path: str = None,
               timeout: int = 30) -> dict | str | None:
    """
    Run a user script (JS or Python) and return the result.
    
    Args:
        script_path: Path to .js or .py script
        url: URL being processed
        config: Merged config dict (keys starting with _ are stripped)
        html_content: HTML content string (written to temp file for JS)
        output_path: Output file path (passed to script)
        timeout: Subprocess timeout in seconds
    
    Returns:
        dict (parsed JSON), str (raw output), or None on failure
    """
    if not script_path or not os.path.exists(script_path):
        logger.error("Script not found: %s", script_path)
        return None
    if not (script_path.endswith('.js') or script_path.endswith('.py')):
        logger.error("Unsupported script extension: %s", script_path)
        return None

    # Runtime defense: check script path is in allowed directory
    from django.conf import settings
    base_dir = getattr(settings, 'LD_SITE_ADAPTERS_DIR', '')
    if base_dir and not is_allowed_script_path(script_path, base_dir):
        logger.error("Script path not allowed: %s", script_path)
        return None

    script_config = {k: v for k, v in (config or {}).items() if not k.startswith('_')}

    if script_path.endswith('.js'):
        return _run_js(script_path, url, script_config, html_content, output_path, timeout)
    return _run_py(script_path, url, script_config, html_content, output_path)


def _run_js(script_path: str, url: str, config: dict,
            html_content: str, output_path: str, timeout: int) -> dict | str | None:
    payload = {"url": url, "config": config}
    tmp_path = None

    if html_content:
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
            tmp.write(html_content)
            tmp_path = tmp.name
        payload["html_path"] = tmp_path
    if output_path:
        payload["output_path"] = output_path

    start = time.monotonic()
    try:
        result = subprocess.run(
            ["node", script_path],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True, text=True, timeout=timeout,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        step = "metadata_script" if not output_path else "snapshot_script"
        log_execution(
            url=url, domain_key="", step=step,
            cmd=["node", script_path], returncode=result.returncode,
            stdout=result.stdout[:500], stderr=result.stderr[:500],
            duration_ms=duration_ms,
        )
        if result.returncode != 0:
            logger.error("JS script failed: %s stderr=%s", script_path, result.stderr)
            return None
        stdout = result.stdout.strip()
        if not stdout:
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return stdout
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - start) * 1000)
        step = "metadata_script" if not output_path else "snapshot_script"
        log_execution(url=url, domain_key="", step=step,
                      cmd=["node", script_path], returncode=-1,
                      stderr="Timeout", duration_ms=duration_ms)
        logger.error("JS script timeout: %s", script_path)
        return None
    finally:
        if tmp_path:
            with suppress(OSError):
                os.unlink(tmp_path)


def _run_py(script_path: str, url: str, config: dict,
            html_content: str, output_path: str, timeout: int = 30) -> dict | str | None:
    """Run a Python script in a worker thread with timeout.

    Note: Python threads cannot be forcibly killed. On timeout, the worker thread
    continues running as a daemon thread (will exit when the process exits).
    This is a known limitation — user scripts should be designed to complete promptly.

    Uses subprocess for hard timeout only when DJANGO_SETTINGS_MODULE is available
    and the script doesn't import Django internals. Falls back to threading otherwise.
    """
    import importlib.util

    def _execute():
        spec = importlib.util.spec_from_file_location("_user_script", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if hasattr(module, 'extract'):
            kwargs = {}
            if html_content is not None:
                kwargs['html_content'] = html_content
            if output_path is not None:
                kwargs['output_path'] = output_path
            return module.extract(url, config, **kwargs)

        # Legacy fallbacks
        if hasattr(module, '_load_website_metadata'):
            return module._load_website_metadata(url, config)
        if html_content and hasattr(module, '_parse_html'):
            return module._parse_html(html_content, url, config)
        if hasattr(module, '_parse_url'):
            return module._parse_url(url, config)
        if hasattr(module, '_create_snapshot'):
            module._create_snapshot(url, output_path, config)
            return None

        raise AttributeError("Script missing extract function")

    result_box = []
    error_box = []

    def _target():
        try:
            result_box.append(_execute())
        except Exception as exc:
            error_box.append(exc)

    start = time.monotonic()
    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    duration_ms = int((time.monotonic() - start) * 1000)

    if thread.is_alive():
        step = "metadata_script" if not output_path else "snapshot_script"
        log_execution(url=url, domain_key="", step=step,
                      cmd=["python", script_path], returncode=-1,
                      stderr="Timeout (%ds) — daemon thread still running" % timeout,
                      duration_ms=duration_ms)
        logger.error("Python script timeout: %s (%ds). "
                     "Thread continues as daemon — consider optimizing the script.",
                     script_path, timeout)
        return None

    if error_box:
        exc = error_box[0]
        step = "metadata_script" if not output_path else "snapshot_script"
        log_execution(url=url, domain_key="", step=step,
                      cmd=["python", script_path], returncode=1,
                      stderr=str(exc)[:500], duration_ms=duration_ms)
        logger.error("Python script error: %s %s", script_path, exc)
        return None

    return result_box[0] if result_box else None