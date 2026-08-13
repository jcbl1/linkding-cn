"""
Config resolver: provides unified config access for each service.

Config structure:
  {
    "auth": { ... },      # auth requirements (cookie + headers)
    "defaults": { ... },  # shared settings
    "metadata": { ... },   # metadata extraction
    "snapshot": { ... },   # HTML snapshot
    "reader": { ... }      # reader mode
  }

Merge rule: defaults + section -> section overrides same-name fields.
HTTP sub-objects are merged: defaults.http + section.http -> section overrides.
Auth sub-objects are merged: top.auth + defaults.auth + section.auth -> section overrides.
"""

import logging
import os

from site_adapters.services.auth.cookies import (
    COOKIE_DEFAULTS,
    merge_cookie,
)
from site_adapters.services.auth.credentials import (
    get_best_cookie,
    get_best_header,
    get_best_token,
    get_best_basic_auth,
)
from site_adapters.services.auth.oauth2 import (
    get_token_header,
    get_valid_token,
    refresh_token as _refresh_token,
)
from site_adapters.services.config import (
    apply_request_url,
    apply_rewrite_url,
    deep_merge,
)
from site_adapters.services.config.loader import (
    load_builtin_config,
    load_domain_config,
)

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
    oauth2: replace as a whole (later overrides)
    basic_auth: replace as a whole (later overrides)
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
        # OAuth2 (was token)
        if 'oauth2' in block:
            result['oauth2'] = dict(block['oauth2'])
        # Basic Auth
        if 'basic_auth' in block:
            result['basic_auth'] = dict(block['basic_auth'])
    return result



def _apply_toggles(section_data: dict, full_config: dict, username: str) -> tuple[list, list]:
    """Apply user toggle preferences to remove_elements / keep_elements."""
    remove_elements = list(section_data.get('remove_elements') or [])
    keep_elements = list(section_data.get('keep_elements') or [])
    toggles = section_data.get('toggles', {})
    user_prefs = {}
    if toggles and username:
        domain_key = full_config.get('_domain_key', '')
        from site_adapters.services.auth.credentials import get_user_domain_preferences
        user_prefs = get_user_domain_preferences(username, domain_key)
    if toggles:
        for toggle_id, toggle_def in toggles.items():
            if not isinstance(toggle_def, dict):
                continue
            selector = toggle_def.get('selector', '')
            if not selector:
                continue
            default_keep = toggle_def.get('default', True)
            user_choice = user_prefs.get(toggle_id)
            should_keep = user_choice if user_choice is not None else default_keep
            if not should_keep:
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
    Build flat config for a section by merging defaults + section.

    Returns a dict with:
    - headers: HTTP headers dict
    - timeout, proxy: framework fields
    - auth: merged auth config dict (cookie + headers)
    - request_url; rewrite_url (metadata only)
    - section-specific fields
    - _request_url, _rewrite_url: resolved URLs
    - _domain_key, _raw: metadata
    """
    default = full_config.get('defaults', {})
    section_data = full_config.get(section, {})

    # Merge: defaults + section (section overrides)
    merged = _merge_dicts(default, section_data)

    # HTTP: defaults.http + section.http
    default_http = default.get('http', {})
    section_http = section_data.get('http', {})
    merged_http = _merge_dicts(default_http, section_http)

    # Extract framework fields from merged
    timeout = merged.get('timeout')
    proxy = merged.get('proxy')

    # HTTP headers (all keys in http sub-object are headers)
    headers = {k: v for k, v in merged_http.items() if v is not None}

    # Auth: top.auth + defaults.auth + section.auth merged
    top_auth = full_config.get('auth', {})
    default_auth = default.get('auth', {})
    section_auth = section_data.get('auth', {})
    merged_auth = _merge_auth(top_auth, default_auth, section_auth)

    # Domain key (used for cookie file path derivation)
    domain_key = full_config.get('_domain_key', '')
    # Hostname from the original URL (used for credential lookups with DNS fallback)
    from urllib.parse import urlparse
    hostname = urlparse(full_config.get('_url', '')).hostname or domain_key

    # Cookie config from auth (deep-merge with defaults)
    cookie_config = {}
    merged_cookie = merged_auth.get('cookie', {})
    if merged_cookie and merged_cookie.get('enabled', True):
        cookie_config = merge_cookie(dict(COOKIE_DEFAULTS), dict(merged_cookie))

    # cookie and http Cookie header cannot coexist
    if cookie_config and 'Cookie' in headers:
        logger.warning("%s: auth.cookie and Cookie header coexist, Cookie header ignored", section)
        headers.pop('Cookie', None)

    # Best cookie: user credential first, shared fallback
    user_cookie_str = None
    if cookie_config:
        user_cookie_str, _ = get_best_cookie(username, hostname)

    # Headers: config default → shared credential → user credential
    if merged_auth.get('headers'):
        merged_headers = merged_auth['headers']
        for header_name, default_val in merged_headers.items():
            if not isinstance(default_val, str):
                default_val = ''
            # Priority: user cred > shared cred > config default
            best_val = None
            if username:
                user_val, _ = get_best_header(username, hostname, header_name)
                if user_val:
                    best_val = user_val
            if not best_val and default_val:
                best_val = default_val
            if best_val:
                headers[header_name] = best_val

    # OAuth2: auto-inject access_token (user first, shared fallback)
    merged_oauth2 = merged_auth.get('oauth2', {})
    if merged_oauth2.get('enabled', True) and merged_oauth2.get('endpoint'):
        if username:
            access_token = get_valid_token(merged_oauth2, username, hostname)
            if access_token:
                oauth2_headers = get_token_header(merged_oauth2, access_token)
                headers.update(oauth2_headers)
        else:
            best_rt, _ = get_best_token(username, hostname)
            if best_rt:
                token_result = _refresh_token(merged_oauth2, best_rt)
                if token_result:
                    oauth2_headers = get_token_header(merged_oauth2, token_result['access_token'])
                    headers.update(oauth2_headers)


    # Basic Auth: user credential first, shared fallback
    merged_basic = merged_auth.get('basic_auth', {})
    if isinstance(merged_basic, dict) and merged_basic.get('enabled', True) and merged_basic:
        best_ba, _ = get_best_basic_auth(username, hostname)
        if best_ba:
            import base64
            credentials = f"{best_ba['username']}:{best_ba['password']}"
            encoded = base64.b64encode(credentials.encode()).decode()
            headers['Authorization'] = f'Basic {encoded}'
    result = {
        'headers': headers,
        'timeout': timeout,
        'proxy': proxy,
        'auth': merged_auth,
        'cookie': cookie_config,
        '_user_cookie': user_cookie_str,
        'request_url': merged.get('request_url'),
    }
    if section == 'metadata':
        result['rewrite_url'] = merged.get('rewrite_url')

    # Section-specific fields
    if section == 'metadata':
        if 'select_title' in section_data:
            result['select_title'] = section_data['select_title']
        if 'select_description' in section_data:
            result['select_description'] = section_data['select_description']
        if 'select_image' in section_data:
            result['select_image'] = section_data['select_image']
        if 'rewrite_title' in section_data:
            result['rewrite_title'] = section_data['rewrite_title']
        if 'rewrite_description' in section_data:
            result['rewrite_description'] = section_data['rewrite_description']
        if 'rewrite_image' in section_data:
            result['rewrite_image'] = section_data['rewrite_image']
        result['scripts'] = section_data.get('scripts')
        result['load_full_page'] = section_data.get('load_full_page', True)
        if 'max_content_limit' in section_data:
            result['max_content_limit'] = section_data['max_content_limit']

    elif section == 'snapshot':
        if 'process_lazy_images' in section_data:
            result['process_lazy_images'] = section_data['process_lazy_images']
        result['remove_classes'] = section_data.get('remove_classes')
        result['set_styles'] = section_data.get('set_styles')
        result['scripts'] = section_data.get('scripts')
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
        if section == 'metadata':
            rewrite_url = apply_rewrite_url(url, merged.get('rewrite_url'))
            if rewrite_url:
                result['_rewrite_url'] = rewrite_url

    # Metadata
    result['_domain_key'] = full_config.get('_domain_key')
    result['_raw'] = full_config.get('_raw')
    result['_adapter'] = full_config.get('_adapter')

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_metadata_config(url: str, username: str = '') -> dict | None:
    base_dir = _get_base_dir()
    if not base_dir or not os.path.isdir(base_dir):
        return None
    builtin = load_builtin_config(base_dir) or {}
    domain = load_domain_config(url, base_dir)
    config = deep_merge(builtin, domain) if domain else builtin
    if not config:
        return None
    config['_url'] = url
    return _build_section_config(config, 'metadata', base_dir, username)


def get_snapshot_config(url: str, username: str = '') -> dict | None:
    base_dir = _get_base_dir()
    if not base_dir or not os.path.isdir(base_dir):
        return None
    builtin = load_builtin_config(base_dir) or {}
    domain = load_domain_config(url, base_dir)
    config = deep_merge(builtin, domain) if domain else builtin
    if not config:
        return None
    config['_url'] = url
    return _build_section_config(config, 'snapshot', base_dir, username)


def get_reader_config(url: str, username: str = '') -> dict | None:
    base_dir = _get_base_dir()
    if not base_dir or not os.path.isdir(base_dir):
        return None
    builtin = load_builtin_config(base_dir) or {}
    domain = load_domain_config(url, base_dir)
    config = deep_merge(builtin, domain) if domain else builtin
    if not config:
        return None
    config['_url'] = url
    return _build_section_config(config, 'reader', base_dir, username)
