"""
Unified authentication API — 轻量统一层

对 credentials.py、cookies.py、tokens.py 的统一抽象。
不改变底层存储结构，仅提供一致的调用接口。
"""

import logging

from site_adapters.services.auth.cookies import (
    get_cookie_for_domain,
    load_cookie_file,
    verify_and_refresh,
    save_cookie_for_domain,
    has_cookie_for_domain,
)
from site_adapters.services.auth.credentials import (
    get_user_cookie,
    get_user_header,
    get_user_token,
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

    # Cookie
    cookie_str = None
    cookie_file = cookie_config.get('file', '')
    if cookie_file:
        cookie_str = load_cookie_file(cookie_file)
    if not cookie_str:
        cookie_str = get_cookie_for_domain(domain_key)

    # 用户 cookie 覆盖
    if username and cookie_file:
        user_cookie, _ = get_user_cookie(username, domain_key)
        if user_cookie:
            cookie_str = user_cookie

    # User headers
    if username and merged_auth.get('headers'):
        for header_name in merged_auth['headers']:
            if header_name not in headers:
                user_val, _ = get_user_header(username, domain_key, header_name)
                if user_val:
                    headers[header_name] = user_val

    # Token
    merged_token = merged_auth.get('token', {})
    if merged_token.get('endpoint') and username:
        access_token = get_valid_token(merged_token, username, domain_key)
        if access_token:
            token_headers = get_token_header(merged_token, access_token)
            headers.update(token_headers)

    return {
        'headers': headers,
        'cookie_str': cookie_str,
        'cookie_file': cookie_file,
    }
