"""
用户凭据管理

存储：credentials/cookies/users/{username}/{domain}.json（加密）
      credentials/headers/users/{username}/{domain}.json（加密）
元数据：credentials/encryption.meta（key fingerprint + 域名索引）
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
# 路径
# ---------------------------------------------------------------------------

def _get_credentials_dir() -> str:
    from site_adapters.services.base import _get_base_dir
    return os.path.join(_get_base_dir(), 'credentials')


def _get_user_cookie_path(username: str, domain: str) -> str:
    return os.path.join(_get_credentials_dir(), 'users', username, domain, 'cookie.json')


def _get_user_header_path(username: str, domain: str) -> str:
    return os.path.join(_get_credentials_dir(), 'users', username, domain, 'header.json')


def _get_user_token_path(username: str, domain: str) -> str:
    return os.path.join(_get_credentials_dir(), 'users', username, domain, 'token.json')


def _get_user_token_cache_path(username: str, domain: str) -> str:
    return os.path.join(_get_credentials_dir(), 'users', username, domain, 'token_cache.json')


def _get_meta_path() -> str:
    return os.path.join(_get_credentials_dir(), 'encryption.meta')


# ---------------------------------------------------------------------------
# 元数据
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
    # 确保 fingerprint 是最新的
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
        return True  # 首次使用，无 fingerprint
    return stored == get_key_fingerprint()


# ---------------------------------------------------------------------------
# 文件读写（加密层）
# ---------------------------------------------------------------------------

def _read_encrypted_file(path: str) -> tuple[str | None, str]:
    """
    读取加密文件。返回 (解密后的内容, 状态)。
    状态: 'ok' | 'key_changed' | 'not_found' | 'error'
    """
    if not os.path.exists(path):
        return None, 'not_found'
    try:
        with open(path, encoding='utf-8') as f:
            raw = f.read()
        if not raw.strip():
            return None, 'not_found'
        if not is_encrypted(raw):
            # 旧版明文，惰性迁移
            return raw, 'ok'
        result = decrypt_or_plaintext(raw)
        if result == raw and is_encrypted(raw):
            # 解密失败且是密文格式 → 密钥变更
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
# Cookie 凭据
# ---------------------------------------------------------------------------

def get_user_cookie(username: str, domain: str) -> tuple[str | None, str]:
    """
    获取用户的 cookie 凭据。
    返回 (cookie 字符串, 状态)。
    状态: 'ok' | 'key_changed' | 'not_found' | 'error'
    """
    path = _get_user_cookie_path(username, domain)
    content, status = _read_encrypted_file(path)
    if content is None:
        return None, status
    # content 是 JSON 格式的 Playwright cookie 列表
    cookie_str = _json_cookie_to_header_string(content)
    return cookie_str, status


def save_user_cookie(username: str, domain: str, cookie_str: str):
    """保存用户的 cookie 凭据（加密存储）。"""
    # 使用共享转换函数（与 cookies 模块保持一致）
    from site_adapters.services.auth.cookies import cookie_string_to_playwright_list
    cookies_list = cookie_string_to_playwright_list(cookie_str, domain)
    content = json.dumps(cookies_list, ensure_ascii=False)
    path = _get_user_cookie_path(username, domain)
    _write_encrypted_file(path, content)
    _update_meta_entry('cookies', username, domain,
                       updated_at=_now_iso(), source='paste')


def delete_user_cookie(username: str, domain: str):
    """删除用户的 cookie 凭据。"""
    path = _get_user_cookie_path(username, domain)
    _remove_file(path)
    meta = _load_meta()
    meta['credentials'].pop(f'cookies:{username}:{domain}', None)
    _save_meta(meta)


# ---------------------------------------------------------------------------
# Header 凭据
# ---------------------------------------------------------------------------

def get_user_header(username: str, domain: str, header_name: str) -> tuple[str | None, str]:
    """
    获取用户的指定 header 凭据值。
    返回 (header 值, 状态)。
    """
    path = _get_user_header_path(username, domain)
    content, status = _read_encrypted_file(path)
    if content is None:
        return None, status
    try:
        headers = json.loads(content)
        return headers.get(header_name), status
    except (json.JSONDecodeError, AttributeError):
        return None, 'error'


def get_user_headers(username: str, domain: str) -> tuple[dict, str]:
    """
    获取用户的所有 header 凭据。
    返回 (headers dict, 状态)。
    """
    path = _get_user_header_path(username, domain)
    content, status = _read_encrypted_file(path)
    if content is None:
        return {}, status
    try:
        headers = json.loads(content)
        return headers if isinstance(headers, dict) else {}, status
    except (json.JSONDecodeError, AttributeError):
        return {}, 'error'


def save_user_header(username: str, domain: str, header_name: str, value: str):
    """保存用户的 header 凭据（加密存储）。"""
    path = _get_user_header_path(username, domain)
    # 读取现有 headers（可能已有其他 header）
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
    """删除用户的 header 凭据。header_name 为空则删除整个文件。"""
    path = _get_user_header_path(username, domain)
    if not header_name:
        _remove_file(path)
        meta = _load_meta()
        meta['credentials'].pop(f'headers:{username}:{domain}', None)
        _save_meta(meta)
        return
    # 删除单个 header
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
# 列表
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Token 凭据
# ---------------------------------------------------------------------------

def get_user_token(username: str, domain: str) -> tuple[str | None, str]:
    """获取用户的 refresh_token。"""
    path = _get_user_token_path(username, domain)
    content, status = _read_encrypted_file(path)
    if content is None:
        return None, status
    try:
        data = json.loads(content)
        return data.get('refresh_token', content), status
    except (json.JSONDecodeError, AttributeError):
        return content, status


def save_user_token(username: str, domain: str, refresh_token: str):
    """保存用户的 refresh_token（加密存储）。"""
    path = _get_user_token_path(username, domain)
    content = json.dumps({'refresh_token': refresh_token}, ensure_ascii=False)
    _write_encrypted_file(path, content)
    _update_meta_entry('tokens', username, domain,
                       updated_at=_now_iso(), source='paste')


def delete_user_token(username: str, domain: str):
    """删除用户的 token 凭据。"""
    path = _get_user_token_path(username, domain)
    _remove_file(path)
    # Also remove cache
    cache_path = _get_user_token_cache_path(username, domain)
    _remove_file(cache_path)
    meta = _load_meta()
    meta['credentials'].pop(f'tokens:{username}:{domain}', None)
    _save_meta(meta)


def load_user_token_cache(username: str, domain: str) -> dict | None:
    """加载 token 缓存（含 access_token + expires_at）。"""
    path = _get_user_token_cache_path(username, domain)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_user_token_cache(username: str, domain: str, cache_data: dict):
    """保存 token 缓存。"""
    path = _get_user_token_cache_path(username, domain)
    atomic_write(path, json.dumps(cache_data, ensure_ascii=False))


def list_user_credentials(username: str) -> list[dict]:
    """列出用户的所有凭据。"""
    result = []
    meta = _load_meta()
    fingerprint_ok = check_key_fingerprint()

    user_dir = os.path.join(_get_credentials_dir(), 'users', username)
    if not os.path.isdir(user_dir):
        return result

    for domain in sorted(os.listdir(user_dir)):
        domain_dir = os.path.join(user_dir, domain)
        if not os.path.isdir(domain_dir):
            continue

        # cookie
        cookie_path = os.path.join(domain_dir, 'cookie.json')
        if os.path.exists(cookie_path):
            meta_key = f'cookies:{username}:{domain}'
            m = meta['credentials'].get(meta_key, {})
            _, status = _read_encrypted_file(cookie_path)
            if not fingerprint_ok and status == 'ok':
                status = 'key_changed'
            result.append({
                'domain': domain,
                'type': 'cookie',
                'status': status,
                'updated_at': m.get('updated_at', ''),
            })

        # header
        header_path = os.path.join(domain_dir, 'header.json')
        if os.path.exists(header_path):
            meta_key = f'headers:{username}:{domain}'
            m = meta['credentials'].get(meta_key, {})
            hdr_content, status = _read_encrypted_file(header_path)
            if not fingerprint_ok and status == 'ok':
                status = 'key_changed'
            header_names = []
            if hdr_content:
                try:
                    hdrs = json.loads(hdr_content)
                    header_names = list(hdrs.keys()) if isinstance(hdrs, dict) else []
                except (json.JSONDecodeError, AttributeError):
                    pass
            result.append({
                'domain': domain,
                'type': 'header',
                'header_names': header_names,
                'status': status,
                'updated_at': m.get('updated_at', ''),
            })

        # token
        token_path = os.path.join(domain_dir, 'token.json')
        if os.path.exists(token_path):
            meta_key = f'tokens:{username}:{domain}'
            m = meta['credentials'].get(meta_key, {})
            _, status = _read_encrypted_file(token_path)
            if not fingerprint_ok and status == 'ok':
                status = 'key_changed'
            result.append({
                'domain': domain,
                'type': 'token',
                'status': status,
                'updated_at': m.get('updated_at', ''),
            })

    return result


def get_auth_requirements_for_domain(domain: str, base_dir: str = '') -> dict:
    """
    查询站点的 auth 需求（含本地 + 订阅源）。
    返回 {'cookie': bool, 'headers': [str], 'token': bool}
    """
    if not base_dir:
        from site_adapters.services.base import _get_base_dir
        base_dir = _get_base_dir()

    # 使用 engine 的域名匹配，自动覆盖本地 + 订阅源
    from site_adapters.services.config.loader import load_domain_config
    url = f'https://{domain}'
    config = load_domain_config(url, base_dir)
    if not config:
        return {'cookie': False, 'headers': [], 'token': False}

    auth = config.get('auth', {})
    has_cookie = bool(auth.get('cookie'))
    headers = list(auth.get('headers', {}).keys()) if isinstance(auth.get('headers'), dict) else []
    has_token = bool(auth.get('token', {}).get('endpoint'))
    return {'cookie': has_cookie, 'headers': headers, 'token': has_token}


def get_auth_requirements_for_domain_key(domain_key: str, base_dir: str = '') -> dict:
    """查询已解析 domain key 的 auth 需求。"""
    if not base_dir:
        from site_adapters.services.base import _get_base_dir
        base_dir = _get_base_dir()
    if not domain_key:
        return {'cookie': False, 'headers': [], 'token': False}

    from site_adapters.services.config import deep_merge
    from site_adapters.services.config.loader import _cache, _resolve_alias

    all_config = _cache.load(base_dir)
    defaults = all_config.get('*', {})
    raw_config = all_config.get(domain_key)
    if raw_config is None:
        return {'cookie': False, 'headers': [], 'token': False}

    resolved = _resolve_alias(raw_config, all_config) if isinstance(raw_config, dict) else raw_config
    if not isinstance(resolved, dict):
        return {'cookie': False, 'headers': [], 'token': False}

    merged = deep_merge(resolved, defaults) if defaults else resolved
    auth = merged.get('auth', {})
    has_cookie = bool(auth.get('cookie'))
    headers = sorted(auth.get('headers', {}).keys()) if isinstance(auth.get('headers'), dict) else []
    has_token = bool(auth.get('token', {}).get('endpoint'))
    return {'cookie': has_cookie, 'headers': headers, 'token': has_token}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _json_cookie_to_header_string(data_str: str) -> str | None:
    """将 Playwright cookie JSON 转为 header 字符串。复用 cookies 模块。"""
    from site_adapters.services.auth.cookies import _cookie_data_to_string as _convert
    try:
        data = json.loads(data_str)
    except (json.JSONDecodeError, TypeError):
        return data_str if isinstance(data_str, str) else None
    return _convert(data)


def _now_iso() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


# ---------------------------------------------------------------------------
# 用户偏好（toggles）
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
        if key == '*' or key.startswith('_'):
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
