"""
Validator — 配置验证 + 字段分类
"""

import json
import logging
import os

from site_adapters.services.config import (
    is_safe_script_path,
    load_jsonc_file,
    parse_jsonc,
    _resolve_path,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
_singlefile_args_set: set[str] | None = None
_defuddle_params_set: set[str] | None = None

# ---------------------------------------------------------------------------
# 各板块合法字段
# ---------------------------------------------------------------------------
from site_adapters.services.config.fields import (
    DEFAULT_FIELDS,
    DEFUDDLE_ARG_FIELDS,
    METADATA_FIELDS,
    SNAPSHOT_FIELDS,
    READER_FIELDS,
    ALL_SECTIONS,
    SINGLEFILE_ARG_NAMES,
)


def get_singlefile_args_set() -> set[str]:
    global _singlefile_args_set
    if _singlefile_args_set is None:
        _singlefile_args_set = set(SINGLEFILE_ARG_NAMES)
    return _singlefile_args_set


def get_defuddle_params_set() -> set[str]:
    global _defuddle_params_set
    if _defuddle_params_set is None:
        _defuddle_params_set = {
            key for key, info in DEFUDDLE_ARG_FIELDS.items()
            if not info.get("reserved")
        }
    return _defuddle_params_set


# ---------------------------------------------------------------------------
# 分类函数
# ---------------------------------------------------------------------------

_SECTION_FIELDS = {
    'defaults': set(DEFAULT_FIELDS.keys()),
    'metadata': set(METADATA_FIELDS.keys()),
    'snapshot': set(SNAPSHOT_FIELDS.keys()),
    'reader': set(READER_FIELDS.keys()),
}

def classify_field(section: str, key: str) -> str:
    """
    Return field classification:
    - "field": known field for this section
    - "unknown": unrecognized field
    """
    fields = _SECTION_FIELDS.get(section, set())
    if key in fields:
        return "field"
    return "unknown"


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
    fields = _SECTION_FIELDS.get(section, set())
    for key in data:
        if key not in fields:
            warnings.append(f"WARN: {section}.{key} is unknown, discarded")
    return warnings


# ---------------------------------------------------------------------------
# 配置验证（从 engine.py 迁移）
# ---------------------------------------------------------------------------

def _is_safe_name(name: str) -> bool:
    return bool(name) and name == os.path.basename(name) and '/' not in name and '\\' not in name and '..' not in name


def _validate_subscriptions(issues: list[str], adapters):
    if adapters is None:
        return
    if not isinstance(adapters, list):
        issues.append("ERROR: _adapters 必须是数组")
        return
    from site_adapters.services.subscriptions import is_remote_source
    for index, adp in enumerate(adapters):
        label = f"_adapters[{index}]"
        if not isinstance(adp, dict):
            issues.append(f"ERROR: {label} 必须是对象")
            continue
        name = adp.get('name', '')
        if name and not _is_safe_name(name):
            issues.append(f"ERROR: {label}.name 非法")
        source = adp.get('source', '')
        if source:
            if is_remote_source(source):
                from urllib.parse import urlparse
                parsed = urlparse(source)
                if parsed.scheme != 'https' or not parsed.netloc:
                    issues.append(f"ERROR: {label}.source 必须是 HTTPS URL")
            elif not os.path.exists(source):
                issues.append(f"WARN: {label}.source 本地文件不存在: {source}")
        interval = adp.get('update_interval', 86400)
        if not isinstance(interval, int) or interval <= 0:
            issues.append(f"ERROR: {label}.update_interval 必须是正整数")


def _validate_cookie_block(issues: list[str], label: str, cookie: dict, file_dir: str):
    """Validate a cookie config block."""
    if not isinstance(cookie, dict):
        issues.append(f"ERROR: {label} must be an object")
        return
    valid_types = ("auto", "login")
    ctype = cookie.get("type", "auto")
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
            invalid_pats = verify.get("content_check", {}).get("invalid_patterns", []) if isinstance(verify.get("content_check"), dict) else verify.get("invalid_patterns", [])
            if invalid_pats is not None:
                if not isinstance(invalid_pats, list) or not all(isinstance(s, str) for s in invalid_pats):
                    issues.append(f"ERROR: {label}.verify.invalid_patterns must be a string array")
            valid_selector = verify.get("valid_selector")
            if valid_selector is not None and not isinstance(valid_selector, str):
                issues.append(f"ERROR: {label}.verify.valid_selector must be a string")
            # warn about unknown keys
            for key in verify:
                if key not in ("check", "check_selectors", "invalid_patterns", "invalid_selectors", "valid_selector", "http_head_probe", "content_check", "probe"):
                    issues.append(f"WARN: {label}.verify.{key} is unknown, will be ignored")
    # refresh
    refresh = cookie.get("refresh")
    if refresh is not None:
        if not isinstance(refresh, dict):
            issues.append(f"ERROR: {label}.refresh must be an object")
        else:
            for key in refresh:
                if key not in ("url", "wait_cookie", "timeout", "interval"):
                    issues.append(f"WARN: {label}.refresh.{key} is unknown, will be ignored")
    # refresh_interval
    refresh_block = cookie.get("refresh")
    ri = None
    if isinstance(refresh_block, dict):
        ri = refresh_block.get("interval", cookie.get("refresh_interval"))
    else:
        ri = cookie.get("refresh_interval")
    if ri is not None and (not isinstance(ri, (int, float)) or ri <= 0):
        issues.append(f"ERROR: {label}.refresh.interval must be a positive number")
    # warn about unknown keys at cookie level
    for key in cookie:
        if key not in ("enabled", "type", "verify", "refresh", "help"):
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
                if name == "help":
                    continue
                if not isinstance(name, str):
                    issues.append(f"ERROR: {label}.headers key must be a string")
                if config is not None and not isinstance(config, str):
                    issues.append(f"ERROR: {label}.headers.{name} must be a string")
    # Validate oauth2 sub-block
    oauth2 = auth.get("oauth2")
    if oauth2 is not None:
        prefix = f"{label}.oauth2"
        if not isinstance(oauth2, dict):
            issues.append(f"ERROR: {prefix} must be an object")
        else:
            endpoint = oauth2.get("endpoint")
            if not isinstance(endpoint, str) or not endpoint.strip():
                issues.append(f"ERROR: {prefix}.endpoint must be a non-empty string")
            for key in ("client_id", "client_secret", "grant_type", "format",
                         "access_token_path", "access_path",
                         "refresh_token_path", "refresh_path",
                         "expires_in_path", "expires_path",
                         "header", "header_format"):
                value = oauth2.get(key)
                if value is not None and not isinstance(value, str):
                    issues.append(f"ERROR: {prefix}.{key} must be a string")
            extra_params = oauth2.get("extra_params")
            if extra_params is not None:
                if not isinstance(extra_params, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in extra_params.items()):
                    issues.append(f"ERROR: {prefix}.extra_params must be a string map")
    # Validate basic_auth sub-block
    basic_auth = auth.get('basic_auth')
    if basic_auth is not None:
        if not isinstance(basic_auth, dict):
            issues.append(f"ERROR: {label}.basic_auth must be an object")
    # Warn about unknown keys
    for key in auth:
        if key not in ('cookie', 'headers', 'oauth2', 'basic_auth'):
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

    # Validate defaults section
    default = data.get('defaults', {})
    if default:
        if not isinstance(default, dict):
            issues.append(f"ERROR: {label}.defaults must be an object")
        else:
            for key in default:
                if classify_field('defaults', key) == 'unknown':
                    issues.append(f"WARN: {label}.defaults.{key} is unknown, will be ignored at runtime")
            auth = default.get('auth')
            if auth is not None:
                _validate_auth_block(issues, f"{label}.defaults.auth", auth, file_dir)

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
            if key == 'scripts':
                if not isinstance(value, list):
                    issues.append(f"ERROR: {label}.{section}.scripts must be an array")
                else:
                    replace_count = 0
                    seen_hooks = set()
                    for i, item in enumerate(value):
                        if not isinstance(item, dict):
                            issues.append(f"ERROR: {label}.{section}.scripts[{i}] must be an object")
                            continue
                        script_path = item.get('path', '')
                        hook_val = item.get('hook', '')
                        if hook_val not in ('before', 'after', 'replace'):
                            issues.append(f"ERROR: {label}.{section}.scripts[{i}].hook must be before/after/replace, got '{hook_val}'")
                        if hook_val == 'replace':
                            replace_count += 1
                        if script_path:
                            if '/' not in script_path and not os.path.isabs(script_path):
                                script_path = os.path.join('scripts', script_path)
                            resolved = _resolve_path(script_path, file_dir) if not os.path.isabs(script_path) else script_path
                            if not os.path.exists(resolved):
                                issues.append(f"ERROR: {label}.{section}.scripts[{i}].path not found: {item.get('path')}")
                            elif not is_safe_script_path(resolved, file_dir):
                                issues.append(f"ERROR: {label}.{section}.scripts[{i}].path not allowed: {item.get('path')}")
                    if replace_count > 1:
                        issues.append(f"ERROR: {label}.{section}.scripts has {replace_count} replace hooks, only 1 allowed")
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
        if section in ('metadata', 'snapshot'):
            content_type = sec.get('content_type')
            if content_type is not None and content_type not in ('html', 'xml', 'json'):
                issues.append(
                    f"ERROR: {label}.{section}.content_type must be html/xml/json, got '{content_type}'"
                )
        if section == 'metadata':
            xmlns = sec.get('xmlns')
            if xmlns is not None and (
                not isinstance(xmlns, dict)
                or not all(
                    isinstance(prefix, str)
                    and isinstance(uri, str)
                    and prefix
                    and uri
                    for prefix, uri in xmlns.items()
                )
            ):
                issues.append(
                    f"ERROR: {label}.metadata.xmlns must be a string-to-string prefix map"
                )
        if section == 'snapshot':
            args = sec.get('singlefile_args', {})
            if args and not isinstance(args, dict):
                issues.append(f"ERROR: {label}.snapshot.singlefile_args must be an object")
            for arg in (args if isinstance(args, dict) else {}):
                if not is_known_singlefile_arg(arg):
                    issues.append(f"WARN: {label}.snapshot.singlefile_args.{arg} unknown")
            carousels = sec.get('process_carousels')
            if carousels is not None:
                if not isinstance(carousels, list) or not all(
                    isinstance(item, str) and item.strip() for item in carousels
                ):
                    issues.append(
                        f"ERROR: {label}.snapshot.process_carousels must be an array of selector strings"
                    )
            scripts = sec.get('scripts')
            has_replace = (
                isinstance(scripts, list)
                and any(isinstance(item, dict) and item.get('hook') == 'replace' for item in scripts)
            )
            if has_replace:
                declarative_fields = [
                    'keep_elements', 'remove_elements', 'remove_classes',
                    'set_styles', 'singlefile_args', 'process_carousels',
                ]
                present = [
                    key for key in declarative_fields
                    if sec.get(key) not in (None, [], {})
                ]
                if present:
                    issues.append(
                        f"WARN: {label}.{section}: replace script bypasses "
                        f"{', '.join(present)}"
                    )
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

    adapters_dir = os.path.join(base_dir, 'adapters')

    if not domain_filename:
        config_path = os.path.join(adapters_dir, 'config.jsonc')
        if os.path.exists(config_path):
            try:
                config_data = load_jsonc_file(config_path)
                if not isinstance(config_data, dict):
                    issues.append("ERROR: config.jsonc 顶层必须是对象")
                else:
                    _validate_subscriptions(issues, config_data.get('_adapters'))
            except json.JSONDecodeError as e:
                issues.append(f"ERROR: config.jsonc 解析失败: {e}")

        if os.path.isdir(adapters_dir):
            adapters_list = []
            if os.path.exists(config_path):
                try:
                    cfg = load_jsonc_file(config_path)
                    if isinstance(cfg, dict):
                        adapters_list = cfg.get('_adapters', [])
                except Exception:
                    pass
            if not isinstance(adapters_list, list):
                adapters_list = []

            for item in adapters_list:
                if not isinstance(item, dict):
                    continue
                if item.get('enabled') is False:
                    continue
                name = item.get('name', '')
                source = item.get('source', '')
                if not source:
                    issues.append(f"WARN: 适配器缺少 source: {name}")
                    continue
                from site_adapters.services.subscriptions import resolve_adapter_path
                file_path = resolve_adapter_path(name, source, adapters_dir)

                if not os.path.exists(file_path):
                    issues.append(f"WARN: 适配器文件不存在: {name}")
                    continue

                try:
                    data = load_jsonc_file(file_path)
                    if not isinstance(data, dict):
                        issues.append(f"ERROR: {name} 顶层必须是对象")
                        continue
                    domains = data.get('domains', {})
                    if isinstance(domains, dict):
                        for domain_key, domain_config in domains.items():
                            label = f"{name}/{domain_key}"
                            _validate_domain_config(issues, label, domain_config, os.path.dirname(file_path))
                    glob_defaults = data.get('defaults')
                    global_defaults_data = data.get('_builtin')
                    if glob_defaults and isinstance(glob_defaults, dict):
                        _validate_domain_config(issues, f"{name}.defaults", glob_defaults, os.path.dirname(file_path))
                    if global_defaults_data and isinstance(global_defaults_data, dict):
                        _validate_domain_config(issues, f"{name}._builtin", global_defaults_data, os.path.dirname(file_path))
                except json.JSONDecodeError as e:
                    issues.append(f"ERROR: {name} 解析失败: {e}")
    else:
        from site_adapters.services.subscriptions import _read_subscription_file, resolve_adapter_path
        adapters_list = []
        config_path = os.path.join(adapters_dir, 'config.jsonc')
        if os.path.exists(config_path):
            try:
                cfg = load_jsonc_file(config_path)
                if isinstance(cfg, dict):
                    adapters_list = cfg.get('_adapters', [])
            except Exception:
                pass
        if not isinstance(adapters_list, list):
            adapters_list = []

        found = False
        for item in adapters_list:
            if not isinstance(item, dict) or item.get('enabled') is False:
                continue
            source = item.get('source', '')
            if not source:
                continue
            file_path = resolve_adapter_path(item.get('name', ''), source, adapters_dir)
            data = _read_subscription_file(file_path)
            if data and isinstance(data.get('domains'), dict) and domain_filename in data['domains']:
                _validate_domain_config(issues, domain_filename, data['domains'][domain_filename], os.path.dirname(file_path))
                found = True
                break
        if not found:
            issues.append(f"ERROR: 域名未找到: {domain_filename}")

    return issues
