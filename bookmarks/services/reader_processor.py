import logging
import tempfile
import os

from site_adapters.services.config.resolver import get_reader_config

logger = logging.getLogger(__name__)


def _extract_defuddle_options(config: dict) -> dict:
    """Extract defuddle options from config."""
    from site_adapters.services.config.validator import is_known_defuddle_param
    args = config.get("defuddle_args", {})
    return {k: v for k, v in args.items() if is_known_defuddle_param(k)}


def parse_html(html_content: str, url: str = "", username: str = "") -> dict:
    """
    Extract content from HTML.

    Dispatch logic:
    1. defuddle_args defined -> defuddle with options
    2. No config -> default defuddle
    """
    from bookmarks.services import defuddle

    config = get_reader_config(url, username=username)

    if config:
        defuddle_opts = _extract_defuddle_options(config)
        if defuddle_opts:
            return _parse_html_with_options(html_content, url, defuddle_opts)

    return defuddle.parse_html(html_content, url=url)


def parse_url(url: str, username: str = "") -> dict:
    """
    Extract content from URL.

    Dispatch logic:
    1. defuddle_args defined -> defuddle with options
    2. No config -> default defuddle
    """
    from bookmarks.services import defuddle

    config = get_reader_config(url, username=username)

    if config:
        defuddle_opts = _extract_defuddle_options(config)
        if defuddle_opts:
            return _parse_url_with_options(url, defuddle_opts)

    return defuddle.parse_url(url)


def _parse_html_with_options(html_content: str, url: str, options: dict) -> dict:
    """Call defuddle module API via Node.js wrapper (supports contentSelector etc.)."""
    from bookmarks.services.defuddle import _inject_base_tag, _run_defuddle

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
    """Call defuddle module API directly via Node.js wrapper."""
    from bookmarks.services.defuddle import _run_defuddle

    return _run_defuddle({"url": url}, options=options)
