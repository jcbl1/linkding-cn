import json
import logging
import os
import re
import shlex
import signal
import subprocess
import tempfile
import time
from contextlib import suppress

from django.conf import settings

from site_adapters.services.auth.credentials import get_shared_cookie
from site_adapters.services.auth.cookies import (
    copy_cookie_file_to_temp,
    generate_temp_cookies_file,
)
from site_adapters.services.execution_log import log_execution


class SingleFileError(Exception):
    pass


logger = logging.getLogger(__name__)


def _resolve_browser_path() -> str | None:
    """当 LD_BROWSER_ENGINE=cloakbrowser 时，解析 cloakbrowser 二进制路径。

    返回 None 表示让 SingleFile 按默认行为从 PATH 寻找 chromium。
    放在 required_options（最低优先级），用户可通过 LD_SINGLEFILE_OPTIONS 显式覆盖。
    """
    engine = getattr(settings, 'LD_BROWSER_ENGINE', 'chromium')
    if engine != 'cloakbrowser':
        return None
    try:
        from cloakbrowser import ensure_binary
        return ensure_binary()
    except ImportError:
        logger.warning(
            "LD_BROWSER_ENGINE=cloakbrowser but cloakbrowser package not installed. "
            "Install with: pip install cloakbrowser && python -m cloakbrowser install. "
            "Falling back to system chromium."
        )
        return None


def get_custom_options(config: dict):
    if config:
        custom_options = config.get("singlefile_args")
    else:
        logger.debug("No config provided")
        return []

    if not custom_options:
        logger.debug("No singlefile_args provided")
        return []

    args = []

    if isinstance(custom_options, dict):
        from site_adapters.services.config.validator import is_known_singlefile_arg
        for arg, value in custom_options.items():
            if not is_known_singlefile_arg(arg):
                logger.warning("Ignoring unknown SingleFile arg: %s", arg)
                continue
            if value is True:
                args.append(arg)
            elif value is False:
                continue
            elif value is None:
                continue
            elif isinstance(value, list):
                args.extend(f"{arg}={item}" for item in value)
            else:
                args.append(f"{arg}={value}")
    else:
        logger.error("singlefile_args must be a dict, got %s", type(custom_options).__name__)
        return []

    logger.debug("SingleFile custom args: %s", args)
    return args


def _as_list(value):
    if not value:
        return []
    return value if isinstance(value, list) else [value]


_BUILTIN_ENGINE_RE = re.compile(
    r'^[ \t]*(?:const|let)\s+builtin_engine\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(null))\s*;?',
    re.MULTILINE,
)


def read_builtin_engine(script_path: str) -> str | None:
    """Read the builtin_engine declaration from a snapshot JS script."""
    with open(script_path, encoding='utf-8') as f:
        source = f.read()
    match = _BUILTIN_ENGINE_RE.search(source)
    if not match:
        raise SingleFileError(
            f"Snapshot JS script must declare builtin_engine: {script_path}"
        )
    if match.group(3) is not None:
        return None
    return match.group(1) if match.group(1) is not None else match.group(2)


def uses_builtin_engine(script_path: str, hook_name: str = '') -> bool:
    """Return whether a snapshot JS before/after hook runs inside SingleFile."""
    if not script_path.endswith('.js') or hook_name not in ('before', 'after'):
        return False
    engine = read_builtin_engine(script_path)
    if engine == 'singlefile':
        return True
    if engine in ('', None):
        return False
    raise SingleFileError(
        f"Unsupported builtin_engine value {engine!r} in {script_path}"
    )


_BROWSER_HOOK_BOILERPLATE = r"""
(() => {
  dispatchEvent(new CustomEvent("single-file-user-script-init"));

  const runHook = async (name, event, responseEvent) => {
    event.preventDefault();
    try {
      for (const hook of window.__linkdingHooks || []) {
        const fn = hook[name];
        if (typeof fn === "function") {
          await fn(
            window.__linkding_snapshot_config.url,
            window.__linkding_snapshot_config.config
          );
        }
      }
    } finally {
      dispatchEvent(new CustomEvent(responseEvent));
    }
  };

  addEventListener(
    "single-file-on-before-capture-request",
    (event) => runHook("before", event, "single-file-on-before-capture-response")
  );
  addEventListener(
    "single-file-on-after-capture-request",
    (event) => runHook("after", event, "single-file-on-after-capture-response")
  );
})();
"""


def _wrap_user_hook_script(source: str, include_before: bool, include_after: bool) -> str:
    checks = []
    if include_before:
        checks.append(
            "if (typeof before === 'function') "
            "window.__linkdingHooks.push({ before: before, after: undefined });"
        )
    if include_after:
        checks.append(
            "if (typeof after === 'function') "
            "window.__linkdingHooks.push({ before: undefined, after: after });"
        )
    return "(() => {\n" + source + "\n" + "\n".join(checks) + "\n})();\n"


def _build_browser_script(config: dict, url: str = '') -> str | None:
    if not config:
        # No config at all — still enable default lazy image fix
        cleanup = {"keep": [], "remove": [], "lazy": True, "removeClasses": {}, "setStyles": {}}
    else:
        lazy = config.get("process_lazy_images")
        # process_lazy_images: true → default attrs; ["data-actualsrc", ...] → custom attrs
        # When not specified, default to True (always fix lazy images)
        if isinstance(lazy, list):
            lazy_config = lazy
        elif lazy is not None:
            lazy_config = bool(lazy)
        else:
            lazy_config = True  # Default: always fix lazy images
        cleanup = {
            "keep": _as_list(config.get("keep_elements")),
            "remove": _as_list(config.get("remove_elements")),
            "lazy": lazy_config,
            "removeClasses": config.get("remove_classes") or {},
            "setStyles": config.get("set_styles") or {},
        }
    import site_adapters.services as _sa_services; vendor_path = os.path.join(os.path.dirname(_sa_services.__file__), 'engine', 'scripts', 'snapshot_browser_script.js')
    with open(vendor_path, encoding='utf-8') as f:
        script = f.read()

    parts = []
    before_paths = _as_list(config.get('_browser_before_scripts'))
    after_paths = _as_list(config.get('_browser_after_scripts'))

    if before_paths or after_paths:
        from site_adapters.services.engine.script_runner import _sanitize_config
        injected_url = url or config.get('_request_url') or config.get('_url') or ''
        parts.append(
            "window.__linkding_snapshot_config = "
            + json.dumps(
                {"url": injected_url, "config": _sanitize_config(config)},
                ensure_ascii=False,
            )
            + ";\n"
        )
        parts.append("window.__linkdingHooks = [];\n")
        for script_path in before_paths:
            with open(script_path, encoding='utf-8') as f:
                source = f.read()
            parts.append(_wrap_user_hook_script(source, include_before=True, include_after=False))
        for script_path in after_paths:
            with open(script_path, encoding='utf-8') as f:
                source = f.read()
            parts.append(_wrap_user_hook_script(source, include_before=False, include_after=True))
        parts.append(_BROWSER_HOOK_BOILERPLATE)

    preamble = "window.__linkding_cleanup_config = " + json.dumps(cleanup) + ";\n"
    parts.append(preamble + script)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tmp:
        tmp.write("".join(parts))
        return tmp.name


def _build_site_adapter_options(url: str, config: dict) -> tuple[list[str], list[str]]:
    if not config:
        return [], []
    options = []
    temp_files = []
    headers = config.get("headers") or {}
    for name, value in headers.items():
        if value is None or name.lower() == "cookie":
            continue
        if name.lower() == "user-agent":
            options.append(f"--user-agent={value}")
        else:
            options.append(f"--http-header={name}: {value}")
    if config.get("proxy"):
        options.append(f"--http-proxy-server={config['proxy']}")
    user_cookie = config.get("_user_cookie")
    cookie_file = None
    if user_cookie:
        cookie_file = generate_temp_cookies_file(config.get("_domain_key", ""), cookie_str=user_cookie)
        if cookie_file:
            temp_files.append(cookie_file)
    if not cookie_file and config.get("_domain_key"):
        domain_key = config["_domain_key"]
        best, _ = get_shared_cookie(domain_key)
        if best:
            cookie_file = generate_temp_cookies_file(domain_key, cookie_str=best)
            if cookie_file:
                temp_files.append(cookie_file)
    if cookie_file:
        options.append(f"--browser-cookies-file={cookie_file}")
    browser_script = _build_browser_script(config, url=url)
    if browser_script:
        options.append(f"--browser-script={browser_script}")
        temp_files.append(browser_script)
    return options, temp_files


def create_snapshot(url: str, filepath: str, config: dict = None):
    singlefile_path = settings.LD_SINGLEFILE_PATH

    custom_options = get_custom_options(config)
    injected_options, temp_files = _build_site_adapter_options(url, config)
    global_options = shlex.split(settings.LD_SINGLEFILE_OPTIONS)
    ublock_options = shlex.split(settings.LD_SINGLEFILE_UBLOCK_OPTIONS)
    required_options = [
        "--browser-arg=--disable-blink-features=AutomationControlled",
        f"--user-agent={settings.LD_DEFAULT_USER_AGENT}",
        "--block-fonts=true",
    ]
    # 自动解析 cloakbrowser 路径（最低优先级，允许显式覆盖）
    browser_path = _resolve_browser_path()
    if browser_path:
        required_options.append(f"--browser-executable-path={browser_path}")

    # Args that allow multiple values (not deduplicated by name)
    multi_value_arg_list = [
        "--browser-script",
        "--browser-stylesheet",
        "--browser-arg",
        "--browser-cookie",
        "--crawl-rewrite-rule",
        "--emulate-media-feature",
        "--http-header",
    ]

    def merge_option(target_options, merged_options):
        """Merge merged_options into target_options (lowest priority first).
        Higher-priority calls override same-name args from earlier calls.
        Multi-value args (e.g. --browser-arg) accumulate across levels."""
        for opt in merged_options:
            arg_name = opt.split("=", 1)[0]
            if arg_name in multi_value_arg_list:
                if opt not in target_options:
                    target_options.append(opt)
            else:
                # Find and replace existing same-name arg, or append
                for i, existing in enumerate(target_options):
                    if existing.split("=", 1)[0] == arg_name:
                        target_options[i] = opt
                        break
                else:
                    target_options.append(opt)

    # Process from lowest to highest priority; later appends override earlier ones
    result_options = []
    merge_option(result_options, required_options)
    merge_option(result_options, ublock_options)
    merge_option(result_options, global_options)
    merge_option(result_options, injected_options)
    merge_option(result_options, custom_options)

    # If before hook provided HTML, use it as the capture target
    before_html = config.get("_before_html_path") if config else None
    snapshot_url = before_html if before_html else (config.get("_request_url", url) if config else url)
    args = [singlefile_path] + result_options + [snapshot_url, filepath]

    logger.debug("SingleFile full args: %s", args)

    start = time.monotonic()
    process = None
    try:
        with suppress(OSError):
            os.remove(filepath)
        # Use start_new_session=True to create a new process group
        process = subprocess.Popen(args, start_new_session=True)
        process.wait(timeout=(config.get("timeout") if config else None) or settings.LD_SINGLEFILE_TIMEOUT_SEC)

        if not os.path.exists(filepath):
            raise SingleFileError("Failed to create snapshot")
        log_execution(
            url=snapshot_url,
            domain_key=(config or {}).get("_domain_key", ""),
            step="snapshot",
            cmd=args,
            returncode=process.returncode if process and process.returncode is not None else 0,
            duration_ms=int((time.monotonic() - start) * 1000),
            config_snapshot=config,
        )
    except subprocess.TimeoutExpired:
        log_execution(
            url=snapshot_url,
            domain_key=(config or {}).get("_domain_key", ""),
            step="snapshot",
            cmd=args,
            returncode=-1,
            stderr="Timeout expired while creating snapshot",
            duration_ms=int((time.monotonic() - start) * 1000),
            config_snapshot=config,
        )
        # First try to terminate properly
        try:
            logger.error("Timeout expired while creating snapshot. Terminating process...")
            process.terminate()
            process.wait(timeout=20)
            raise SingleFileError("Timeout expired while creating snapshot") from None
        except subprocess.TimeoutExpired:
            # Kill the whole process group, which should also clean up any chromium
            # processes spawned by single-file
            logger.error("Timeout expired while terminating. Killing process group...")
            if process:
                with suppress(OSError):
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            raise SingleFileError("Timeout expired while creating snapshot") from None
    except OSError as error:
        log_execution(
            url=snapshot_url,
            domain_key=(config or {}).get("_domain_key", ""),
            step="snapshot",
            cmd=args,
            returncode=1,
            stderr=str(error),
            duration_ms=int((time.monotonic() - start) * 1000),
            config_snapshot=config,
        )
        raise SingleFileError(f"Failed to start single-file: {error}") from None
    except SingleFileError as error:
        log_execution(
            url=snapshot_url,
            domain_key=(config or {}).get("_domain_key", ""),
            step="snapshot",
            cmd=args,
            returncode=process.returncode if process and process.returncode is not None else 1,
            stderr=str(error),
            duration_ms=int((time.monotonic() - start) * 1000),
            config_snapshot=config,
        )
        raise
    finally:
        for temp_file in temp_files:
            with suppress(OSError):
                os.unlink(temp_file)
