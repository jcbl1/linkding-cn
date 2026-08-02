"""
OAuth2 Token 自动刷新

流程：
1. 用户提供 refresh_token（加密存储在 credentials 系统中）
2. 系统根据 site adapter 配置的 token 端点，POST 获取 access_token
3. 缓存 access_token 直到过期
4. 过期后自动刷新
5. 注入为 HTTP header
"""

import json
import logging
import re
import threading
import time

import requests

from site_adapters.services.auth.credentials import (
    get_user_token,
    save_user_token_cache,
    load_user_token_cache,
)

logger = logging.getLogger(__name__)

# 缓存：{username:domain: access_token, expires_at, refresh_token}
_token_cache: dict[str, dict] = {}
_token_cache_lock = threading.Lock()

# 提前刷新的秒数
_REFRESH_AHEAD_SEC = 60


def _cache_key(username: str, domain_key: str) -> str:
    return f'{username}:{domain_key}'


_PATH_PART_RE = re.compile(r'^([^\[\]]+)(?:\[(\d+)\])?$')


def _resolve_json_path(data: dict, path: str) -> str | None:
    """从 JSON 中按 dot path 取值，支持 items[0].token。"""
    current = data
    for part in path.split('.'):
        match = _PATH_PART_RE.match(part)
        if not match or not isinstance(current, dict):
            return None
        current = current.get(match.group(1))
        if current is None:
            return None
        index = match.group(2)
        if index is not None:
            if not isinstance(current, list):
                return None
            idx = int(index)
            if idx >= len(current):
                return None
            current = current[idx]
    return str(current) if current is not None else None


def refresh_token(token_config: dict, refresh_token_value: str) -> dict | None:
    """
    调用 token 端点刷新 access_token。
    返回 {'access_token': ..., 'refresh_token': ..., 'expires_in': ...} 或 None。
    """
    endpoint = token_config.get('endpoint', '')
    if not endpoint or not refresh_token_value:
        return None

    client_id = token_config.get('client_id', '')
    client_secret = token_config.get('client_secret', '')
    grant_type = token_config.get('grant_type', 'refresh_token')
    fmt = token_config.get('format', 'form')
    extra = token_config.get('extra_params', {})

    # 构造请求体
    body = {
        'grant_type': grant_type,
        'refresh_token': refresh_token_value,
    }
    if client_id:
        body['client_id'] = client_id
    if client_secret:
        body['client_secret'] = client_secret
    body.update(extra)

    access_path = token_config.get('access_path', 'access_token')
    refresh_path = token_config.get('refresh_path', 'refresh_token')
    expires_path = token_config.get('expires_path', 'expires_in')

    try:
        if fmt == 'json':
            resp = requests.post(endpoint, json=body, timeout=15)
        else:
            resp = requests.post(endpoint, data=body, timeout=15)

        if resp.status_code != 200:
            logger.error('Token refresh failed: %s status=%d', endpoint, resp.status_code)
            return None

        data = resp.json()

        access_token = _resolve_json_path(data, access_path)
        if not access_token:
            logger.error('Token refresh: no access_token at path %s in response', access_path)
            return None

        new_refresh = _resolve_json_path(data, refresh_path) or refresh_token_value
        expires_in_str = _resolve_json_path(data, expires_path)
        expires_in = int(expires_in_str) if expires_in_str else 3600

        return {
            'access_token': access_token,
            'refresh_token': new_refresh,
            'expires_in': expires_in,
        }

    except Exception as e:
        logger.error('Token refresh exception: %s %s', endpoint, e)
        return None


def get_valid_token(token_config: dict, username: str, domain_key: str) -> str | None:
    """
    获取有效的 access_token。
    优先从缓存读取，过期则自动刷新。
    返回 access_token 字符串或 None。

    锁仅保护缓存读写；HTTP 请求在锁外执行，避免阻塞其他线程。
    """
    if not username or not domain_key:
        return None

    key = _cache_key(username, domain_key)
    now = time.time()

    # 1. 检查内存缓存（lock-free fast path）
    with _token_cache_lock:
        cached = _token_cache.get(key)
    if cached and cached.get('expires_at', 0) > now:
        return cached['access_token']

    # 2. 检查持久化缓存
    stored = load_user_token_cache(username, domain_key)
    if stored and stored.get('expires_at', 0) > now:
        with _token_cache_lock:
            _token_cache[key] = stored
        return stored['access_token']

    # 3. 需要刷新 — 锁内 double-check + 读取 refresh_token，锁外执行 HTTP
    with _token_cache_lock:
        cached = _token_cache.get(key)
        if cached and cached.get('expires_at', 0) > now:
            return cached['access_token']
        rt, _ = get_user_token(username, domain_key)

    if not rt:
        return None

    # HTTP 请求在锁外执行（可能阻塞数秒）
    result = refresh_token(token_config, rt)
    if not result:
        return None

    entry = {
        'access_token': result['access_token'],
        'expires_at': time.time() + result['expires_in'] - _REFRESH_AHEAD_SEC,
    }

    # 4. 写回缓存（double-check：另一个线程可能已经刷新了）
    with _token_cache_lock:
        existing = _token_cache.get(key)
        if existing and existing.get('expires_at', 0) > time.time():
            return existing['access_token']
        _token_cache[key] = entry

    # 5. 持久化（锁外，非关键路径）
    save_user_token_cache(username, domain_key, entry)
    if result['refresh_token'] != rt:
        from site_adapters.services.auth.credentials import save_user_token
        save_user_token(username, domain_key, result['refresh_token'])

    return result['access_token']


def verify_and_refresh_token(token_config: dict, username: str,
                              domain_key: str, verify_context: dict) -> str | None:
    """
    验证 token 有效性，失效则刷新。
    与 cookies.verify_and_refresh 对应。
    """
    token = get_valid_token(token_config, username, domain_key)
    if not token:
        return None

    verify_cfg = token_config.get('verify', {})
    invalid = verify_cfg.get('invalid_patterns', [])
    if not invalid:
        return token

    # 验证（复用 cookies 的声明式验证逻辑）
    from site_adapters.services.auth.cookies import verify_cookie_declarative
    result = verify_cookie_declarative(verify_cfg, verify_context)
    if result.get('valid'):
        return token

    logger.info('Token invalid for %s, refreshing', domain_key)

    # 清除缓存强制刷新。注意：get_valid_token 内部会重新刷新并更新缓存，
    # 如果刷新成功但验证仍失败，第二次调用会从缓存返回（不再循环）。
    with _token_cache_lock:
        _token_cache.pop(_cache_key(username, domain_key), None)

    return get_valid_token(token_config, username, domain_key)


def get_token_header(token_config: dict, access_token: str) -> dict:
    """
    将 access_token 格式化为 HTTP header。
    """
    header_name = token_config.get('header', 'Authorization')
    header_format = token_config.get('header_format', 'Bearer {token}')
    return {header_name: header_format.replace('{token}', access_token)}
