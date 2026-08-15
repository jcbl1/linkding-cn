import logging

from site_adapters.services.config.resolver import get_reader_config

logger = logging.getLogger(__name__)


def _extract_defuddle_options(config: dict) -> dict:
    """Extract defuddle options from config."""
    from site_adapters.services.config.validator import is_known_defuddle_param
    args = config.get("defuddle_args", {})
    return {k: v for k, v in args.items() if is_known_defuddle_param(k)}


def _normalize_html_for_reader(html_content: str) -> str:
    """Unwrap serialized shadow DOM templates for reader extraction.

    The original snapshot is not modified; this function returns a temporary
    normalized copy that Defuddle can parse as ordinary HTML.
    """
    if not html_content:
        return html_content

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_content, "html.parser")
    for template in soup.find_all("template", attrs={"shadowrootmode": True}):
        for child in list(template.contents):
            template.insert_before(child)
        template.decompose()
    return str(soup)


def parse_html(html_content: str, url: str = "", username: str = "") -> dict:
    """
    Extract content from HTML.

    Dispatch logic:
    1. defuddle_args defined -> defuddle with options
    2. No config -> default defuddle
    """
    from bookmarks.services import defuddle

    html_content = _normalize_html_for_reader(html_content)
    config = get_reader_config(url, username=username)

    defuddle_opts = _extract_defuddle_options(config) if config else {}

    return defuddle.parse_html(html_content, url=url, options=defuddle_opts or None)


def parse_url(url: str, username: str = "") -> dict:
    """
    Extract content from URL.

    Dispatch logic:
    1. defuddle_args defined -> defuddle with options
    2. No config -> default defuddle
    """
    from bookmarks.services import defuddle

    config = get_reader_config(url, username=username)

    defuddle_opts = _extract_defuddle_options(config) if config else {}

    return defuddle.parse_url(url, options=defuddle_opts or None)
