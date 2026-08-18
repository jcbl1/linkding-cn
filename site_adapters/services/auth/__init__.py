"""
Unified authentication API — 轻量统一层

对 credentials.py、cookies.py 的统一抽象。
不改变底层存储结构，仅提供一致的调用接口。
"""

import logging

from site_adapters.services.auth.cookies import (
    verify_and_refresh,
)
from site_adapters.services.auth.credentials import (
    _normalize_headers_block,
    get_best_cookie,
    get_best_header,
    get_best_headers,
    get_best_token,
    get_best_basic_auth,
    save_user_cookie,
    save_user_header,
    save_user_token,
    delete_user_cookie,
    delete_user_header,
    delete_user_token,
    list_user_credentials,
    get_auth_requirements_for_domain,
    get_auth_requirements_for_domain_key,
)
from site_adapters.services.auth.oauth2 import (
    get_valid_token,
    get_token_header,
    refresh_token as _refresh_token,
)

logger = logging.getLogger(__name__)


def get_auth_for_request(*, url: str, domain_key: str, section: str,
                         merged_auth: dict, merged_http: dict,
                         cookie_config: dict, username: str = '',
                         scope: str = '') -> dict:
    """
    统一获取某次请求所需的全部认证信息。

    scope: effective scope for credential lookup ('' for domain-level,
           'metadata'/'snapshot'/'reader' for section-level).

    返回：
    {
        'headers': dict,        # 要注入的 HTTP headers（含 token header）
        'cookie_str': str|None, # cookie 字符串
    }
    """
    headers = dict(merged_http)

    # Cookie: user credential first, shared credential fallback (within scope)
    cookie_str = None
    best, _ = get_best_cookie(username=username, hostname=domain_key, scope=scope)
    if best:
        cookie_str = best

    # Headers: read ALL saved credentials, then apply config defaults
    if 'headers' in merged_auth:
        headers_norm = _normalize_headers_block(merged_auth['headers'])
        if headers_norm.get('enabled', True):
            # Step 1: read all saved header credentials (not limited to declared names)
            all_saved, _ = get_best_headers(
                username=username, hostname=domain_key, scope=scope)
            for name, val in all_saved.items():
                if name not in headers:
                    headers[name] = val
            # Step 2: for declared headers without saved credentials, use config default
            for header_name, default_val in headers_norm.get('values', {}).items():
                if not isinstance(default_val, str):
                    default_val = ''
                if header_name not in headers and default_val:
                    headers[header_name] = default_val

    # OAuth2: user first, shared fallback (within scope)
    merged_oauth2 = merged_auth.get('oauth2', merged_auth.get('token', {}))
    if merged_oauth2.get('enabled', True) and merged_oauth2.get('endpoint'):
        if username:
            access_token = get_valid_token(merged_oauth2, username, domain_key, scope=scope)
            if access_token:
                token_headers = get_token_header(merged_oauth2, access_token)
                headers.update(token_headers)
        else:
            best_rt, _ = get_best_token(username=username, hostname=domain_key, scope=scope)
            if best_rt:
                token_result = _refresh_token(merged_oauth2, best_rt)
                if token_result:
                    token_headers = get_token_header(merged_oauth2, token_result['access_token'])
                    headers.update(token_headers)

    # Basic Auth: user credential first, shared fallback (within scope)
    merged_basic = merged_auth.get('basic_auth', {})
    if isinstance(merged_basic, dict) and merged_basic.get('enabled', True) and merged_basic:
        best_ba, _ = get_best_basic_auth(username=username, hostname=domain_key, scope=scope)
        if best_ba:
            import base64
            credentials = f"{best_ba['username']}:{best_ba['password']}"
            encoded = base64.b64encode(credentials.encode()).decode()
            headers['Authorization'] = f'Basic {encoded}'
    return {
        'headers': headers,
        'cookie_str': cookie_str,
    }
