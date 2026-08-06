"""
Unified authentication API — 轻量统一层

对 credentials.py、cookies.py、tokens.py 的统一抽象。
不改变底层存储结构，仅提供一致的调用接口。
"""

import logging

from site_adapters.services.auth.cookies import (
    load_cookie_file,
    verify_and_refresh,
)
from site_adapters.services.auth.credentials import (
    get_best_cookie,
    get_best_header,
    get_best_token,
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
from site_adapters.services.auth.tokens import (
    get_valid_token,
    get_token_header,
    verify_and_refresh_token,
    refresh_token as _refresh_token,
)

logger = logging.getLogger(__name__)


def get_auth_for_request(url: str, domain_key: str, section: str,
                         merged_auth: dict, merged_http: dict,
                         cookie_config: dict, username: str = '') -> dict:
    """
    统一获取某次请求所需的全部认证信息。

    返回：
    {
        'headers': dict,        # 要注入的 HTTP headers（含 token header）
        'cookie_str': str|None, # cookie 字符串
        'cookie_file': str,     # cookie 文件路径
    }
    """
    headers = dict(merged_http)

    # Cookie: config file first, then credential-driven (user → shared)
    cookie_str = None
    cookie_file = cookie_config.get('file', '')
    if cookie_file:
        cookie_str = load_cookie_file(cookie_file)
    if not cookie_str:
        best, _ = get_best_cookie(username, domain_key)
        if best:
            cookie_str = best


    # Best headers: user first, shared fallback
    if merged_auth.get('headers'):
        for header_name in merged_auth['headers']:
            if header_name not in headers:
                best_val, _ = get_best_header(username, domain_key, header_name)
                if best_val:
                    headers[header_name] = best_val

    # Token: user first, shared fallback
    merged_token = merged_auth.get('token', {})
    if merged_token.get('endpoint'):
        if username:
            access_token = get_valid_token(merged_token, username, domain_key)
            if access_token:
                token_headers = get_token_header(merged_token, access_token)
                headers.update(token_headers)
        else:
            best_rt, _ = get_best_token(username, domain_key)
            if best_rt:
                token_result = _refresh_token(merged_token, best_rt)
                if token_result:
                    token_headers = get_token_header(merged_token, token_result['access_token'])
                    headers.update(token_headers)

    return {
        'headers': headers,
        'cookie_str': cookie_str,
        'cookie_file': cookie_file,
    }
