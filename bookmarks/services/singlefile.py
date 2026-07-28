import json
import logging
import os
import shlex
import signal
import subprocess
import tempfile
import time
from contextlib import suppress

from django.conf import settings

from site_adapters.services.auth.cookies import (
    generate_temp_cookies_file,
    load_cookie_file,
)
from site_adapters.services.execution_log import log_execution


class SingleFileError(Exception):
    pass


logger = logging.getLogger(__name__)


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
            elif value is False or value is None:
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


def _build_browser_script(config: dict) -> str | None:
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
    preamble = "window.__linkding_cleanup_config = " + json.dumps(cleanup) + ";\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tmp:
        tmp.write(preamble + script)
        return tmp.name


def _build_site_adapter_options(config: dict) -> tuple[list[str], list[str]]:
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
    cookie_config = config.get("cookie", {})
    cookie_file = cookie_config.get("file") if cookie_config else None
    if user_cookie:
        cookie_file = generate_temp_cookies_file(config.get("_domain_key", ""), cookie_str=user_cookie)
        if cookie_file:
            temp_files.append(cookie_file)
    elif cookie_file:
        cookie_str = load_cookie_file(cookie_file)
        if cookie_str:
            cookie_file = generate_temp_cookies_file(config.get("_domain_key", ""), cookie_str=cookie_str)
            if cookie_file:
                temp_files.append(cookie_file)
        else:
            cookie_file = None
    if not cookie_file and config.get("_domain_key"):
        cookie_file = generate_temp_cookies_file(config["_domain_key"])
        if cookie_file:
            temp_files.append(cookie_file)
    if cookie_file:
        options.append(f"--browser-cookies-file={cookie_file}")
    browser_script = _build_browser_script(config)
    if browser_script:
        options.append(f"--browser-script={browser_script}")
        temp_files.append(browser_script)
    return options, temp_files


def create_snapshot(url: str, filepath: str, config: dict = None):
    singlefile_path = settings.LD_SINGLEFILE_PATH

    custom_options = get_custom_options(config)
    injected_options, temp_files = _build_site_adapter_options(config)
    global_options = shlex.split(settings.LD_SINGLEFILE_OPTIONS)
    ublock_options = shlex.split(settings.LD_SINGLEFILE_UBLOCK_OPTIONS)
    required_options = [
        "--browser-arg=--disable-blink-features=AutomationControlled",
        f"--user-agent={settings.LD_DEFAULT_USER_AGENT}",
    ]

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

    snapshot_url = config.get("_request_url", url) if config else url
    args = [singlefile_path] + result_options + [snapshot_url, filepath]

    logger.debug("SingleFile full args: %s", args)

    start = time.monotonic()
    process = None
    try:
        with suppress(OSError):
            os.remove(filepath)
        # Use start_new_session=True to create a new process group
        process = subprocess.Popen(args, start_new_session=True)
        process.wait(timeout=settings.LD_SINGLEFILE_TIMEOUT_SEC)

        # check if the file was created
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
