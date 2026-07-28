"""
Config resolver: provides unified config access for each service.

Config structure:
  {
    "auth": { ... },      # auth requirements (cookie + headers)
    "default": { ... },   # shared settings
    "metadata": { ... },   # metadata extraction
    "snapshot": { ... },   # HTML snapshot
    "reader": { ... }      # reader mode
  }

Merge rule: default + section -> section overrides same-name fields.
HTTP sub-objects are merged: default.http + section.http -> section overrides.
Auth sub-objects are merged: top.auth + default.auth + section.auth -> section overrides.
"""

import logging
import os

from site_adapters.services.auth.cookies import (
    COOKIE_DEFAULTS,
    derive_cookie_file,
    merge_cookie,
)
from site_adapters.services.auth.credentials import (
    get_user_cookie,
    get_user_header,
)
from site_adapters.services.auth.tokens import (
    get_token_header,
    get_valid_token,
)
from site_adapters.services.config import (
    apply_request_url,
    apply_rewrite_url,
)
from site_adapters.services.config.loader import load_domain_config

logger = logging.getLogger(__name__)


from site_adapters.services.base import _get_base_dir

# ---------------------------------------------------------------------------
# Merge helpers
# ---------------------------------------------------------------------------

def _merge_dicts(base: dict, override: dict) -> dict:
    """Merge two dicts: override values replace base, None removes key."""
    result = dict(base)
    for key, value in override.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Auth merge
# ---------------------------------------------------------------------------

def _merge_auth(*auth_blocks: dict) -> dict:
    """
    Merge multiple auth blocks. Later blocks override earlier ones.
    cookie: deep-merge using merge_cookie()
    headers: merge dicts (later overrides same key)
    """
    result = {}
    for block in auth_blocks:
        if not block:
            continue
        # Cookie
        if 'cookie' in block:
            result['cookie'] = merge_cookie(result.get('cookie', {}), block['cookie'])
        # Headers
        if 'headers' in block:
            existing_headers = result.get('headers', {})
            existing_headers.update(block['headers'])
            result['headers'] = existing_headers
        # Token
        if 'token' in block:
            result['token'] = dict(block['token'])
    return result



def _apply_toggles(section_data: dict, full_config: dict, username: str) -> tuple[list, list]:
    """Apply user toggle preferences to remove_elements / keep_elements."""
    remove_elements = list(section_data.get('remove_elements') or [])
    keep_elements = list(section_data.get('keep_elements') or [])
    toggles = section_data.get('toggles', {})
    if toggles and username:
        from site_adapters.services.auth.credentials import get_user_domain_preferences
        domain_key = full_config.get('_domain_key', '')
        user_prefs = get_user_domain_preferences(username, domain_key)
        for toggle_id, toggle_def in toggles.items():
            if not isinstance(toggle_def, dict):
                continue
            selector = toggle_def.get('selector', '')
            if not selector:
                continue
            default_remove = toggle_def.get('default', False)
            user_choice = user_prefs.get(toggle_id)
            should_remove = user_choice if user_choice is not None else default_remove
            if should_remove:
                if selector not in remove_elements:
                    remove_elements.append(selector)
                if selector in keep_elements:
                    keep_elements.remove(selector)
            else:
                if selector in remove_elements:
                    remove_elements.remove(selector)
                if selector not in keep_elements:
                    keep_elements.append(selector)
    return remove_elements, keep_elements

# ---------------------------------------------------------------------------
# Section config builder
# ---------------------------------------------------------------------------

def _build_section_config(full_config: dict, section: str, base_dir: str, username: str = '') -> dict:
    """
    Build flat config for a section by merging default + section.

    Returns a dict with:
    - headers: HTTP headers dict
    - timeout, proxy: framework fields
    - auth: merged auth config dict (cookie + headers)
    - request_url, rewrite_url
    - section-specific fields
    - _request_url, _rewrite_url: resolved URLs
    - _domain_key, _raw: metadata
    """
    default = full_config.get('default', {})
    section_data = full_config.get(section, {})

    # Merge: default + section (section overrides)
    merged = _merge_dicts(default, section_data)

    # HTTP: default.http + section.http
    default_http = default.get('http', {})
    section_http = section_data.get('http', {})
    merged_http = _merge_dicts(default_http, section_http)

    # Extract framework fields from merged
    timeout = merged.get('timeout')
    proxy = merged.get('proxy')

    # HTTP headers (all keys in http sub-object are headers)
    headers = {k: v for k, v in merged_http.items() if v is not None}

    # Auth: top.auth + default.auth + section.auth merged
    top_auth = full_config.get('auth', {})
    default_auth = default.get('auth', {})
    section_auth = section_data.get('auth', {})
    merged_auth = _merge_auth(top_auth, default_auth, section_auth)

    # Domain key (used by cookie/token/credential lookups below)
    domain_key = full_config.get('_domain_key', '')

    # Cookie config from auth
    cookie_config = {}
    if merged_auth.get('cookie'):
        cookie_config = dict(merged_auth['cookie'])
        for key, value in COOKIE_DEFAULTS.items():
            if key not in cookie_config:
                cookie_config[key] = value
        cookie_config['file'] = derive_cookie_file(domain_key)

    # cookie and http Cookie header cannot coexist
    if cookie_config.get('file') and 'Cookie' in headers:
        logger.warning("%s: auth.cookie and Cookie header coexist, Cookie header ignored", section)
        headers.pop('Cookie', None)

    # Inject user cookie credentials (stored separately, highest priority)
    user_cookie_str = None
    if username and cookie_config.get('file'):
        user_cookie_str, _ = get_user_cookie(username, domain_key)

    # Inject user header credentials
    if username and merged_auth.get('headers'):
        for header_name in merged_auth['headers']:
            if header_name not in headers:
                user_header_val, _ = get_user_header(username, domain_key, header_name)
                if user_header_val:
                    headers[header_name] = user_header_val

    # Token: auto-inject access_token as header
    merged_token = merged_auth.get('token', {})
    if merged_token.get('endpoint') and username:
        access_token = get_valid_token(merged_token, username, domain_key)
        if access_token:
            token_headers = get_token_header(merged_token, access_token)
            headers.update(token_headers)

    result = {
        'headers': headers,
        'timeout': timeout,
        'proxy': proxy,
        'auth': merged_auth,
        'cookie': cookie_config,
        '_user_cookie': user_cookie_str,
        'request_url': merged.get('request_url'),
        'rewrite_url': merged.get('rewrite_url'),
    }

    # Section-specific fields
    if section == 'metadata':
        if 'select_title' in section_data:
            result['select_title'] = section_data['select_title']
        if 'select_description' in section_data:
            result['select_description'] = section_data['select_description']
        if 'select_image' in section_data:
            result['select_image'] = section_data['select_image']
        result['script'] = section_data.get('script')

    elif section == 'snapshot':
        if 'process_lazy_images' in section_data:
            result['process_lazy_images'] = section_data['process_lazy_images']
        result['remove_classes'] = section_data.get('remove_classes')
        result['set_styles'] = section_data.get('set_styles')
        result['script'] = section_data.get('script')
        result['singlefile_args'] = section_data.get('singlefile_args', {})
        result['toggles'] = section_data.get('toggles', {})
        result['remove_elements'], result['keep_elements'] = _apply_toggles(section_data, full_config, username)

    elif section == 'reader':
        result['defuddle_args'] = section_data.get('defuddle_args', {})

    # URL processing
    url = full_config.get('_url', '')
    if url:
        request_url = apply_request_url(url, merged.get('request_url'))
        if request_url:
            result['_request_url'] = request_url
        rewrite_url = apply_rewrite_url(url, merged.get('rewrite_url'))
        if rewrite_url:
            result['_rewrite_url'] = rewrite_url

    # Metadata
    result['_domain_key'] = full_config.get('_domain_key')
    result['_raw'] = full_config.get('_raw')

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_metadata_config(url: str, username: str = '') -> dict | None:
    base_dir = _get_base_dir()
    if not base_dir or not os.path.isdir(base_dir):
        return None
    config = load_domain_config(url, base_dir)
    if not config:
        return None
    config['_url'] = url
    return _build_section_config(config, 'metadata', base_dir, username)


def get_snapshot_config(url: str, username: str = '') -> dict | None:
    base_dir = _get_base_dir()
    if not base_dir or not os.path.isdir(base_dir):
        return None
    config = load_domain_config(url, base_dir)
    if not config:
        return None
    config['_url'] = url
    return _build_section_config(config, 'snapshot', base_dir, username)


def get_reader_config(url: str, username: str = '') -> dict | None:
    base_dir = _get_base_dir()
    if not base_dir or not os.path.isdir(base_dir):
        return None
    config = load_domain_config(url, base_dir)
    if not config:
        return None
    config['_url'] = url
    return _build_section_config(config, 'reader', base_dir, username)
