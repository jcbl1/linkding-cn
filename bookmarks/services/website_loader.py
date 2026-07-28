import json
import logging
import os
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

from site_adapters.services.auth.cookies import (
    load_cookie_file,
    verify_and_refresh,
)
from site_adapters.services.auth.cookies import get_cookie_for_domain
from site_adapters.services.execution_log import log_execution
from site_adapters.services.config.resolver import get_metadata_config
from site_adapters.services.engine.script_runner import run_script
from site_adapters.services.engine.browser_fallback import load_metadata_via_browser
from bookmarks.utils import get_registrable_domain

logger = logging.getLogger(__name__)

# Per-domain rate limiter for metadata requests
_domain_last_request: dict[str, float] = {}

_JSON_LD_SKIP_TYPES = frozenset({"WebSite", "Organization", "BreadcrumbList"})

# Default title selectors derived from fivefilters/ftr-site-config patterns (2034 domains).
# Tried in order when no domain-specific config provides select_title.
_DEFAULT_TITLE_SELECTORS = [
    'meta[property="og:title"]',
    'h1[class*="title"]',
    'h1[class*="Title"]',
    '.article-title',
    '.post-title',
    '.entry-title',
    '.ArticleTitle',
    '.post__title',
    'h1',
]


def _wait_for_domain(domain: str):
    cooldown = settings.LD_METADATA_DOMAIN_COOLDOWN_SEC
    if cooldown <= 0:
        return
    now = time.monotonic()
    last = _domain_last_request.get(domain, 0)
    wait = cooldown - (now - last)
    if wait > 0:
        logger.debug('Rate limit: sleeping %.1fs for %s', wait, domain)
        time.sleep(wait)


def _record_domain_request(domain: str):
    _domain_last_request[domain] = time.monotonic()


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


def _metadata_config_cache_key(config: dict) -> str:
    return json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)


def load_website_metadata(url: str, ignore_cache: bool = False, username: str = ''):
    config = get_metadata_config(url, username=username)

    if config:
        loader_file = config.get("script")
        if loader_file:
            loader_path = loader_file  # site_adapters engine resolved to absolute path
            if loader_path and os.path.exists(loader_path):
                body = load_page(url, config)
                if loader_path.endswith(".js"):
                    result = run_script(loader_path, url=url, config=config, html_content=body)
                    if result and isinstance(result, dict):
                        return WebsiteMetadata(
                            url=result.get('url') or url,
                            title=result.get('title'),
                            description=result.get('description'),
                            preview_image=result.get('preview_image'),
                        )
                    return _empty_metadata(url)
                result = run_script(loader_path, url=url, config=config, html_content=body)
                if result:
                    return _normalize_metadata_result(url, result, source=loader_path)
                return _empty_metadata(url)
        else:
            if ignore_cache:
                return _load_website_metadata(url, config, username=username)
            return _load_website_metadata_config_cached(
                url, _metadata_config_cache_key(config), username=username
            )

    if ignore_cache:
        result = _load_website_metadata(url, username=username)
    else:
        result = _load_website_metadata_cached(url)

    # Browser fallback: when no config matched and default extraction got nothing useful
    if result and not result.title:
        browser_result = load_metadata_via_browser(url, username=username)
        if browser_result and browser_result.get('title'):
            return WebsiteMetadata(
                url=url,
                title=browser_result.get('title'),
                description=browser_result.get('description'),
                preview_image=browser_result.get('preview_image'),
            )

    return result


# Caching metadata avoids scraping again when saving bookmarks, in case the
# metadata was already scraped to show preview values in the bookmark form
@lru_cache(maxsize=10)
def _load_website_metadata_cached(url: str, username: str = ''):
    return _load_website_metadata(url, username=username)


@lru_cache(maxsize=10)
def _load_website_metadata_config_cached(url: str, config_key: str, username: str = ''):
    return _load_website_metadata(url, json.loads(config_key), username=username)


_METADATA_MAX_RETRIES = 3
_METADATA_RETRY_BASE_DELAY = 1.0  # seconds, doubles each attempt


def _load_website_metadata(url: str, config: dict = None, username: str = ''):
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
            logger.info("Metadata request failed without retry. url=%s", url, exc_info=exc)
            return _empty_metadata(url)
        except Exception as exc:
            logger.error("Unexpected metadata request failure. url=%s", url, exc_info=exc)
            return _empty_metadata(url)

    if last_exc is not None:
        raise last_exc

    try:
        start = timezone.now()
        soup = BeautifulSoup(page_text, "html.parser")
        title, description, preview_image = _parse_metadata_from_soup(
            soup, fetch_url, config
        )

        cookie_config = config.get("cookie") if config else {}
        if cookie_config and cookie_config.get("file") and not config.get("_user_cookie"):
            domain_key = config.get("_domain_key")
            verify_context = {
                "url": fetch_url,
                "status": 200,
                "title": title or "",
                "body_preview": (page_text or "")[:2000],
            }
            before = _cookie_string_from_config(config)
            after = verify_and_refresh(cookie_config, fetch_url, domain_key, verify_context)
            if after and after != before:
                retry_config = dict(config)
                page_text = load_page(fetch_url, retry_config)
                soup = BeautifulSoup(page_text, "html.parser")
                title, description, preview_image = _parse_metadata_from_soup(
                    soup, fetch_url, retry_config
                )

        end = timezone.now()
        logger.debug("Parsing duration: %s", end - start)
    except Exception as exc:
        logger.error("Unexpected metadata parsing failure. url=%s", url, exc_info=exc)
        return _empty_metadata(url)

    return WebsiteMetadata(
        url=(config.get("_rewrite_url") if config else None) or url,
        title=title,
        description=description,
        preview_image=preview_image,
    )


def _extract_json_ld(soup) -> dict:
    """Extract metadata from the first application/ld+json script tag.
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
        # Normalise to a list of objects
        if isinstance(data, dict):
            items = [data] + (data.get("@graph") if isinstance(data.get("@graph"), list) else [])
        elif isinstance(data, list):
            items = data
        else:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            # Skip non-content types
            item_type = item.get("@type", "")
            if isinstance(item_type, list):
                type_set = set(item_type)
            else:
                type_set = {item_type} if isinstance(item_type, str) else set()
            if type_set & _JSON_LD_SKIP_TYPES:
                continue
            result = {}
            # title
            title = item.get("headline") or item.get("name")
            if title and isinstance(title, str):
                result["title"] = title.strip()
            # description
            desc = item.get("description")
            if desc and isinstance(desc, str):
                result["description"] = desc.strip()
            # image
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


def _parse_metadata_from_soup(soup, url: str, config: dict | None = None, include_sources: bool = False):
    sources = {}

    # Pre-extract JSON-LD once (shared across title/desc/image fallbacks)
    json_ld = None

    title_selectors = config.get("select_title") if config else None
    title_explicit = config is not None and "select_title" in config
    title, source = _extract_with_selector_source(
        soup, title_selectors or [], url, "title"
    )
    if title is None and not title_explicit:
        # Enhanced fallback chain (fivefilters-informed):
        # og:title → h1[class*=title] → .article-title/.post-title/.entry-title → h1 → <title> → twitter:title → JSON-LD
        title, source = _extract_with_selector_source(
            soup, _DEFAULT_TITLE_SELECTORS, url, "title"
        )
        if not title:
            if soup.title and soup.title.string:
                title = soup.title.string.strip()
                source = "title"
        if not title:
            tw = soup.find("meta", attrs={"name": "twitter:title"})
            title = tw["content"].strip() if tw and tw.get("content") else None
            source = "meta[name=twitter:title]" if title else None
        if not title:
            if json_ld is None:
                json_ld = _extract_json_ld(soup)
            title = json_ld.get("title")
            source = "json-ld" if title else None
    sources["title"] = {"value": title, "selector": source}

    desc_selectors = config.get("select_description") if config else None
    desc_explicit = config is not None and "select_description" in config
    description, source = _extract_with_selector_source(
        soup, desc_selectors or [], url, "description"
    )
    if description is None and not desc_explicit:
        # Enhanced fallback: og:description → meta[name=description] → twitter:description → JSON-LD
        og_desc = soup.find("meta", attrs={"property": "og:description"})
        description = og_desc["content"].strip() if og_desc and og_desc.get("content") else None
        source = "meta[property=og:description]" if description else None
        if not description:
            description_tag = soup.find("meta", attrs={"name": "description"})
            description = description_tag["content"].strip() if description_tag and description_tag.get("content") else None
            source = "meta[name=description]" if description else None
        if not description:
            tw = soup.find("meta", attrs={"name": "twitter:description"})
            description = tw["content"].strip() if tw and tw.get("content") else None
            source = "meta[name=twitter:description]" if description else None
        if not description:
            if json_ld is None:
                json_ld = _extract_json_ld(soup)
            description = json_ld.get("description")
            source = "json-ld" if description else None
    sources["description"] = {"value": description, "selector": source}

    image_selectors = config.get("select_image") if config else None
    image_explicit = config is not None and "select_image" in config
    preview_image, source = _extract_with_selector_source(
        soup, image_selectors or [], url, "image"
    )
    if preview_image is None and not image_explicit:
        # Fallback: og:image → twitter:image → JSON-LD → link[rel=preload]
        image_tag_meta = soup.find("meta", attrs={"property": "og:image"}) or soup.find(
            "meta", attrs={"name": "og:image"}
        )
        if image_tag_meta:
            preview_image = image_tag_meta["content"].strip()
            source = "meta[property=og:image]"
        if not preview_image:
            tw = soup.find("meta", attrs={"name": "twitter:image"}) or soup.find(
                "meta", attrs={"property": "twitter:image"}
            )
            preview_image = tw["content"].strip() if tw and tw.get("content") else None
            source = "meta[name=twitter:image]" if preview_image else None
        if not preview_image:
            if json_ld is None:
                json_ld = _extract_json_ld(soup)
            preview_image = json_ld.get("image")
            source = "json-ld" if preview_image else None
        if not preview_image:
            image_tag_link = soup.find("link", attrs={"rel": "preload", "as": "image"})
            if image_tag_link:
                preview_image = image_tag_link["href"].strip()
                source = "link[rel=preload][as=image]"

    if (
        preview_image
        and not preview_image.startswith("http://")
        and not preview_image.startswith("https://")
    ):
        preview_image = urljoin(url, preview_image)
    sources["preview_image"] = {"value": preview_image, "selector": source}

    if include_sources:
        return title, description, preview_image, sources
    return title, description, preview_image


def _extract_with_selector_source(soup, selectors, url: str = "", field: str = ""):
    if isinstance(selectors, str):
        selectors = [selectors]
    for selector in selectors or []:
        if not selector or not selector.strip():
            continue
        try:
            el = soup.select_one(selector)
        except Exception:
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
            return value, selector
    return None, None


def load_website_metadata_for_test(url: str, username: str = ''):
    config = get_metadata_config(url, username=username)
    if config and config.get("script"):
        script_path = config["script"]
        body = load_page(config.get("_request_url", url), config)
        result = run_script(script_path, url=url, config=config, html_content=body)
        if result and isinstance(result, dict):
            metadata = WebsiteMetadata(
                url=result.get('url') or url,
                title=result.get('title'),
                description=result.get('description'),
                preview_image=result.get('preview_image'),
            )
        else:
            metadata = _empty_metadata(url)
        return metadata, {"script": script_path}, config

    fetch_url = config.get("_request_url", url) if config else url
    page_text = load_page(fetch_url, config)
    soup = BeautifulSoup(page_text, "html.parser")
    title, description, preview_image, sources = _parse_metadata_from_soup(
        soup, fetch_url, config, include_sources=True
    )
    metadata = WebsiteMetadata(
        url=(config.get("_rewrite_url") if config else None) or url,
        title=title,
        description=description,
        preview_image=preview_image,
    )
    return metadata, sources, config


def load_page(url: str, config: dict = None):
    # Per-domain rate limiting
    domain = get_registrable_domain(url)
    if domain:
        _wait_for_domain(domain)

    headers = build_request_headers(config)
    cookies = build_request_cookies(config)
    timeout = config.get("timeout", 10) if config else 10
    proxies = config.get("proxy") if config else None

    # Build equivalent curl command for debugging
    curl_cmd = ['curl', '-sS', '-L', '--max-time', str(timeout)]
    for k, v in (headers or {}).items():
        curl_cmd += ['-H', f'{k}: {v}']
    for k, v in (cookies or {}).items():
        curl_cmd += ['-b', f'{k}={v}']
    curl_cmd.append(url)
    _page_start = time.monotonic()

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
                if domain:
                    _record_domain_request(domain)
                raise RetryableMetadataError(
                    f"Retryable metadata response: {status_code}"
                )
            if status_code >= 400:
                if domain:
                    _record_domain_request(domain)
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
        duration_ms = int((time.monotonic() - _page_start) * 1000)
        log_execution(url=url, domain_key="", step="metadata",
                      cmd=curl_cmd, returncode=1,
                      stderr=str(exc)[:500], duration_ms=duration_ms)
        if domain:
            _record_domain_request(domain)
        raise RetryableMetadataError(
            f"Retryable metadata request failure for {url}"
        ) from exc

    # Use charset_normalizer to determine encoding that best matches the response content.
    # Several sites specify the response encoding incorrectly, so we ignore it and use
    # custom logic instead of Response.text which respects the declared encoding first.
    results = from_bytes(content or "")
    duration_ms = int((time.monotonic() - _page_start) * 1000)
    log_execution(url=url, domain_key="", step="metadata",
                  cmd=curl_cmd, returncode=0, duration_ms=duration_ms)
    if domain:
        _record_domain_request(domain)
    return str(results.best())


def load_full_page(url: str, config: dict = None):
    """Download full page content for reader mode."""
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
        return response.text
    except requests.exceptions.RequestException:
        logger.error("Failed to load page: %s", url)
        raise


def get_request_config(url: str) -> dict | None:
    return get_metadata_config(url)


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
    return headers


def build_request_cookies(config: dict = None) -> dict:
    cookies = {}
    cookies_str = _cookie_string_from_config(config)
    if cookies_str:
        try:
            simple_cookie = SimpleCookie()
            simple_cookie.load(cookies_str)
            cookies = {key: value.value for key, value in simple_cookie.items()}
        except Exception:
            logger.warning("Failed to parse cookies for config")
            return cookies
    return cookies


def _cookie_string_from_config(config: dict = None) -> str | None:
    if not config:
        return None
    # Priority: user credentials > cookie file (shared) > Cookie header (http config) > shared cookie
    user_cookie = config.get("_user_cookie")
    if user_cookie:
        return user_cookie
    cookie_config = config.get("cookie", {})
    cookie_file = cookie_config.get("file") if cookie_config else None
    if cookie_file:
        return load_cookie_file(cookie_file)
    cookies_str = config.get("headers", {}).get("Cookie")
    if cookies_str:
        return cookies_str
    domain_key = config.get("_domain_key")
    if domain_key:
        return get_cookie_for_domain(domain_key)
    return None
