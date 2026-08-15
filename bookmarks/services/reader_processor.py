import logging
import re

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


def _collect_carousels(html_content: str) -> list[str]:
    """Extract processed carousels from normalized snapshot HTML."""
    if not html_content or "ld-carousel" not in html_content:
        return []
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_content, "html.parser")
    return [
        str(figure)
        for figure in soup.find_all("figure", attrs={"aria-label": "ld-carousel"})
    ]


def _carousel_is_before_text(html_content: str) -> bool:
    """Return whether a carousel appears before the first paragraph in the source."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_content, "html.parser")
    carousel = soup.find("figure", attrs={"aria-label": "ld-carousel"})
    paragraph = next(
        (
            item
            for item in soup.find_all("p")
            if len(item.get_text(" ", strip=True)) >= 80
        ),
        None,
    ) or soup.find("p")
    if not carousel or not paragraph:
        return False

    serialized = str(soup)
    carousel_index = serialized.find('aria-label="ld-carousel"')
    paragraph_index = serialized.find(str(paragraph))
    return (
        carousel_index >= 0
        and paragraph_index >= 0
        and carousel_index < paragraph_index
    )


def _restore_missing_carousels(
    html_content: str, extracted_content: str, carousels: list[str]
) -> str:
    """Restore processed carousels that site-specific defuddle extractors omit."""
    if not carousels or "ld-carousel" in extracted_content:
        return extracted_content

    before = [
        carousel
        for carousel in carousels
        if _carousel_is_before_text(html_content)
    ]
    after = [
        carousel for carousel in carousels if carousel not in before
    ]

    if before:
        fragment = "".join(before)
        article_match = re.search(r"<article\b[^>]*>", extracted_content, re.I)
        if article_match:
            extracted_content = (
                extracted_content[: article_match.end()]
                + fragment
                + extracted_content[article_match.end() :]
            )
        else:
            extracted_content = fragment + extracted_content

    if after:
        fragment = "".join(after)
        article_close = re.search(r"</article>", extracted_content, re.I)
        if article_close:
            extracted_content = (
                extracted_content[: article_close.start()]
                + fragment
                + extracted_content[article_close.start() :]
            )
        else:
            extracted_content = extracted_content + fragment

    return extracted_content


def parse_html(html_content: str, url: str = "", username: str = "") -> dict:
    """
    Extract content from HTML.

    Dispatch logic:
    1. defuddle_args defined -> defuddle with options
    2. No config -> default defuddle
    """
    from bookmarks.services import defuddle

    html_content = _normalize_html_for_reader(html_content)
    carousels = _collect_carousels(html_content)
    config = get_reader_config(url, username=username)

    defuddle_opts = _extract_defuddle_options(config) if config else {}

    result = defuddle.parse_html(
        html_content, url=url, options=defuddle_opts or None
    )
    if result.get("content"):
        result["content"] = _restore_missing_carousels(
            html_content, result["content"], carousels
        )
    return result


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
