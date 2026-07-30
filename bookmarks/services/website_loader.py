import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from http.cookies import SimpleCookie
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from charset_normalizer import from_bytes
from django.conf import settings
from django.utils import timezone

from bookmarks.utils import get_registrable_domain, load_module, search_config_for_domain

logger = logging.getLogger(__name__)

# Per-domain rate limiter for metadata requests (thread-safe)
_domain_last_request: dict[str, float] = {}
_domain_rate_lock = threading.Lock()
_DOMAIN_RATE_MAX_SIZE = 1000  # Prevent unbounded growth

_JSON_LD_SKIP_TYPES = frozenset({"WebSite", "Organization", "BreadcrumbList"})

# Selectors available in <head> section (found via streaming read)
_HEAD_TITLE_SELECTORS = [
    'meta[property="og:title"]',
    'title',
    'meta[name="twitter:title"]',
]

# Selectors that require reading <body> (only used as fallback)
_BODY_TITLE_SELECTORS = [
    'h1[class*="title"]',
    'h1[class*="Title"]',
    '.article-title',
    '.post-title',
    '.entry-title',
    '.ArticleTitle',
    '.post__title',
    'h1',
]


def _throttle_domain(domain: str):
    """Check per-domain rate limit and record the request atomically."""
    cooldown = settings.LD_METADATA_DOMAIN_COOLDOWN_SEC
    if cooldown <= 0:
        return
    wait = 0.0
    with _domain_rate_lock:
        # Prevent unbounded growth
        if len(_domain_last_request) >= _DOMAIN_RATE_MAX_SIZE:
            _domain_last_request.clear()
        now = time.monotonic()
        last = _domain_last_request.get(domain, 0)
        wait = cooldown - (now - last)
        if wait > 0:
            _domain_last_request[domain] = last + cooldown
        else:
            _domain_last_request[domain] = now
    if wait > 0:
        logger.debug('Rate limit: sleeping %.1fs for %s', wait, domain)
        time.sleep(wait)


class RetryableMetadataError(Exception):
    pass


class NonRetryableMetadataError(Exception):
    pass


@dataclass
class WebsiteMetadata:
    url: str
    title: str | None
    description: str | None
    preview_image: str | None

    def to_dict(self):
        return {
            "url": self.url,
            "title": self.title,
            "description": self.description,
            "preview_image": self.preview_image,
        }


# Cache for custom loader settings and modules
_settings_cache = None
_loaders_module_cache: dict[str, tuple] = {}


def _empty_metadata(url: str):
    return WebsiteMetadata(url=url, title=None, description=None, preview_image=None)


def _normalize_metadata_result(url: str, metadata, source: str):
    if isinstance(metadata, WebsiteMetadata):
        return metadata

    if metadata is None:
        logger.warning("Metadata loader returned no result. url=%s source=%s", url, source)
    else:
        logger.warning(
            "Metadata loader returned invalid result. url=%s source=%s type=%s",
            url, source, type(metadata).__name__,
        )

    return _empty_metadata(url)


def _call_metadata_loader(
    loader, url: str, config: dict = None, source: str = "default"
):
    try:
        metadata = loader(url, config)
    except RetryableMetadataError:
        raise
    except NonRetryableMetadataError as exc:
        logger.info(
            "Metadata request failed without retry. url=%s source=%s",
            exc_info=exc,
        )
        return _empty_metadata(url)
    except Exception as exc:
        logger.error(
            "Unexpected metadata request failure. url=%s source=%s",
            exc_info=exc,
        )
        return _empty_metadata(url)

    return _normalize_metadata_result(url, metadata, source)


_METADATA_MAX_RETRIES = 3
_METADATA_RETRY_BASE_DELAY = 1.0  # seconds, doubles each attempt


# Load website metadata, with optional custom loader config
def load_website_metadata(url: str, ignore_cache: bool = False):
    settings_path = settings.LD_CUSTOM_WEBSITE_LOADER_SETTINGS
    config = search_config_for_domain(url, settings_path, _settings_cache)

    if config:
        loader_file = config.get("loader")
        if loader_file:
            loader_path = (
                os.path.join(os.path.dirname(settings_path), loader_file)
                if loader_file
                else None
            )
            if loader_path and os.path.exists(loader_path):
                module = load_module(loader_path, _loaders_module_cache)
                func = module._load_website_metadata
                return _call_metadata_loader(func, url, config, source=loader_path)
        else:
            if ignore_cache:
                return _load_website_metadata(url, config)
            return _load_website_metadata_config_cached(url, _config_cache_key(config))

    if ignore_cache:
        return _load_website_metadata(url)
    return _load_website_metadata_cached(url)


def _config_cache_key(config: dict) -> str:
    return json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)


# Caching metadata avoids scraping again when saving bookmarks
@lru_cache(maxsize=10)
def _load_website_metadata_cached(url: str):
    return _load_website_metadata(url)


@lru_cache(maxsize=10)
def _load_website_metadata_config_cached(url: str, config_key: str):
    return _load_website_metadata(url, json.loads(config_key))


def _load_website_metadata(url: str, config: dict = None):
    fetch_url = config.get("_request_url", url) if config else url
    page_text = None
    last_exc = None

    for attempt in range(_METADATA_MAX_RETRIES + 1):
        try:
            start = timezone.now()
            page_text = load_page(fetch_url, config)
            end = timezone.now()
            logger.debug("Load duration: %s", end - start)
            last_exc = None
            break
        except RetryableMetadataError as exc:
            last_exc = exc
            if attempt < _METADATA_MAX_RETRIES:
                delay = _METADATA_RETRY_BASE_DELAY * (2 ** attempt)
                logger.info(
                    "Retryable error (attempt %d/%d), retrying in %.1fs. url=%s",
                    attempt + 1, _METADATA_MAX_RETRIES, delay, url,
                )
                time.sleep(delay)
            else:
                logger.warning(
                    "All %d retries exhausted. url=%s",
                    _METADATA_MAX_RETRIES, url,
                )
        except NonRetryableMetadataError as exc:
            logger.info("Metadata request failed without retry. url=%s", exc_info=exc)
            return _empty_metadata(url)
        except Exception as exc:
            logger.error("Unexpected metadata request failure. url=%s", exc_info=exc)
            return _empty_metadata(url)

    if last_exc is not None:
        logger.warning("All %d retries exhausted, returning empty metadata. url=%s", _METADATA_MAX_RETRIES, url)
        return _empty_metadata(url)

    try:
        start = timezone.now()
        soup = BeautifulSoup(page_text, "html.parser")
        title, description, preview_image = _parse_metadata_from_soup_head(soup, fetch_url, config)
        end = timezone.now()
        logger.debug("Parsing duration: %s", end - start)
    except Exception as exc:
        logger.error("Unexpected metadata parsing failure. url=%s", exc_info=exc)
        return _empty_metadata(url)

    # Fallback: if title not found in head, try loading full page for body selectors
    if title is None:
        try:
            logger.debug("Title not found in head, loading full page for body: %s", url)
            full_page_text = load_full_page(fetch_url, config)
            full_soup = BeautifulSoup(full_page_text, "html.parser")
            body_title, body_desc, body_image = _parse_metadata_from_soup_body(full_soup, fetch_url, config)
            title = body_title
            # Only use body results if head didn't provide them
            if description is None:
                description = body_desc
            if preview_image is None:
                preview_image = body_image
        except Exception as exc:
            logger.debug("Full page fallback failed for %s: %s", url, exc)

    return WebsiteMetadata(
        url=(config.get("_rewrite_url") if config else None) or url,
        title=title,
        description=description,
        preview_image=preview_image,
    )


def _extract_json_ld(soup) -> dict:
    """Extract metadata from application/ld+json script tags.
    Returns dict with optional keys: title, description, image.
    """
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string
        if not raw or not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            items = [data] + (data.get("@graph") if isinstance(data.get("@graph"), list) else [])
        elif isinstance(data, list):
            items = data
        else:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = item.get("@type", "")
            if isinstance(item_type, list):
                type_set = set(item_type)
            else:
                type_set = {item_type} if isinstance(item_type, str) else set()
            if type_set & _JSON_LD_SKIP_TYPES:
                continue
            result = {}
            item_title = item.get("headline") or item.get("name")
            if item_title and isinstance(item_title, str):
                result["title"] = item_title.strip()
            desc = item.get("description")
            if desc and isinstance(desc, str):
                result["description"] = desc.strip()
            img = item.get("image")
            if img:
                if isinstance(img, str):
                    result["image"] = img.strip()
                elif isinstance(img, dict):
                    url_val = img.get("url")
                    if url_val and isinstance(url_val, str):
                        result["image"] = url_val.strip()
                elif isinstance(img, list) and img:
                    first = img[0]
                    if isinstance(first, str):
                        result["image"] = first.strip()
                    elif isinstance(first, dict):
                        url_val = first.get("url")
                        if url_val and isinstance(url_val, str):
                            result["image"] = url_val.strip()
            if result:
                return result
    return {}


def _extract_with_selector_source(soup, selectors, url: str = "", field: str = ""):
    """Try CSS selectors in order, return the first matched value or None."""
    if isinstance(selectors, str):
        selectors = [selectors]
    for selector in selectors or []:
        if not selector or not selector.strip():
            continue
        try:
            el = soup.select_one(selector)
        except (ValueError, SyntaxError):
            # Invalid CSS selector syntax - skip silently
            continue
        if not el:
            continue
        value = None
        if el.name == "meta":
            value = el.get("content")
        elif field == "image":
            value = el.get("src") or el.get("href") or el.get("content")
        else:
            value = el.get("content") or el.get_text(" ", strip=True)
        if value:
            value = urljoin(url, value.strip()) if field == "image" else value.strip()
            return value
    return None


def _parse_metadata_from_soup_head(soup, url: str, config: dict | None = None):
    """Extract metadata from <head> section only (fast, streaming read)."""
    json_ld = None
    title_selectors = config.get("select_title") if config else None
    desc_selectors = config.get("select_description") if config else None
    image_selectors = config.get("select_image") if config else None
    has_custom_title = config is not None and "select_title" in config
    has_custom_desc = config is not None and "select_description" in config
    has_custom_image = config is not None and "select_image" in config

    # --- Title ---
    title = _extract_with_selector_source(soup, title_selectors or [], url, "title")
    if title is None and not has_custom_title:
        title = _extract_with_selector_source(soup, _HEAD_TITLE_SELECTORS, url, "title")
    if title is None:
        json_ld = _extract_json_ld(soup)
        title = json_ld.get("title")

    # --- Description ---
    description = _extract_with_selector_source(soup, desc_selectors or [], url, "description")
    if description is None and not has_custom_desc:
        description = _find_meta_description(soup)
    if description is None:
        if json_ld is None:
            json_ld = _extract_json_ld(soup)
        description = json_ld.get("description")

    # --- Preview Image ---
    preview_image = _extract_with_selector_source(soup, image_selectors or [], url, "image")
    if preview_image is None and not has_custom_image:
        preview_image = _find_meta_image(soup)
    if preview_image is None:
        if json_ld is None:
            json_ld = _extract_json_ld(soup)
        preview_image = json_ld.get("image")
    if preview_image is None:
        image_tag_link = soup.find("link", attrs={"rel": "preload", "as": "image"})
        if image_tag_link:
            preview_image = image_tag_link["href"].strip()

    if preview_image and not preview_image.startswith(("http://", "https://")):
        preview_image = urljoin(url, preview_image)

    return title, description, preview_image


def _find_meta_description(soup):
    """Find description from meta tags (standard, og, twitter)."""
    desc_tag = soup.find("meta", attrs={"name": "description"})
    if desc_tag and desc_tag.get("content"):
        return desc_tag["content"].strip()
    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"):
        return og_desc["content"].strip()
    tw_desc = soup.find("meta", attrs={"name": "twitter:description"})
    if tw_desc and tw_desc.get("content"):
        return tw_desc["content"].strip()
    return None


def _find_meta_image(soup):
    """Find image from meta tags (og, twitter)."""
    og_image = soup.find("meta", attrs={"property": "og:image"}) or soup.find(
        "meta", attrs={"name": "og:image"}
    )
    if og_image and og_image.get("content"):
        return og_image["content"].strip()
    tw_image = soup.find("meta", attrs={"name": "twitter:image"}) or soup.find(
        "meta", attrs={"property": "twitter:image"}
    )
    if tw_image and tw_image.get("content"):
        return tw_image["content"].strip()
    return None


def _parse_metadata_from_soup_body(soup, url: str, config: dict | None = None):
    """Extract metadata from <body> section (full page read required)."""
    json_ld = None
    title_selectors = config.get("select_title") if config else None

    # --- Title from body selectors (custom or defaults) ---
    if title_selectors:
        title = _extract_with_selector_source(soup, title_selectors, url, "title")
    else:
        title = _extract_with_selector_source(soup, _BODY_TITLE_SELECTORS, url, "title")
    if title is None:
        json_ld = _extract_json_ld(soup)
        title = json_ld.get("title")

    # --- Description (JSON-LD only, since head selectors should have been tried) ---
    description = None
    if json_ld is None:
        json_ld = _extract_json_ld(soup)
    description = json_ld.get("description")
    if not description:
        tw = soup.find("meta", attrs={"name": "twitter:description"})
        description = tw["content"].strip() if tw and tw.get("content") else None

    # --- Image (JSON-LD only) ---
    preview_image = None
    if json_ld:
        preview_image = json_ld.get("image")
    if not preview_image:
        tw = soup.find("meta", attrs={"name": "twitter:image"}) or soup.find(
            "meta", attrs={"property": "twitter:image"}
        )
        preview_image = tw["content"].strip() if tw and tw.get("content") else None

    if preview_image and not preview_image.startswith(("http://", "https://")):
        preview_image = urljoin(url, preview_image)

    return title, description, preview_image


def load_page(url: str, config: dict = None):
    # Per-domain rate limiting
    domain = get_registrable_domain(url)
    if domain:
        _throttle_domain(domain)

    headers = build_request_headers(config)
    cookies = build_request_cookies(config)
    timeout = config.get("timeout", 10) if config else 10
    proxies = config.get("proxy") if config else None

    CHUNK_SIZE = config.get("chunk_size", 50 * 1024) if config else 50 * 1024
    MAX_CONTENT_LIMIT = (
        config.get("max_content_limit", 5000 * 1024) if config else 5000 * 1024
    )

    size = 0
    content = None
    iteration = 0
    try:
        with requests.get(
            url,
            timeout=timeout,
            headers=headers,
            cookies=cookies,
            proxies=proxies,
            stream=True,
        ) as r:
            status_code = r.status_code
            if status_code == 429 or status_code >= 500:
                raise RetryableMetadataError(
                    f"Retryable metadata response: {status_code}"
                )
            if status_code >= 400:
                raise NonRetryableMetadataError(
                    f"Non-retryable metadata response: {status_code}"
                )

            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                size += len(chunk)
                iteration = iteration + 1
                content = chunk if content is None else content + chunk

                logger.debug(
                    "Loaded chunk (iteration=%d, total=%.1fKB)", iteration, size / 1024
                )

                # Stop reading if we have parsed end of head tag
                end_of_head = b"</head>"
                if end_of_head in content:
                    logger.debug("Found closing head tag after %d bytes", size)
                    content = content.split(end_of_head)[0] + end_of_head
                    break
                # Stop reading if we exceed limit
                if size > MAX_CONTENT_LIMIT:
                    logger.debug("Cancel reading document after %d bytes", size)
                    break
    except (RetryableMetadataError, NonRetryableMetadataError):
        raise
    except requests.exceptions.RequestException as exc:
        raise RetryableMetadataError(
            f"Retryable metadata request failure for {url}"
        ) from exc

    if not content:
        return ""

    # Use charset_normalizer to determine encoding
    results = from_bytes(content)
    best = results.best()
    return str(best) if best is not None else ""


_FULL_PAGE_MAX_SIZE = 20 * 1024 * 1024  # 20MB safeguard


def load_full_page(url: str, config: dict = None):
    """Download full page content for reader mode."""
    # Per-domain rate limiting
    domain = get_registrable_domain(url)
    if domain:
        _throttle_domain(domain)

    headers = build_request_headers(config)
    cookies = build_request_cookies(config)
    timeout = config.get("timeout", 30) if config else 30
    proxies = config.get("proxy") if config else None

    try:
        response = requests.get(
            url, timeout=timeout, headers=headers, cookies=cookies, proxies=proxies
        )
        response.raise_for_status()
        # Fix encoding: let requests detect actual encoding instead of relying
        # on potentially incorrect Content-Type header (common with Chinese sites)
        if response.encoding and response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding
        content = response.text
        if len(content) > _FULL_PAGE_MAX_SIZE:
            logger.warning("Full page content truncated from %d to %d bytes: %s", len(content), _FULL_PAGE_MAX_SIZE, url)
            content = content[:_FULL_PAGE_MAX_SIZE]
        return content
    except requests.exceptions.RequestException as exc:
        logger.error("Failed to load page %s: %s", url, exc)
        raise


def get_request_config(url: str) -> dict | None:
    settings_path = settings.LD_CUSTOM_WEBSITE_LOADER_SETTINGS
    if not settings_path or not os.path.exists(settings_path):
        return None
    return search_config_for_domain(url, settings_path, _settings_cache)


def detect_content_type(
    url: str, config: dict | None = None, timeout: int = 10
) -> str | None:
    request_config = config if config is not None else get_request_config(url)
    request_timeout = (
        request_config.get("timeout", timeout) if request_config else timeout
    )
    request_kwargs = {
        "allow_redirects": True,
        "cookies": build_request_cookies(request_config),
        "headers": build_request_headers(request_config),
        "timeout": request_timeout,
    }
    proxies = request_config.get("proxy") if request_config else None
    if proxies:
        request_kwargs["proxies"] = proxies

    try:
        response = requests.head(url, **request_kwargs)
        if response.status_code == 200:
            return (
                response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            )
    except requests.RequestException:
        pass

    try:
        with requests.get(url, stream=True, **request_kwargs) as response:
            if response.status_code == 200:
                return (
                    response.headers.get("Content-Type", "")
                    .split(";")[0]
                    .strip()
                    .lower()
                )
    except requests.RequestException:
        pass

    return None


def is_pdf_content_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    return content_type in ("application/pdf", "application/x-pdf")


def build_request_headers(config: dict = None):
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml",
        "Accept-Encoding": "gzip, deflate",
        "Dnt": "1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": settings.LD_DEFAULT_USER_AGENT,
    }
    if config and config.get("headers"):
        headers.update(config["headers"])
        if config.get("headers", {}).get("Cookie"):
            headers.pop("Cookie", None)
    return headers


def build_request_cookies(config: dict = None) -> dict:
    cookies = {}
    cookies_str = config.get("headers", {}).get("Cookie") if config else None
    if cookies_str:
        try:
            simple_cookie = SimpleCookie()
            simple_cookie.load(cookies_str)
            cookies = {key: value.value for key, value in simple_cookie.items()}
        except Exception:
            logger.warning("Failed to parse cookies for config")
            return cookies
    return cookies
