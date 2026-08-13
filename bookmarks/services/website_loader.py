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

from site_adapters.services.auth.cookies import (
    verify_and_refresh,
)
from site_adapters.services.auth.credentials import get_shared_cookie
from site_adapters.services.execution_log import log_execution
from site_adapters.services.config import (
    apply_request_url,
    apply_rewrite,
    apply_rewrite_url,
)
from site_adapters.services.config.resolver import get_metadata_config
from site_adapters.services.engine.script_runner import run_script
from site_adapters.services.engine.browser_fallback import load_metadata_via_browser
from bookmarks.utils import get_registrable_domain

logger = logging.getLogger(__name__)

# Per-domain rate limiter for metadata requests
_domain_last_request: dict[str, float] = {}

_JSON_LD_SKIP_TYPES = frozenset({"WebSite", "Organization", "BreadcrumbList"})

_domain_rate_lock = threading.Lock()
_DOMAIN_RATE_MAX_SIZE = 1000  # Prevent unbounded growth


def _wait_for_domain(domain: str):
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


def _apply_metadata_before_result(url: str, config: dict, result) -> None:
    """Apply the partial config returned by a metadata before hook."""
    if not isinstance(result, dict):
        return
    for key, value in result.items():
        if key.startswith('_'):
            continue
        if key == 'request_url':
            config['request_url'] = value
            if isinstance(value, str) and value.startswith(('http://', 'https://')):
                config['_request_url'] = value
            else:
                resolved = apply_request_url(url, value)
                if resolved:
                    config['_request_url'] = resolved
        elif key == 'rewrite_url':
            config['rewrite_url'] = value
            resolved = apply_rewrite_url(url, value)
            if resolved:
                config['_rewrite_url'] = resolved
        elif key == 'user_cookie':
            config['_user_cookie'] = value
            config['user_cookie'] = value
        elif key == 'headers' and isinstance(value, dict):
            config.setdefault('headers', {}).update(value)
        else:
            config[key] = value


def _normalize_metadata_result(url: str, metadata, source: str):
    if isinstance(metadata, WebsiteMetadata):
        return metadata
    if isinstance(metadata, dict):
        return WebsiteMetadata(
            url=metadata.get('url') or url,
            title=metadata.get('title'),
            description=metadata.get('description'),
            preview_image=metadata.get('image') or metadata.get('preview_image'),
        )

    if metadata is None:
        logger.warning("Metadata loader returned no result. url=%s source=%s", url, source)
    else:
        logger.warning(
            "Metadata loader returned invalid result. url=%s source=%s type=%s",
            url, source, type(metadata).__name__,
        )

    return _empty_metadata(url)


def _load_with_hooks(url: str, config: dict, scripts: list, username: str = '',
                     ignore_cache: bool = False) -> WebsiteMetadata:
    """Execute metadata pipeline with hook scripts.

    Order: before hooks → [replace or built-in engine] → after hooks
    """
    # 1. Run before hooks
    for entry in scripts:
        if entry.get('hook') != 'before':
            continue
        script_path = entry.get('path', '')
        if not script_path or not os.path.exists(script_path):
            continue
        logger.debug("Running metadata before hook: %s", script_path)
        result = run_script(script_path, hook_name='before', url=url, config=dict(config))
        _apply_metadata_before_result(url, config, result)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("After before hook, config keys: %s", list(config.keys()))

    # 2. Run replace hook or built-in engine
    has_replace = any(e.get('hook') == 'replace' for e in scripts)

    if has_replace:
        for entry in scripts:
            if entry.get('hook') != 'replace':
                continue
            script_path = entry.get('path', '')
            if not script_path or not os.path.exists(script_path):
                continue
            logger.debug("Running metadata replace hook: %s", script_path)
            result = run_script(script_path, hook_name='replace', url=url, config=dict(config))
            if result is None:
                return _empty_metadata(url)
            if isinstance(result, dict):
                result['title'] = apply_rewrite(result.get('title'), config.get('rewrite_title'))
                result['description'] = apply_rewrite(result.get('description'), config.get('rewrite_description'))
                result['image'] = apply_rewrite(result.get('image'), config.get('rewrite_image'))
                metadata = _normalize_metadata_result(url, result, source=script_path)
            else:
                return _empty_metadata(url)
            break  # Only one replace allowed
    else:
        # Built-in engine: load page + parse
        if ignore_cache:
            metadata = _load_website_metadata(url, config, username=username)
        else:
            metadata = _load_website_metadata_config_cached(
                url, _metadata_config_cache_key(config), username=username
            )

    # 3. Run after hooks
    if metadata is None:
        metadata = _empty_metadata(url)

    result_dict = {
        'title': metadata.title,
        'description': metadata.description,
        'image': metadata.preview_image,
        'url': metadata.url,
    }

    for entry in scripts:
        if entry.get('hook') != 'after':
            continue
        script_path = entry.get('path', '')
        if not script_path or not os.path.exists(script_path):
            continue
        logger.debug("Running metadata after hook: %s", script_path)
        after_result = run_script(
            script_path,
            hook_name='after',
            url=url,
            config=dict(config),
            result_dict=result_dict,
        )
        if isinstance(after_result, dict):
            result_dict.update(after_result)

    return WebsiteMetadata(
        url=result_dict.get('url') or url,
        title=result_dict.get('title'),
        description=result_dict.get('description'),
        preview_image=result_dict.get('image'),
    )


def _metadata_config_cache_key(config: dict) -> str:
    return json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)


def load_website_metadata(url: str, ignore_cache: bool = False, username: str = ''):
    config = get_metadata_config(url, username=username)

    if config:
        scripts = config.get("scripts")
        if scripts:
            return _load_with_hooks(url, config, scripts, username=username, ignore_cache=ignore_cache)

        loader_file = config.get("script")
        if loader_file:
            loader_path = loader_file  # site_adapters engine resolved to absolute path
            if loader_path and os.path.exists(loader_path):
                load_full = config.get("load_full_page", True) if config else True
                body = load_page(url, config, load_full_page=load_full)
                if loader_path.endswith(".js"):
                    result = run_script(loader_path, url=url, config=config, html_content=body)
                    if result and isinstance(result, dict):
                        return WebsiteMetadata(
                            url=result.get('url') or url,
                            title=apply_rewrite(result.get('title'), config.get('rewrite_title')),
                            description=apply_rewrite(result.get('description'), config.get('rewrite_description')),
                            preview_image=apply_rewrite(
                                result.get('preview_image') or result.get('image'),
                                config.get('rewrite_image'),
                            ),
                        )
                    return _empty_metadata(url)
                result = run_script(loader_path, url=url, config=config, html_content=body)
                if result:
                    if isinstance(result, dict):
                        result['title'] = apply_rewrite(result.get('title'), config.get('rewrite_title'))
                        result['description'] = apply_rewrite(result.get('description'), config.get('rewrite_description'))
                        result['image'] = apply_rewrite(result.get('image'), config.get('rewrite_image'))
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


def _config_cache_key(config: dict) -> str:
    return json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)


# Caching metadata avoids scraping again when saving bookmarks
@lru_cache(maxsize=10)
def _load_website_metadata_cached(url: str, username: str = ''):
    return _load_website_metadata(url, username=username)


@lru_cache(maxsize=10)
def _load_website_metadata_config_cached(url: str, config_key: str, username: str = ''):
    return _load_website_metadata(url, json.loads(config_key), username=username)


_METADATA_MAX_RETRIES = 3
_METADATA_RETRY_BASE_DELAY = 1.0  # seconds, doubles each attempt


def _load_website_metadata(url: str, config: dict = None, username: str = '', include_sources: bool = False):
    fetch_url = config.get("_request_url", url) if config else url
    load_full = config.get("load_full_page", True) if config else True
    page_text = None
    last_exc = None

    # Pre-flight: for auto-type cookie sites without cookies, acquire via browser first.
    try:
        cookie_config = config.get("cookie") if config else {}
        if cookie_config and cookie_config.get("type") == "auto":
            cookie_str = _cookie_string_from_config(config)
            if not cookie_str and cookie_config.get("refresh"):
                domain_key = config.get("_domain_key")
                logger.info("No cookie for auto site %s, acquiring via browser refresh", domain_key)
                new_cookie = verify_and_refresh(
                    cookie_config, fetch_url, domain_key,
                    {"url": fetch_url, "status": 0, "title": "", "body_preview": ""},
                    username=username,
                )
                if new_cookie:
                    config["_user_cookie"] = new_cookie
                    logger.info("Pre-flight acquired cookie for %s, proceeding with request", domain_key)
                else:
                    logger.warning("Pre-flight failed to acquire cookie for %s", domain_key)
    except Exception as e:
        logger.error("Pre-flight cookie acquisition error for %s: %s", url, e)

    for attempt in range(_METADATA_MAX_RETRIES + 1):
        try:
            start = timezone.now()
            page_text = load_page(fetch_url, config, load_full_page=load_full)
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
            if include_sources:
                return _empty_metadata(url), {}
            return _empty_metadata(url)
        except Exception as exc:
            logger.error("Unexpected metadata request failure. url=%s", exc_info=exc)
            if include_sources:
                return _empty_metadata(url), {}
            return _empty_metadata(url)

    if last_exc is not None:
        logger.warning("All %d retries exhausted, returning empty metadata. url=%s", _METADATA_MAX_RETRIES, url)
        if include_sources:
            return _empty_metadata(url), {}
        return _empty_metadata(url)

    try:
        start = timezone.now()
        soup = BeautifulSoup(page_text, "html.parser")
        title, description, preview_image, sources = _parse_metadata_from_soup(
            soup, fetch_url, config, include_sources=True
        )

        cookie_config = config.get("cookie") if config else {}
        if cookie_config:
            domain_key = config.get("_domain_key")
            verify_context = {
                "url": fetch_url,
                "status": 200,
                "title": title or "",
                "body_preview": (page_text or "")[:2000],
            }
            before = _cookie_string_from_config(config)
            after = verify_and_refresh(cookie_config, fetch_url, domain_key, verify_context, username=username)
            if after and after != before:
                retry_config = dict(config)
                # 刷新成功后更新 _user_cookie 为新的 cookie 字符串
                retry_config["_user_cookie"] = after
                page_text = load_page(fetch_url, retry_config, load_full_page=load_full)
                soup = BeautifulSoup(page_text, "html.parser")
                title, description, preview_image, sources = _parse_metadata_from_soup(
                    soup, fetch_url, retry_config, include_sources=True
                )

        end = timezone.now()
        logger.debug("Parsing duration: %s", end - start)
    except Exception as exc:
        logger.error("Unexpected metadata parsing failure. url=%s", url, exc_info=exc)
        if include_sources:
            return _empty_metadata(url), {}
        return _empty_metadata(url)

    if config:
        title = apply_rewrite(title, config.get('rewrite_title'))
        description = apply_rewrite(description, config.get('rewrite_description'))
        preview_image = apply_rewrite(preview_image, config.get('rewrite_image'))

    metadata = WebsiteMetadata(
        url=(config.get("_rewrite_url") if config else None) or url,
        title=title,
        description=description,
        preview_image=preview_image,
    )
    if include_sources:
        return metadata, sources
    return metadata


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
            title = item.get("headline") or item.get("name") or item.get("title")
            if title and isinstance(title, str):
                result["title"] = title.strip()
            # description
            desc = item.get("description")
            if desc and isinstance(desc, str):
                result["description"] = desc.strip()
            # image
            img = item.get("image") or item.get("thumbnailUrl")
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
    title, source = _extract_with_selector_source(
        soup, title_selectors or [], url, "title"
    )
    sources["title"] = {"value": title, "selector": source}

    desc_selectors = config.get("select_description") if config else None
    description, source = _extract_with_selector_source(
        soup, desc_selectors or [], url, "description"
    )
    sources["description"] = {"value": description, "selector": source}

    image_selectors = config.get("select_image") if config else None
    preview_image, source = _extract_with_selector_source(
        soup, image_selectors or [], url, "image"
    )
    sources["preview_image"] = {"value": preview_image, "selector": source}

    # JSON-LD as universal fallback for any missing fields
    if title is None or description is None or preview_image is None:
        json_ld = _extract_json_ld(soup)
        if title is None:
            title = json_ld.get("title")
            if title:
                sources["title"] = {"value": title, "selector": "json-ld"}
        if description is None:
            description = json_ld.get("description")
            if description:
                sources["description"] = {"value": description, "selector": "json-ld"}
        if preview_image is None:
            preview_image = json_ld.get("image")
            if preview_image:
                sources["preview_image"] = {"value": preview_image, "selector": "json-ld"}

    if (
        preview_image
        and not preview_image.startswith("http://")
        and not preview_image.startswith("https://")
    ):
        preview_image = urljoin(url, preview_image)
    sources["preview_image"] = {"value": preview_image, "selector": sources["preview_image"]["selector"]}

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
        load_full = config.get("load_full_page", True) if config else True
        body = load_page(config.get("_request_url", url), config, load_full_page=load_full)
        result = run_script(script_path, url=url, config=config, html_content=body)
        if result and isinstance(result, dict):
            metadata = WebsiteMetadata(
                url=result.get('url') or url,
                title=apply_rewrite(result.get('title'), config.get('rewrite_title')),
                description=apply_rewrite(result.get('description'), config.get('rewrite_description')),
                preview_image=apply_rewrite(result.get('preview_image'), config.get('rewrite_image')),
            )
        else:
            metadata = _empty_metadata(url)
        return metadata, {"script": script_path}, config

    metadata, sources = _load_website_metadata(url, config, username, include_sources=True)
    return metadata, sources, config


def load_page(url: str, config: dict = None, load_full_page: bool = False):
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

    # Unit: KB
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

                # Stop reading at </head> unless loading full page
                if not load_full_page:
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

    if not content:
        return ""

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
        if config.get("headers", {}).get("Cookie"):
            headers.pop("Cookie", None)
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
    # Priority: user credentials > shared credentials > Cookie header (http config)
    user_cookie = config.get("_user_cookie")
    if user_cookie:
        return user_cookie
    domain_key = config.get("_domain_key")
    if domain_key:
        shared, _ = get_shared_cookie(domain_key)
        if shared:
            return shared
    cookies_str = config.get("headers", {}).get("Cookie")
    if cookies_str:
        return cookies_str
    return None
