import logging
import os
import shlex
import signal
import subprocess
from contextlib import suppress

from django.conf import settings


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
        for arg, value in custom_options.items():
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


def create_snapshot(url: str, filepath: str, config: dict = None):
    singlefile_path = settings.LD_SINGLEFILE_PATH

    # Build options from lowest to highest priority
    custom_options = get_custom_options(config)
    global_options = shlex.split(settings.LD_SINGLEFILE_OPTIONS)
    ublock_options = shlex.split(settings.LD_SINGLEFILE_UBLOCK_OPTIONS)
    required_options = [
        "--browser-arg=--disable-blink-features=AutomationControlled",
        f"--user-agent={settings.LD_DEFAULT_USER_AGENT}",
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
        """Merge merged_options into target_options.
        Higher-priority calls override same-name args from earlier calls.
        Multi-value args accumulate across levels."""
        for opt in merged_options:
            arg_name = opt.split("=", 1)[0]
            if arg_name in multi_value_arg_list:
                if opt not in target_options:
                    target_options.append(opt)
            else:
                for i, existing in enumerate(target_options):
                    if existing.split("=", 1)[0] == arg_name:
                        target_options[i] = opt
                        break
                else:
                    target_options.append(opt)

    # Process from lowest to highest priority
    result_options = []
    merge_option(result_options, required_options)
    merge_option(result_options, ublock_options)
    merge_option(result_options, global_options)
    merge_option(result_options, custom_options)

    args = [singlefile_path] + result_options + [url, filepath]

    logger.debug("SingleFile full args: %s", args)

    process = None
    try:
        process = subprocess.Popen(args, start_new_session=True)
        process.wait(timeout=settings.LD_SINGLEFILE_TIMEOUT_SEC)

        if not os.path.exists(filepath):
            raise SingleFileError("Failed to create snapshot")
    except subprocess.TimeoutExpired:
        try:
            logger.error("Timeout expired while creating snapshot. Terminating process...")
            process.terminate()
            process.wait(timeout=20)
            raise SingleFileError("Timeout expired while creating snapshot") from None
        except subprocess.TimeoutExpired:
            logger.error("Timeout expired while terminating. Killing process group...")
            with suppress(OSError):
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            raise SingleFileError("Timeout expired while creating snapshot") from None
    except subprocess.CalledProcessError as error:
        raise SingleFileError(f"Failed to create snapshot: {error.stderr}") from None
    except OSError as error:
        raise SingleFileError(f"Failed to start single-file: {error}") from None
