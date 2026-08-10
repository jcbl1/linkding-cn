"""
Shared script runner for user-defined scripts (Python / JavaScript).

Supports hook-based dispatch where the hook name (before/after/replace)
maps to the function name in the script file.

All user scripts receive a sanitized config where internal _-prefixed keys
are mapped to their user-facing names (e.g., _request_url → request_url).
"""

import importlib.util
import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from contextlib import suppress
from typing import Any

from site_adapters.services.execution_log import log_execution
from site_adapters.services.subscriptions import is_allowed_script_path

logger = logging.getLogger(__name__)

# Mapping from internal _ prefixed keys to user-facing names
_INTERNAL_TO_USER_KEY = {
    '_request_url': 'request_url',
    '_rewrite_url': 'rewrite_url',
    '_user_cookie': 'user_cookie',
    '_domain_key': 'domain_key',
}


def _sanitize_config(config: dict) -> dict:
    """Strip internal _ prefixed keys, map known internal keys to user-facing names."""
    result = {}
    for key, value in (config or {}).items():
        if key in _INTERNAL_TO_USER_KEY:
            result[_INTERNAL_TO_USER_KEY[key]] = value
        elif not key.startswith('_'):
            result[key] = value
    return result


def run_script(script_path: str, *, hook_name: str = None, url: str = '',
               config: dict = None, html_content: str = None,
               output_path: str = None, result_dict: dict = None,
               timeout: int = 30) -> Any:
    """
    Run a user script (JS or Python) and return the result.

    Args:
        script_path: Path to .js or .py script
        hook_name: Hook name (before/after/replace). Maps to function name in script.
        url: URL being processed
        config: Merged config dict (sanitized: _ prefixed keys mapped to user names)
        html_content: HTML content string
        output_path: Output file path
        result_dict: Result dict (for after hooks in metadata)
        timeout: Subprocess timeout in seconds

    Returns:
        For before hooks: None (or str for snapshot before returning HTML)
        For after hooks: None (modifies in place)
        For replace hooks: dict (metadata) or None (snapshot writes to file)
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

    script_config = _sanitize_config(config)

    if script_path.endswith('.js'):
        return _run_js(script_path, hook_name, url, script_config,
                       html_content, output_path, result_dict, timeout)
    return _run_py(script_path, hook_name, url, script_config,
                   html_content, output_path, result_dict, timeout)


def _run_js(script_path: str, hook_name: str, url: str, config: dict,
            html_content: str, output_path: str, result_dict: dict,
            timeout: int) -> Any:
    payload = {"url": url, "config": config, "hook": hook_name}
    tmp_path = None

    if html_content:
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
            tmp.write(html_content)
            tmp_path = tmp.name
        payload["html_path"] = tmp_path
    if output_path:
        payload["output_path"] = output_path
    if result_dict is not None:
        payload["result"] = result_dict

    start = time.monotonic()
    try:
        result = subprocess.run(
            ["node", script_path],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True, text=True, timeout=timeout,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        step = f"{hook_name}_hook" if hook_name else "script"
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
        step = f"{hook_name}_hook" if hook_name else "script"
        log_execution(url=url, domain_key="", step=step,
                      cmd=["node", script_path], returncode=-1,
                      stderr="Timeout", duration_ms=duration_ms)
        logger.error("JS script timeout: %s", script_path)
        return None
    finally:
        if tmp_path:
            with suppress(OSError):
                os.unlink(tmp_path)


def _run_py(script_path: str, hook_name: str, url: str, config: dict,
            html_content: str, output_path: str, result_dict: dict,
            timeout: int = 30) -> Any:
    """Run a Python script hook in a worker thread with timeout.

    Dispatches to the function named after hook_name (before/after/replace).
    Falls back to extract() for replace if the named function is absent.
    """
    def _execute():
        spec = importlib.util.spec_from_file_location("_user_script", script_path)
        module = importlib.util.module_from_spec(spec)
        loader = spec.loader
        if loader is None:
            raise ImportError(f"No loader for {script_path}")
        loader.exec_module(module)

        # Try hook-specific function first, then fallbacks
        target_fn = None
        if hook_name and hasattr(module, hook_name):
            target_fn = getattr(module, hook_name)
        elif hook_name == 'replace' and hasattr(module, 'extract'):
            # Legacy fallback: replace hooks can use extract()
            target_fn = getattr(module, 'extract')
        elif hasattr(module, 'extract'):
            target_fn = getattr(module, 'extract')

        if target_fn is None:
            raise AttributeError(
                f"Script {script_path} missing function '{hook_name}' "
                f"(or 'extract' for replace)"
            )

        # Call with appropriate kwargs based on hook type
        if hook_name == 'before':
            kwargs = {}
            if html_content is not None:
                kwargs['html_content'] = html_content
            if output_path is not None:
                kwargs['output_path'] = output_path
            return target_fn(url, config, **kwargs)
        elif hook_name == 'after':
            if result_dict is not None:
                # metadata after: result dict
                return target_fn(result_dict, url, config)
            else:
                # snapshot after: output_path
                return target_fn(output_path, config)
        elif hook_name == 'replace':
            kwargs = {}
            if html_content is not None:
                kwargs['html_content'] = html_content
            if output_path is not None:
                kwargs['output_path'] = output_path
            return target_fn(url, config, **kwargs)
        else:
            # generic fallback
            kwargs = {}
            if html_content is not None:
                kwargs['html_content'] = html_content
            if output_path is not None:
                kwargs['output_path'] = output_path
            if result_dict is not None:
                kwargs['result'] = result_dict
            return target_fn(url, config, **kwargs)

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
        step = f"{hook_name}_hook" if hook_name else "script"
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
        step = f"{hook_name}_hook" if hook_name else "script"
        log_execution(url=url, domain_key="", step=step,
                      cmd=["python", script_path], returncode=1,
                      stderr=str(exc)[:500], duration_ms=duration_ms)
        logger.error("Python script error: %s %s", script_path, exc)
        return None

    return result_box[0] if result_box else None
