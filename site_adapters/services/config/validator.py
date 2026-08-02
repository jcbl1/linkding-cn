"""
Validator — 配置验证 + 字段分类

合并了原 engine.py 的验证函数和 classifier.py 的字段分类逻辑。
编辑器自动补全函数（get_http_headers_set 等）也在此模块。
"""

import json
import logging
import os
import re

from site_adapters.services.config import (
    is_safe_script_path,
    load_jsonc_file,
    parse_jsonc,
    _resolve_path,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
_BASE_DIR = os.path.dirname(__file__)
_SERVICES_DIR = os.path.dirname(_BASE_DIR)
_SOURCE_ETC_DIR = os.path.join(_SERVICES_DIR, 'engine', 'references')

# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
_http_headers_set: set[str] | None = None
_http_headers_descs: dict[str, str] | None = None
_singlefile_args_set: set[str] | None = None
_defuddle_params_set: set[str] | None = None

# ---------------------------------------------------------------------------
# 各板块合法字段
# ---------------------------------------------------------------------------

DEFAULT_FIELDS = frozenset({
    "timeout", "proxy",
    "request_url", "rewrite_url",
    "http", "auth",
})

METADATA_FIELDS = frozenset({
    "select_title", "select_description", "select_image",
    "script",
    "timeout", "proxy",
    "request_url", "rewrite_url",
    "http", "auth",
})

SNAPSHOT_FIELDS = frozenset({
    "keep_elements", "remove_elements", "process_lazy_images",
    "remove_classes", "set_styles",
    "script",
    "singlefile_args",
    "toggles",
    "timeout", "proxy",
    "request_url", "rewrite_url",
    "http", "auth",
})

READER_FIELDS = frozenset({
    "defuddle_args",
    "timeout", "proxy",
    "http", "auth",
})

# ---------------------------------------------------------------------------
# 兜底列表
# ---------------------------------------------------------------------------

COMMON_HTTP_HEADERS = frozenset({
    "Accept", "Accept-Encoding", "Accept-Language", "Authorization",
    "Cache-Control", "Connection", "Content-Type", "Cookie",
    "Host", "Origin", "Pragma", "Referer", "User-Agent",
    "X-Forwarded-For", "X-Requested-With",
})

_SINGLEFILE_ARGS_FALLBACK = frozenset({
    "--browser-cookies-file", "--browser-script", "--user-agent",
    "--http-proxy-server", "--http-header", "--browser-executable-path",
    "--browser-load-max-time", "--browser-wait-delay", "--browser-wait-until",
    "--remove-hidden-elements", "--remove-frames",
    "--load-deferred-images", "--dump-content", "--error-file",
    "--user-script-enabled", "--compress-CSS", "--compress-HTML",
})

_DEFUDDLE_PARAMS_FALLBACK = frozenset({
    "contentSelector", "removeExactSelectors", "removePartialSelectors",
    "removeHiddenElements", "removeLowScoring", "removeSmallImages",
    "removeImages", "standardize", "url", "markdown", "separateMarkdown",
    "debug", "language", "useAsync", "includeReplies", "profile",
})

# ---------------------------------------------------------------------------
# etc 文件加载
# ---------------------------------------------------------------------------

_ETC_DELIMITERS = ('|', ',', ' ')

def _parse_etc_line(line: str) -> tuple[str, str] | None:
    """Parse one line: 'Name' or 'Name | Description'. Delimiters: | , space"""
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    for i, ch in enumerate(line):
        if ch in _ETC_DELIMITERS:
            return line[:i].strip(), line[i+1:].strip()
    return line, ''

def _get_user_etc_dir() -> str:
    try:
        from django.conf import settings
        return os.path.join(settings.LD_SITE_ADAPTERS_DIR, 'etc')
    except Exception:
        return ''

def _load_from_etc(filename: str, fallback: frozenset) -> tuple[set[str], dict[str, str]]:
    """Load from engine/references/ (user dir first), return (set, {name: desc})"""
    user_dir = _get_user_etc_dir()
    for d in (user_dir, _SOURCE_ETC_DIR):
        path = os.path.join(d, filename) if d else ''
        if path and os.path.exists(path):
            try:
                names, descs = set(), {}
                with open(path, encoding='utf-8') as f:
                    for line in f:
                        parsed = _parse_etc_line(line)
                        if parsed:
                            names.add(parsed[0])
                            if parsed[1]:
                                descs[parsed[0]] = parsed[1]
                if names:
                    return names, descs
            except OSError:
                pass
    return set(fallback), {}


def get_http_headers_set() -> set[str]:
    global _http_headers_set
    if _http_headers_set is None:
        _http_headers_set = _load_from_etc('http_headers.txt', COMMON_HTTP_HEADERS)[0]
    return _http_headers_set


def get_http_headers_descs() -> dict[str, str]:
    """Return HTTP header descriptions (if provided in etc file)"""
    global _http_headers_descs
    if _http_headers_descs is None:
        _http_headers_descs = _load_from_etc('http_headers.txt', COMMON_HTTP_HEADERS)[1]
    return _http_headers_descs


def get_singlefile_args_set() -> set[str]:
    global _singlefile_args_set
    if _singlefile_args_set is None:
        _singlefile_args_set = _load_from_etc('singlefile_args.txt', _SINGLEFILE_ARGS_FALLBACK)[0]
    return _singlefile_args_set


def get_defuddle_params_set() -> set[str]:
    global _defuddle_params_set
    if _defuddle_params_set is None:
        _defuddle_params_set = _load_from_etc('defuddle_params.txt', _DEFUDDLE_PARAMS_FALLBACK)[0]
    return _defuddle_params_set


# ---------------------------------------------------------------------------
# 分类函数
# ---------------------------------------------------------------------------

_SECTION_FIELDS = {
    'default': DEFAULT_FIELDS,
    'metadata': METADATA_FIELDS,
    'snapshot': SNAPSHOT_FIELDS,
    'reader': READER_FIELDS,
}

def classify_field(section: str, key: str) -> str:
    """
    Return field classification:
    - "field": known field for this section
    - "unknown": unrecognized field
    """
    fields = _SECTION_FIELDS.get(section, frozenset())
    if key in fields:
        return "field"
    return "unknown"


def is_http_header(name: str) -> bool:
    headers = get_http_headers_set()
    if headers:
        return name in headers
    return bool(re.match(r'^[A-Z]', name))


def is_known_singlefile_arg(name: str) -> bool:
    return name in get_singlefile_args_set()


def is_known_defuddle_param(name: str) -> bool:
    return name in get_defuddle_params_set()


# ---------------------------------------------------------------------------
# 字段分离
# ---------------------------------------------------------------------------

def separate_http_fields(data: dict) -> tuple[dict, dict]:
    """Separate http section: framework fields vs HTTP headers."""
    framework = {"timeout", "proxy", "cookie"}
    app = {}
    headers = {}
    for key, value in data.items():
        if key in framework:
            app[key] = value
        else:
            headers[key] = value
    return app, headers


def validate_section_fields(section: str, data: dict) -> list[str]:
    """Validate section fields, return warnings. Unknown fields are discarded."""
    warnings = []
    fields = _SECTION_FIELDS.get(section, frozenset())
    for key in data:
        if key not in fields:
            warnings.append(f"WARN: {section}.{key} is unknown, discarded")
    return warnings


# ---------------------------------------------------------------------------
# 配置验证（从 engine.py 迁移）
# ---------------------------------------------------------------------------

def _is_safe_name(name: str) -> bool:
    return bool(name) and name == os.path.basename(name) and '/' not in name and '\\' not in name and '..' not in name


def _validate_subscriptions(issues: list[str], subscriptions):
    if subscriptions is None:
        return
    if not isinstance(subscriptions, list):
        issues.append("ERROR: _subscriptions 必须是数组")
        return
    for index, sub in enumerate(subscriptions):
        label = f"_subscriptions[{index}]"
        if not isinstance(sub, dict):
            issues.append(f"ERROR: {label} 必须是对象")
            continue
        from urllib.parse import urlparse
        parsed = urlparse(sub.get('url', ''))
        if parsed.scheme != 'https' or not parsed.netloc:
            issues.append(f"ERROR: {label}.url 必须是 HTTPS URL")
        name = sub.get('name', '')
        if name and not _is_safe_name(name):
            issues.append(f"ERROR: {label}.name 非法")
        interval = sub.get('update_interval', 86400)
        if not isinstance(interval, int) or interval <= 0:
            issues.append(f"ERROR: {label}.update_interval 必须是正整数")


def _check_exclusive(issues, label, data, groups):
    """检查互斥参数组，同组内超过一个则警告"""
    for group in groups:
        present = []
        for key in group:
            val = data
            for part in key.split('.'):
                val = (val if isinstance(val, dict) else {}).get(part)
            if val:
                present.append(key)
        if len(present) > 1:
            issues.append(f"WARN: {label}: {', '.join(present)} 互斥，只有 {present[0]} 生效")


def _validate_cookie_block(issues: list[str], label: str, cookie: dict, file_dir: str):
    """Validate a cookie config block."""
    if not isinstance(cookie, dict):
        issues.append(f"ERROR: {label} must be an object")
        return
    valid_types = ("anon", "login")
    ctype = cookie.get("type", "anon")
    if ctype not in valid_types:
        issues.append(f"ERROR: {label}.type must be one of {valid_types}, got '{ctype}'")
    # verify
    verify = cookie.get("verify")
    if verify is not None:
        if not isinstance(verify, dict):
            issues.append(f"ERROR: {label}.verify must be an object")
        else:
            check = verify.get("check")
            if check is not None:
                if not isinstance(check, list) or not all(isinstance(s, str) for s in check):
                    issues.append(f"ERROR: {label}.verify.check must be a string array")
            invalid_pats = verify.get("invalid_patterns")
            if invalid_pats is not None:
                if not isinstance(invalid_pats, list) or not all(isinstance(s, str) for s in invalid_pats):
                    issues.append(f"ERROR: {label}.verify.invalid_patterns must be a string array")
            valid_selector = verify.get("valid_selector")
            if valid_selector is not None and not isinstance(valid_selector, str):
                issues.append(f"ERROR: {label}.verify.valid_selector must be a string")
            # warn about unknown keys
            for key in verify:
                if key not in ("check", "invalid_patterns", "valid_selector"):
                    issues.append(f"WARN: {label}.verify.{key} is unknown, will be ignored")
    # refresh
    refresh = cookie.get("refresh")
    if refresh is not None:
        if not isinstance(refresh, dict):
            issues.append(f"ERROR: {label}.refresh must be an object")
        else:
            for key in refresh:
                if key not in ("url", "wait_cookie", "timeout"):
                    issues.append(f"WARN: {label}.refresh.{key} is unknown, will be ignored")
    # refresh_interval
    ri = cookie.get("refresh_interval")
    if ri is not None:
        if not isinstance(ri, (int, float)) or ri <= 0:
            issues.append(f"ERROR: {label}.refresh_interval must be a positive number")
    # warn about unknown keys at cookie level
    for key in cookie:
        if key not in ("type", "verify", "refresh", "refresh_interval"):
            issues.append(f"WARN: {label}.{key} is unknown, will be ignored")


def _validate_auth_block(issues: list[str], label: str, auth: dict, file_dir: str):
    """Validate an auth config block."""
    if not isinstance(auth, dict):
        issues.append(f"ERROR: {label} must be an object")
        return
    # Validate cookie sub-block
    cookie = auth.get('cookie')
    if cookie is not None:
        _validate_cookie_block(issues, f"{label}.cookie", cookie, file_dir)
    # Validate headers sub-block
    headers = auth.get('headers')
    if headers is not None:
        if not isinstance(headers, dict):
            issues.append(f"ERROR: {label}.headers must be an object")
        else:
            for name, config in headers.items():
                if not isinstance(name, str):
                    issues.append(f"ERROR: {label}.headers key must be a string")
                if config and not isinstance(config, dict):
                    issues.append(f"ERROR: {label}.headers.{name} must be an object")
    # Validate token sub-block
    token = auth.get('token')
    if token is not None:
        if not isinstance(token, dict):
            issues.append(f"ERROR: {label}.token must be an object")
        else:
            valid_types = ("anon", "login")
            ttype = token.get("type")
            if ttype is None:
                issues.append(f"ERROR: {label}.token.type is required")
            elif ttype not in valid_types:
                issues.append(f"ERROR: {label}.token.type must be one of {valid_types}, got '{ttype}'")
            endpoint = token.get("endpoint")
            if not isinstance(endpoint, str) or not endpoint.strip():
                issues.append(f"ERROR: {label}.token.endpoint must be a non-empty string")
            for key in ("client_id", "client_secret", "grant_type", "format", "access_path", "refresh_path", "expires_path", "header", "header_format"):
                value = token.get(key)
                if value is not None and not isinstance(value, str):
                    issues.append(f"ERROR: {label}.token.{key} must be a string")
            extra_params = token.get("extra_params")
            if extra_params is not None:
                if not isinstance(extra_params, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in extra_params.items()):
                    issues.append(f"ERROR: {label}.token.extra_params must be a string map")
            verify = token.get("verify")
            if verify is not None:
                if not isinstance(verify, dict):
                    issues.append(f"ERROR: {label}.token.verify must be an object")
                else:
                    check = verify.get("check")
                    if check is not None:
                        if not isinstance(check, list) or not all(isinstance(s, str) for s in check):
                            issues.append(f"ERROR: {label}.token.verify.check must be a string array")
                    invalid_pats = verify.get("invalid_patterns")
                    if invalid_pats is not None:
                        if not isinstance(invalid_pats, list) or not all(isinstance(s, str) for s in invalid_pats):
                            issues.append(f"ERROR: {label}.token.verify.invalid_patterns must be a string array")
                    valid_selector = verify.get("valid_selector")
                    if valid_selector is not None and not isinstance(valid_selector, str):
                        issues.append(f"ERROR: {label}.token.verify.valid_selector must be a string")
                    for key in verify:
                        if key not in ("check", "invalid_patterns", "valid_selector"):
                            issues.append(f"WARN: {label}.token.verify.{key} is unknown, will be ignored")
    # Warn about unknown keys
    for key in auth:
        if key not in ('cookie', 'headers', 'token'):
            issues.append(f"WARN: {label}.{key} is unknown, will be ignored")


def _validate_domain_config(issues: list[str], label: str, data: dict, file_dir: str):
    if not isinstance(data, dict):
        issues.append(f"ERROR: {label} top level must be an object")
        return
    if data.get('type') == 'alias':
        if not data.get('target'):
            issues.append(f"ERROR: {label} alias missing target")
        return
    # Validate auth at top level (shared across all sections)
    top_auth = data.get('auth')
    if top_auth is not None:
        _validate_auth_block(issues, f"{label}.auth", top_auth, file_dir)

    # Validate default section
    default = data.get('default', {})
    if default:
        if not isinstance(default, dict):
            issues.append(f"ERROR: {label}.default must be an object")
        else:
            for key in default:
                if classify_field('default', key) == 'unknown':
                    issues.append(f"WARN: {label}.default.{key} is unknown, will be ignored at runtime")
            auth = default.get('auth')
            if auth is not None:
                _validate_auth_block(issues, f"{label}.default.auth", auth, file_dir)

    # Validate sections
    for section in ('metadata', 'snapshot', 'reader'):
        sec = data.get(section, {})
        if not sec:
            continue
        if not isinstance(sec, dict):
            issues.append(f"ERROR: {label}.{section} must be an object")
            continue
        for key, value in sec.items():
            if classify_field(section, key) == 'unknown':
                issues.append(f"WARN: {label}.{section}.{key} is unknown, will be ignored at runtime")
            if key.endswith('_script') or key == 'script':
                script_path = _resolve_path(value, file_dir) if isinstance(value, str) else ''
                if value and not isinstance(value, str):
                    issues.append(f"ERROR: {label}.{section}.{key} must be a string path")
                elif script_path and not os.path.exists(script_path):
                    issues.append(f"ERROR: {label}.{section}.{key} script not found: {value}")
                elif script_path and not is_safe_script_path(script_path, file_dir):
                    issues.append(f"ERROR: {label}.{section}.{key} script path not allowed: {value}")
        auth = sec.get('auth')
        if auth is not None:
            _validate_auth_block(issues, f"{label}.{section}.auth", auth, file_dir)
        if section == 'snapshot':
            args = sec.get('singlefile_args', {})
            if args and not isinstance(args, dict):
                issues.append(f"ERROR: {label}.snapshot.singlefile_args must be an object")
            for arg in (args if isinstance(args, dict) else {}):
                if not is_known_singlefile_arg(arg):
                    issues.append(f"WARN: {label}.snapshot.singlefile_args.{arg} unknown")
            _check_exclusive(issues, f"{label}.{section}", sec, [
                ('script', 'keep_elements', 'remove_elements', 'remove_classes', 'set_styles', 'singlefile_args'),
            ])
        if section == 'reader':
            args = sec.get('defuddle_args', {})
            if args and not isinstance(args, dict):
                issues.append(f"ERROR: {label}.reader.defuddle_args must be an object")
            for arg in (args if isinstance(args, dict) else {}):
                if not is_known_defuddle_param(arg):
                    issues.append(f"WARN: {label}.reader.defuddle_args.{arg} unknown")


def validate_config(base_dir: str, domain_filename: str = '') -> list[str]:
    issues = []
    if not os.path.isdir(base_dir):
        issues.append(f"ERROR: 目录不存在: {base_dir}")
        return issues

    if not domain_filename:
        # 检查 global.jsonc
        global_path = os.path.join(base_dir, 'global.jsonc')
        if os.path.exists(global_path):
            try:
                global_data = load_jsonc_file(global_path)
                if not isinstance(global_data, dict):
                    issues.append("ERROR: global.jsonc 顶层必须是对象")
                else:
                    _validate_subscriptions(issues, global_data.get('_subscriptions'))
                    if '*' in global_data:
                        _validate_domain_config(issues, 'global.jsonc.*', global_data.get('*'), os.path.dirname(global_path))
            except json.JSONDecodeError as e:
                issues.append(f"ERROR: global.jsonc 解析失败: {e}")

    # 检查域名文件
    domains_dir = os.path.join(base_dir, 'domains')
    if os.path.isdir(domains_dir):
        filenames = [domain_filename] if domain_filename else os.listdir(domains_dir)
        for fname in filenames:
            if not (fname.endswith('.jsonc') or fname.endswith('.json')):
                continue
            if fname != os.path.basename(fname) or '/' in fname or '\\' in fname or '..' in fname:
                issues.append(f"ERROR: 非法文件名: {fname}")
                continue
            fpath = os.path.join(domains_dir, fname)
            if not os.path.exists(fpath):
                issues.append(f"ERROR: 文件不存在: {fname}")
                continue
            try:
                data = load_jsonc_file(fpath)
                _validate_domain_config(issues, fname, data, os.path.dirname(fpath))
            except json.JSONDecodeError as e:
                issues.append(f"ERROR: {fname} 解析失败: {e}")

    return issues
