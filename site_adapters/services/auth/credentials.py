"""
User credential management

Storage: credentials/users/{username}/{domain}/cookie.json (encrypted)
         credentials/users/{username}/{domain}/header.json (encrypted)
Metadata: credentials/encryption.meta (key fingerprint + domain index)
"""

import json
import logging
import os
import time

from bookmarks.utils import atomic_write
from site_adapters.services.auth.crypto import (
    decrypt_or_plaintext,
    encrypt_value,
    get_key_fingerprint,
    is_encrypted,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _get_credentials_dir() -> str:
    from site_adapters.services.base import _get_base_dir
    return os.path.join(_get_base_dir(), 'credentials')



# ── Shared credential path helpers ──

def _shared_credential_dir(domain: str) -> str:
    return os.path.join(_get_credentials_dir(), 'shared', domain)


def _shared_credential_path(domain: str, cred_type: str, index: int = 0) -> str:
    """Build a shared credential file path.

    index=0 uses the bare filename (cookie.json); index>=1 uses cookie_1.json etc.
    This allows future multi-credential pool expansion without changing the API.
    """
    basename = f'{cred_type}.json' if index <= 0 else f'{cred_type}_{index}.json'
    return os.path.join(_shared_credential_dir(domain), basename)


# ── User credential path helpers ──

def _credential_path(username: str, domain: str, filename: str) -> str:
    """Build a credential file path without domain matching."""
    return os.path.join(_get_credentials_dir(), 'users', username, domain, filename)


def _resolve_domain_in_dir(base_dir: str, hostname: str) -> str | None:
    """Find the best matching domain directory in a given base directory.

    Multi-level DNS fallback: tries the full hostname first, then strips
    one subdomain at a time. Checks exact match, then wildcard forward
    (stored ``*.example.com`` matching candidate ``www.example.com``).

    Only returns directories that contain at least one credential file.
    """
    if not os.path.isdir(base_dir):
        return None

    stored = []
    for entry in os.listdir(base_dir):
        entry_path = os.path.join(base_dir, entry)
        if not os.path.isdir(entry_path):
            continue
        try:
            if any(os.path.isfile(os.path.join(entry_path, f)) for f in os.listdir(entry_path)):
                stored.append(entry)
        except OSError:
            continue

    if not stored:
        return None

    bare_hostname = hostname[2:] if hostname.startswith('*.') else hostname
    parts = bare_hostname.split('.')
    candidates = []
    for i in range(len(parts) - 1):
        candidates.append('.'.join(parts[i:]))

    for candidate in candidates:
        if candidate in stored:
            return candidate

        best_wildcard = None
        best_depth = -1
        for entry in stored:
            if entry.startswith('*.'):
                suffix = entry[1:]
                if candidate.endswith(suffix):
                    depth = entry.count('.')
                    if depth > best_depth:
                        best_wildcard = entry
                        best_depth = depth
        if best_wildcard:
            return best_wildcard

    return None


def _resolve_credential_domain(username: str, hostname: str) -> str | None:
    """Resolve domain for user credentials (thin wrapper)."""
    return _resolve_domain_in_dir(
        os.path.join(_get_credentials_dir(), 'users', username), hostname
    )


def _resolve_shared_credential_domain(hostname: str) -> str | None:
    """Resolve domain for shared credentials (thin wrapper)."""
    return _resolve_domain_in_dir(
        os.path.join(_get_credentials_dir(), 'shared'), hostname
    )


def _resolve_credential_path(username: str, hostname: str, filename: str) -> str:
    """Build a credential file path with domain matching for reads."""
    matched = _resolve_credential_domain(username, hostname)
    effective_domain = matched if matched else hostname
    return _credential_path(username, effective_domain, filename)


def _get_meta_path() -> str:
    return os.path.join(_get_credentials_dir(), 'encryption.meta')


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def _load_meta() -> dict:
    path = _get_meta_path()
    if not os.path.exists(path):
        return {'key_fingerprint': '', 'credentials': {}}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {'key_fingerprint': '', 'credentials': {}}
        data.setdefault('key_fingerprint', '')
        data.setdefault('credentials', {})
        return data
    except (json.JSONDecodeError, OSError):
        return {'key_fingerprint': '', 'credentials': {}}


def _save_meta(meta: dict):
    atomic_write(_get_meta_path(), json.dumps(meta, indent=2, ensure_ascii=False))


def _update_meta_entry(cred_type: str, username: str, domain: str, **fields):
    meta = _load_meta()
    # keep fingerprint current
    meta['key_fingerprint'] = get_key_fingerprint()
    key = f'{cred_type}:{username}:{domain}'
    entry = meta['credentials'].get(key, {})
    entry.update(fields)
    meta['credentials'][key] = entry
    _save_meta(meta)


def check_key_fingerprint() -> bool:
    """检查存储的 fingerprint 与当前密钥是否匹配。匹配返回 True。"""
    meta = _load_meta()
    stored = meta.get('key_fingerprint', '')
    if not stored:
        return True  # first use, no fingerprint
    return stored == get_key_fingerprint()


# ---------------------------------------------------------------------------
# File I/O (encryption layer)
# ---------------------------------------------------------------------------

def _read_encrypted_file(path: str) -> tuple[str | None, str]:
    """
    Read encrypted file. Returns (decrypted_content, status).
    Status: 'ok' | 'key_changed' | 'not_found' | 'error'
    """
    if not os.path.exists(path):
        return None, 'not_found'
    try:
        with open(path, encoding='utf-8') as f:
            raw = f.read()
        if not raw.strip():
            return None, 'not_found'
        if not is_encrypted(raw):
            # legacy plaintext, lazy migration
            return raw, 'ok'
        result = decrypt_or_plaintext(raw)
        if result == raw and is_encrypted(raw):
            # decrypt failed, key changed
            return None, 'key_changed'
        return result, 'ok'
    except Exception:
        return None, 'error'


def _write_encrypted_file(path: str, content: str):
    """加密并写入文件（原子写入：先写临时文件再 rename）。"""
    encrypted = encrypt_value(content)
    atomic_write(path, encrypted)


def _remove_file(path: str):
    if os.path.exists(path):
        os.remove(path)


# ---------------------------------------------------------------------------
# Cookie credentials
# ---------------------------------------------------------------------------

def get_user_cookie(username: str, hostname: str) -> tuple[str | None, str]:
    """
    Get the user's cookie credential for a hostname.

    Uses DNS multi-level fallback: "www.zhihu.com" -> "zhihu.com".

    Returns (cookie_string | None, status).
    Status: 'ok' | 'key_changed' | 'not_found' | 'error'
    """
    path = _resolve_credential_path(username, hostname, 'cookie.json')
    content, status = _read_encrypted_file(path)
    if content is None:
        return None, status
    cookie_str = _json_cookie_to_header_string(content)
    return cookie_str, status


def save_user_cookie(username: str, domain: str, cookie_str: str, exact: bool = True):
    """Save the user's cookie credential (encrypted storage).

    By default uses exact domain without fallback matching to avoid
    accidentally overwriting a different domain's credentials.
    """
    from site_adapters.services.auth.cookies import cookie_string_to_playwright_list
    cookies_list = cookie_string_to_playwright_list(cookie_str, domain)
    cookie_content = json.dumps(cookies_list, ensure_ascii=False)
    if exact:
        path = _credential_path(username, domain, 'cookie.json')
    else:
        path = _resolve_credential_path(username, domain, 'cookie.json')
    _write_encrypted_file(path, cookie_content)
    _update_meta_entry('cookies', username, domain,
                       updated_at=_now_iso(), source='paste')


def delete_user_cookie(username: str, domain: str):
    """Delete the user's cookie credential."""
    path = _resolve_credential_path(username, domain, 'cookie.json')
    _remove_file(path)
    meta = _load_meta()
    meta['credentials'].pop(f'cookies:{username}:{domain}', None)
    _save_meta(meta)


# ---------------------------------------------------------------------------
# Header credentials
# ---------------------------------------------------------------------------

def get_user_header(username: str, hostname: str, header_name: str) -> tuple[str | None, str]:
    """
    Get a specific header credential for a hostname.
    Returns (header_value | None, status).
    """
    path = _resolve_credential_path(username, hostname, 'header.json')
    content, status = _read_encrypted_file(path)
    if content is None:
        return None, status
    try:
        headers = json.loads(content)
        return headers.get(header_name), status
    except (json.JSONDecodeError, AttributeError):
        return None, 'error'


def get_user_headers(username: str, hostname: str) -> tuple[dict, str]:
    """
    Get all header credentials for a hostname.
    Returns (headers dict, status).
    """
    path = _resolve_credential_path(username, hostname, 'header.json')
    content, status = _read_encrypted_file(path)
    if content is None:
        return {}, status
    try:
        headers = json.loads(content)
        return headers if isinstance(headers, dict) else {}, status
    except (json.JSONDecodeError, AttributeError):
        return {}, 'error'


def save_user_header(username: str, domain: str, header_name: str, value: str, exact: bool = True):
    """Save a header credential (encrypted storage)."""
    path = _credential_path(username, domain, 'header.json') if exact \
        else _resolve_credential_path(username, domain, 'header.json')
    # read existing headers (may have other headers)
    existing, _ = _read_encrypted_file(path)
    try:
        headers = json.loads(existing) if existing else {}
        if not isinstance(headers, dict):
            headers = {}
    except (json.JSONDecodeError, AttributeError):
        headers = {}
    headers[header_name] = value
    content = json.dumps(headers, ensure_ascii=False)
    _write_encrypted_file(path, content)
    _update_meta_entry('headers', username, domain,
                       updated_at=_now_iso(), source='paste')


def delete_user_header(username: str, domain: str, header_name: str = ''):
    """Delete a header credential. Empty header_name deletes the entire file."""
    path = _resolve_credential_path(username, domain, 'header.json')
    if not header_name:
        _remove_file(path)
        meta = _load_meta()
        meta['credentials'].pop(f'headers:{username}:{domain}', None)
        _save_meta(meta)
        return
    # delete single header
    existing, _ = _read_encrypted_file(path)
    try:
        headers = json.loads(existing) if existing else {}
    except (json.JSONDecodeError, AttributeError):
        headers = {}
    headers.pop(header_name, None)
    if headers:
        _write_encrypted_file(path, json.dumps(headers, ensure_ascii=False))
    else:
        _remove_file(path)
        meta = _load_meta()
        meta['credentials'].pop(f'headers:{username}:{domain}', None)
        _save_meta(meta)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Token credentials
# ---------------------------------------------------------------------------

def get_user_token(username: str, hostname: str) -> tuple[str | None, str]:
    """Get the user's refresh_token for a hostname."""
    path = _resolve_credential_path(username, hostname, 'token.json')
    content, status = _read_encrypted_file(path)
    if content is None:
        return None, status
    try:
        data = json.loads(content)
        return data.get('refresh_token', content) if isinstance(data, dict) else content, status
    except (json.JSONDecodeError, AttributeError):
        return content, status


def save_user_token(username: str, domain: str, refresh_token: str, exact: bool = True):
    """Save the user's refresh_token (encrypted storage)."""
    path = _credential_path(username, domain, 'token.json') if exact \
        else _resolve_credential_path(username, domain, 'token.json')
    token_content = json.dumps({'refresh_token': refresh_token}, ensure_ascii=False)
    _write_encrypted_file(path, token_content)
    _update_meta_entry('tokens', username, domain,
                       updated_at=_now_iso(), source='paste')


def delete_user_token(username: str, domain: str):
    """Delete the user's token credential."""
    path = _resolve_credential_path(username, domain, 'token.json')
    _remove_file(path)
    cache_path = _resolve_credential_path(username, domain, 'token_cache.json')
    _remove_file(cache_path)
    meta = _load_meta()
    meta['credentials'].pop(f'tokens:{username}:{domain}', None)
    _save_meta(meta)



# ---------------------------------------------------------------------------
# Shared credentials
# ---------------------------------------------------------------------------

# ── Shared Cookie ──

def get_shared_cookie(hostname: str) -> tuple[str | None, str]:
    """Get the shared cookie credential for a hostname.

    Uses DNS multi-level fallback. Returns (cookie_string | None, status).
    """
    matched = _resolve_shared_credential_domain(hostname)
    if not matched:
        return None, 'not_found'
    path = _shared_credential_path(matched, 'cookie')
    content, status = _read_encrypted_file(path)
    if content is None:
        return None, status
    cookie_str = _json_cookie_to_header_string(content)
    return cookie_str, status


def save_shared_cookie(domain: str, cookie_str: str):
    """Save a shared cookie credential (encrypted storage)."""
    from site_adapters.services.auth.cookies import cookie_string_to_playwright_list
    cookies_list = cookie_string_to_playwright_list(cookie_str, domain)
    cookie_content = json.dumps(cookies_list, ensure_ascii=False)
    path = _shared_credential_path(domain, 'cookie')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_encrypted_file(path, cookie_content)
    _update_meta_entry('cookies', 'shared', domain,
                       updated_at=_now_iso(), source='paste')


def delete_shared_cookie(domain: str):
    """Delete a shared cookie credential."""
    path = _shared_credential_path(domain, 'cookie')
    _remove_file(path)
    meta = _load_meta()
    meta['credentials'].pop(f'cookies:shared:{domain}', None)
    _save_meta(meta)


# ── Shared Header ──

def get_shared_header(hostname: str, header_name: str) -> tuple[str | None, str]:
    """Get a specific shared header credential for a hostname."""
    matched = _resolve_shared_credential_domain(hostname)
    if not matched:
        return None, 'not_found'
    path = _shared_credential_path(matched, 'header')
    content, status = _read_encrypted_file(path)
    if content is None:
        return None, status
    try:
        headers = json.loads(content)
        return headers.get(header_name), status
    except (json.JSONDecodeError, AttributeError):
        return None, 'error'


def get_shared_headers(hostname: str) -> tuple[dict, str]:
    """Get all shared header credentials for a hostname."""
    matched = _resolve_shared_credential_domain(hostname)
    if not matched:
        return {}, 'not_found'
    path = _shared_credential_path(matched, 'header')
    content, status = _read_encrypted_file(path)
    if content is None:
        return {}, status
    try:
        headers = json.loads(content)
        return headers if isinstance(headers, dict) else {}, status
    except (json.JSONDecodeError, AttributeError):
        return {}, 'error'


def save_shared_header(domain: str, header_name: str, value: str):
    """Save a shared header credential (encrypted storage)."""
    path = _shared_credential_path(domain, 'header')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing, _ = _read_encrypted_file(path)
    try:
        headers = json.loads(existing) if existing else {}
        if not isinstance(headers, dict):
            headers = {}
    except (json.JSONDecodeError, AttributeError):
        headers = {}
    headers[header_name] = value
    content = json.dumps(headers, ensure_ascii=False)
    _write_encrypted_file(path, content)
    _update_meta_entry('headers', 'shared', domain,
                       updated_at=_now_iso(), source='paste')


def delete_shared_header(domain: str, header_name: str = ''):
    """Delete a shared header credential. Empty header_name deletes the entire file."""
    path = _shared_credential_path(domain, 'header')
    if not header_name:
        _remove_file(path)
        meta = _load_meta()
        meta['credentials'].pop(f'headers:shared:{domain}', None)
        _save_meta(meta)
        return
    existing, _ = _read_encrypted_file(path)
    try:
        headers = json.loads(existing) if existing else {}
    except (json.JSONDecodeError, AttributeError):
        headers = {}
    headers.pop(header_name, None)
    if headers:
        _write_encrypted_file(path, json.dumps(headers, ensure_ascii=False))
    else:
        _remove_file(path)
        meta = _load_meta()
        meta['credentials'].pop(f'headers:shared:{domain}', None)
        _save_meta(meta)


# ── Shared Token ──

def get_shared_token(hostname: str) -> tuple[str | None, str]:
    """Get the shared refresh_token for a hostname."""
    matched = _resolve_shared_credential_domain(hostname)
    if not matched:
        return None, 'not_found'
    path = _shared_credential_path(matched, 'token')
    content, status = _read_encrypted_file(path)
    if content is None:
        return None, status
    try:
        data = json.loads(content)
        return data.get('refresh_token', content) if isinstance(data, dict) else content, status
    except (json.JSONDecodeError, AttributeError):
        return content, status


def save_shared_token(domain: str, refresh_token: str):
    """Save a shared refresh_token (encrypted storage)."""
    path = _shared_credential_path(domain, 'token')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    token_content = json.dumps({'refresh_token': refresh_token}, ensure_ascii=False)
    _write_encrypted_file(path, token_content)
    _update_meta_entry('tokens', 'shared', domain,
                       updated_at=_now_iso(), source='paste')


def delete_shared_token(domain: str):
    """Delete a shared token credential."""
    path = _shared_credential_path(domain, 'token')
    _remove_file(path)
    cache_path = _shared_credential_path(domain, 'token_cache')
    _remove_file(cache_path)
    meta = _load_meta()
    meta['credentials'].pop(f'tokens:shared:{domain}', None)
    _save_meta(meta)



# ---------------------------------------------------------------------------
# Unified best-credential resolution (user first, shared fallback)
# Used by resolver.py and auth/__init__.py to avoid duplicated fallback logic.
# ---------------------------------------------------------------------------

def get_best_cookie(username: str, hostname: str) -> tuple[str | None, str]:
    """Get the best available cookie: user credential first, shared fallback."""
    if username:
        result, status = get_user_cookie(username, hostname)
        if result:
            return result, status
    return get_shared_cookie(hostname)


def get_best_header(username: str, hostname: str, header_name: str) -> tuple[str | None, str]:
    """Get the best available header: user credential first, shared fallback."""
    if username:
        result, status = get_user_header(username, hostname, header_name)
        if result:
            return result, status
    return get_shared_header(hostname, header_name)


def get_best_token(username: str, hostname: str) -> tuple[str | None, str]:
    """Get the best available token: user credential first, shared fallback."""
    if username:
        result, status = get_user_token(username, hostname)
        if result:
            return result, status
    return get_shared_token(hostname)


def _list_credentials_in_dir(base_dir: str, meta_prefix: str,
                                  include_values: bool = False) -> list[dict]:
    """List all credentials in a directory, optionally with decrypted values."""
    result = []
    meta = _load_meta()
    fingerprint_ok = check_key_fingerprint()

    if not os.path.isdir(base_dir):
        return result

    for domain in sorted(os.listdir(base_dir)):
        domain_dir = os.path.join(base_dir, domain)
        if not os.path.isdir(domain_dir):
            continue

        for cred_type, filename in [
            ('cookie', 'cookie.json'),
            ('header', 'header.json'),
            ('token', 'token.json'),
        ]:
            path = os.path.join(domain_dir, filename)
            if not os.path.isfile(path):
                continue
            meta_key = f'{cred_type}s:{meta_prefix}:{domain}'
            m = meta['credentials'].get(meta_key, {})
            content, status = _read_encrypted_file(path)
            if not fingerprint_ok and status == 'ok':
                status = 'key_changed'

            entry = {
                'domain': domain,
                'type': cred_type,
                'status': status,
                'updated_at': m.get('updated_at', ''),
            }

            if include_values and content and status == 'ok':
                if cred_type == 'cookie':
                    entry['cookie'] = _json_cookie_to_header_string(content) or ''
                elif cred_type == 'header':
                    try:
                        hdrs = json.loads(content)
                        if isinstance(hdrs, dict):
                            entry['header_names'] = list(hdrs.keys())
                            entry['header_values'] = hdrs
                    except (json.JSONDecodeError, AttributeError):
                        entry['header_names'] = []
                        entry['header_values'] = {}
                elif cred_type == 'token':
                    try:
                        token_data = json.loads(content)
                        entry['token'] = token_data.get('refresh_token', '') if isinstance(token_data, dict) else ''
                    except (json.JSONDecodeError, AttributeError):
                        entry['token'] = ''

            result.append(entry)

    return result


def list_shared_credentials(include_values: bool = False) -> list[dict]:
    """List all shared credentials."""
    return _list_credentials_in_dir(
        os.path.join(_get_credentials_dir(), 'shared'), 'shared',
        include_values=include_values,
    )


def list_user_credentials(username: str) -> list[dict]:
    """列出用户的所有凭据（含解密后的值）。"""
    return _list_credentials_in_dir(
        os.path.join(_get_credentials_dir(), 'users', username),
        username,
        include_values=True,
    )



def load_user_token_cache(username: str, hostname: str) -> dict | None:
    """Load token cache (access_token + expires_at) for a hostname."""
    path = _resolve_credential_path(username, hostname, 'token_cache.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def save_user_token_cache(username: str, domain: str, cache_data: dict):
    """Save token cache for a domain.

    Uses exact domain (no fallback) because the cache is always written
    alongside the token itself in the same domain directory.
    """
    matched = _resolve_credential_domain(username, domain)
    effective_domain = matched if matched else domain
    path = _credential_path(username, effective_domain, 'token_cache.json')
    atomic_write(path, json.dumps(cache_data, ensure_ascii=False))


def get_auth_requirements_for_domain(hostname: str, base_dir: str = '') -> dict:
    """
    Query auth requirements for a hostname (local + subscription).
    Returns {'cookie': bool, 'headers': [str], 'token': bool}
    """
    if not base_dir:
        from site_adapters.services.base import _get_base_dir
        base_dir = _get_base_dir()

    from site_adapters.services.config.loader import load_domain_config
    url = f'https://{hostname}'
    config = load_domain_config(url, base_dir)
    if not config:
        return {'cookie': False, 'headers': [], 'token': False, 'cookie_type': ''}

    auth = config.get('auth', {})
    has_cookie = bool(auth.get('cookie'))
    headers = list(auth.get('headers', {}).keys()) if isinstance(auth.get('headers'), dict) else []
    has_token = bool(auth.get('token', {}).get('endpoint'))
    cookie_type = auth.get('cookie', {}).get('type', 'anon') if isinstance(auth.get('cookie'), dict) else 'anon'
    return {'cookie': has_cookie, 'headers': headers, 'token': has_token, 'cookie_type': cookie_type}


def get_auth_requirements_for_domain_key(domain_key: str, base_dir: str = '') -> dict:
    """Query auth requirements for a resolved domain key."""
    if not base_dir:
        from site_adapters.services.base import _get_base_dir
        base_dir = _get_base_dir()
    if not domain_key:
        return {'cookie': False, 'headers': [], 'token': False, 'cookie_type': ''}

    from site_adapters.services.config import deep_merge
    from site_adapters.services.config.loader import _cache, _resolve_alias

    all_config = _cache.load(base_dir)
    defaults = all_config.get('defaults', {})
    raw_config = all_config.get(domain_key)
    if raw_config is None:
        return {'cookie': False, 'headers': [], 'token': False, 'cookie_type': ''}

    resolved = _resolve_alias(raw_config, all_config) if isinstance(raw_config, dict) else raw_config
    if not isinstance(resolved, dict):
        return {'cookie': False, 'headers': [], 'token': False, 'cookie_type': ''}

    merged = deep_merge(resolved, defaults) if defaults else resolved
    auth = merged.get('auth', {})
    has_cookie = bool(auth.get('cookie'))
    headers = sorted(auth.get('headers', {}).keys()) if isinstance(auth.get('headers'), dict) else []
    has_token = bool(auth.get('token', {}).get('endpoint'))
    cookie_type = auth.get('cookie', {}).get('type', 'anon') if isinstance(auth.get('cookie'), dict) else 'anon'
    return {'cookie': has_cookie, 'headers': headers, 'token': has_token, 'cookie_type': cookie_type}


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _json_cookie_to_header_string(data_str: str) -> str | None:
    """Convert Playwright cookie JSON to header string."""
    from site_adapters.services.auth.cookies import _cookie_data_to_string as _convert
    try:
        data = json.loads(data_str)
    except (json.JSONDecodeError, TypeError):
        return data_str if isinstance(data_str, str) else None
    return _convert(data)


def _now_iso() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


# ---------------------------------------------------------------------------
# User preferences (toggles)
# ---------------------------------------------------------------------------

def _get_user_preferences_path(username: str) -> str:
    return os.path.join(_get_credentials_dir(), 'users', username, 'preferences.json')


def get_user_preferences(username: str) -> dict:
    """获取用户的所有偏好设置。格式: {domain_key: {toggle_id: true/false, ...}}"""
    path = _get_user_preferences_path(username)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def get_user_domain_preferences(username: str, domain_key: str) -> dict:
    """获取用户对特定域名的偏好。格式: {toggle_id: true/false}"""
    prefs = get_user_preferences(username)
    domain_prefs = prefs.get(domain_key, {})
    return domain_prefs if isinstance(domain_prefs, dict) else {}


def save_user_preferences(username: str, domain_key: str, toggle_id: str, enabled: bool):
    """保存用户对特定域名某个 toggle 的偏好。"""
    prefs = get_user_preferences(username)
    if domain_key not in prefs:
        prefs[domain_key] = {}
    prefs[domain_key][toggle_id] = enabled
    path = _get_user_preferences_path(username)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write(path, json.dumps(prefs, indent=2, ensure_ascii=False))


def list_domains_with_toggles(base_dir: str) -> list[dict]:
    """列出所有声明了 toggles 的域名及其 toggle 定义。"""
    from site_adapters.services.config.loader import _cache
    all_config = _cache.load(base_dir)
    result = []
    for key, config in all_config.items():
        if key == 'defaults' or key.startswith('_'):
            continue
        if not isinstance(config, dict):
            continue
        toggles = config.get('snapshot', {}).get('toggles', {})
        if toggles and isinstance(toggles, dict):
            result.append({
                'domain': key,
                'toggles': toggles,
            })
    return result
