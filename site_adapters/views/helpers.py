"""
Shared helpers for site adapter views.
"""
import json
import logging
import os
import re
from functools import wraps
from pathlib import Path

from django.conf import settings as django_settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse

from bookmarks.utils import atomic_write
from site_adapters.services.config import (
    parse_jsonc,
)
from site_adapters.services.config.jsonc import (
    update_key as _replace_top_level_jsonc_value,
)
from site_adapters.services.config.validator import (
    get_defuddle_params_set,
    get_http_headers_descs,
    get_http_headers_set,
    get_singlefile_args_set,
)

logger = logging.getLogger(__name__)

TEST_ASSETS_DIR = os.path.join(os.path.dirname(django_settings.LD_ASSET_FOLDER), 'site_adapters', 'test_assets')
from site_adapters.services.base import _get_adapters_dir, _get_base_dir


def _ensure_base_dirs():
    base_dir = _get_base_dir()
    adapters_dir = _get_adapters_dir()
    os.makedirs(adapters_dir, exist_ok=True)
    for name in ('cookies', 'logs', 'test_assets'):
        os.makedirs(os.path.join(base_dir, name), exist_ok=True)

    # 确保 defaults 适配器存在
    _ensure_defaults_adapter(adapters_dir)
    # 清理磁盘已删除的适配器条目
    _cleanup_stale_adapters(adapters_dir)


def _cleanup_stale_adapters(adapters_dir: str):
    """清理磁盘上已被删除的适配器条目。

    - 本地适配器：源文件不存在则从 config.jsonc 删除条目
    - 远程适配器：缓存文件不存在则保留条目（可重新下载）
    - defaults：文件不存在则由 _ensure_defaults_adapter 重建，此处不处理
    """
    from site_adapters.services.subscriptions import is_remote_source, resolve_adapter_path
    config_path = os.path.join(adapters_dir, 'config.jsonc')
    if not os.path.exists(config_path):
        return
    try:
        data, text = _load_config()
    except Exception:
        return
    adapters = data.get('_adapters', [])
    if not isinstance(adapters, list):
        return
    cleaned = []
    changed = False
    for item in adapters:
        if not isinstance(item, dict):
            cleaned.append(item)
            continue
        source = item.get('source', '')
        if not source:
            continue
        if is_remote_source(source):
            # 远程适配器：始终保留
            cleaned.append(item)
            continue
        # 本地适配器：检查文件是否存在
        try:
            file_path = resolve_adapter_path(item.get('name', ''), source, adapters_dir)
            if os.path.exists(file_path):
                cleaned.append(item)
            else:
                logger.info('Removing stale local adapter: %s (%s)', item.get('name', ''), source)
                changed = True
        except Exception:
            cleaned.append(item)
    if changed:
        _save_adapters_list(cleaned)

def _ensure_defaults_adapter(adapters_dir: str):
    """首次部署时创建 defaults 适配器和 config.jsonc。"""
    import json
    from bookmarks.utils import atomic_write

    config_path = os.path.join(adapters_dir, 'config.jsonc')
    defaults_dir = os.path.join(adapters_dir, 'defaults')
    defaults_file = os.path.join(defaults_dir, 'adapters.jsonc')

    # 创建 config.jsonc（如果不存在）
    if not os.path.exists(config_path):
        default_config = {
            '_adapters': [{
                'id': 'defaults',
                'name': 'defaults',
                'source': './defaults/adapters.jsonc',
                'update_interval': 86400,
                'enabled': True,
            }],
        }
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        atomic_write(config_path, json.dumps(default_config, indent=2, ensure_ascii=False))

    # 创建 defaults/adapters.jsonc（如果不存在）
    if not os.path.exists(defaults_file):
        default_data = {'_meta': {'id': 'defaults', 'name': 'defaults', 'description': 'Built-in system adapter with the highest priority. Fields defined in the _builtin section serve as the fallback when no domain adapter matches.'}, 'defaults': {}, '_builtin': {}, 'domains': {}}
        os.makedirs(defaults_dir, exist_ok=True)
        atomic_write(defaults_file, json.dumps(default_data, indent=2, ensure_ascii=False))


def site_adapters_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not (request.user.is_active and request.user.is_superuser):
            raise PermissionDenied()
        return view_func(request, *args, **kwargs)

    return login_required(wrapped)






def _resolve_domain_path(filename: str) -> str:
    """解析域名文件路径。已废弃：域名现在存储在适配器文件内部。"""
    raise NotImplementedError("Domains are now stored inside adapter files, not as separate files")


def _invalidate_site_adapters_cache():
    from site_adapters.services.config.loader import _cache
    _cache.invalidate()


def _is_safe_subscription_name(name: str) -> bool:
    if not name:
        return True
    if name.startswith('.'):
        return False
    if '/' in name or '\\' in name or '..' in name:
        return False
    return bool(re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]*$', name))








# ---------------------------------------------------------------------------
# Config (config.jsonc) management
# ---------------------------------------------------------------------------

def _config_path() -> str:
    return os.path.join(_get_adapters_dir(), 'config.jsonc')


def _load_config() -> tuple[dict, str]:
    path = _config_path()
    if not os.path.exists(path):
        return {}, ''
    with open(path, encoding='utf-8') as f:
        text = f.read()
    if not text.strip():
        return {}, text
    data = parse_jsonc(text)
    if not isinstance(data, dict):
        raise ValueError('config.jsonc must be an object')
    return data, text


def _save_adapters_list(adapters: list[dict]):
    path = _config_path()
    text = ''
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            text = f.read()
    new_text = _replace_top_level_jsonc_value(text, '_adapters', adapters)
    atomic_write(path, new_text)
    _invalidate_site_adapters_cache()






def _ensure_defaults_first(adapters: list):
    """确保 id="defaults" 的适配器排在列表第一位。"""
    defaults_idx = None
    for i, a in enumerate(adapters):
        if isinstance(a, dict) and a.get('id') == 'defaults':
            defaults_idx = i
            break
    if defaults_idx is not None and defaults_idx > 0:
        adapters.insert(0, adapters.pop(defaults_idx))


def _get_adapters_list() -> list:
    data, _ = _load_config()
    adapters = data.get("_adapters", [])
    if not isinstance(adapters, list):
        return []
    # 去重：id+name 均相同时视为重复，保留最先出现的
    seen_keys = set()
    deduped = []
    for item in adapters:
        if not isinstance(item, dict):
            deduped.append(item)
            continue
        key = (item.get('id', ''), item.get('name', ''))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(item)
    # 确保 defaults 始终排在第一位
    _ensure_defaults_first(deduped)
    return deduped


def _get_disabled_domains() -> set[str]:
    """Read _disabled_domains from config.jsonc. Returns a set of domain keys."""
    data, _ = _load_config()
    disabled = data.get('_disabled_domains', [])
    if not isinstance(disabled, list):
        return set()
    return set(disabled)


def _toggle_domain_disabled(domain_key: str, disabled: bool):
    """Add or remove domain_key from _disabled_domains in config.jsonc."""
    current = _get_disabled_domains()
    if disabled:
        current.add(domain_key)
    else:
        current.discard(domain_key)
    path = _config_path()
    text = ''
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            text = f.read()
    new_text = _replace_top_level_jsonc_value(text, '_disabled_domains', sorted(current))
    atomic_write(path, new_text)
    _invalidate_site_adapters_cache()



# Test helpers
def _sanitize_url_for_filename(url: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in url)[:120]


def _extract_cleanup_stats(html_path: str) -> dict:
    try:
        with open(html_path, encoding='utf-8') as f:
            head = f.read(20000)
    except OSError:
        return {}
    marker = 'linkding-cleanup-stats'
    idx = head.find(marker)
    if idx < 0:
        return {}
    for quote in ['"', "'"]:
        content_marker = f'content={quote}'
        content_idx = head.find(content_marker, idx)
        if content_idx >= 0:
            start = content_idx + len(content_marker)
            end = head.find(quote, start)
            if end >= 0:
                try:
                    return json.loads(head[start:end].replace('&quot;', '"'))
                except json.JSONDecodeError:
                    pass
    return {}


# Schema helpers
def _schema_type(prop: dict) -> str:
    if '$ref' in prop:
        return prop['$ref'].rsplit('/', 1)[-1]
    if 'oneOf' in prop:
        return ' | '.join(_schema_type(item) for item in prop['oneOf'])
    value = prop.get('type', 'any')
    if isinstance(value, list):
        return ' | '.join(value)
    if value == 'array':
        return f"array<{_schema_type(prop.get('items', {}))}>"
    return value


def _schema_section_fields() -> dict:
    schema_path = Path(__file__).resolve().parent.parent / 'services' / 'config' / 'schema.json'
    with open(schema_path, encoding='utf-8') as f:
        schema = json.load(f)
    definitions = schema.get('definitions', {})
    sections = {
        'http': 'http_config',
        'metadata': 'metadata_config',
        'snapshot': 'snapshot_config',
        'reader': 'reader_config',
    }
    result = {}
    for section, definition_name in sections.items():
        props = definitions.get(definition_name, {}).get('properties', {})
        result[section] = {
            name: {
                'type': _schema_type(prop),
                'desc': prop.get('description', ''),
            }
            for name, prop in props.items()
        }
    return result


# Adapter helpers
def _adapter_from_post(request) -> dict:
    source = request.POST.get('source', '').strip()
    name = request.POST.get('name', '').strip()
    adapter_id = request.POST.get('id', '').strip()
    interval_raw = request.POST.get('update_interval', '').strip()

    from site_adapters.services.subscriptions import is_remote_source, validate_subscription_url

    # id 和 name 优先从请求获取，其次从本地源文件 _meta 自动检测
    # 远程源允许缺失（由调用方在下载后补全）
    if not adapter_id or not name:
        if not is_remote_source(source):
            from site_adapters.services.subscriptions import resolve_adapter_path, _read_subscription_file
            try:
                file_path = resolve_adapter_path(name or '', source)
                data = _read_subscription_file(file_path)
                if data and isinstance(data.get('_meta'), dict):
                    meta = data['_meta']
                    if not adapter_id and meta.get('id'):
                        adapter_id = meta['id']
                    if not name and meta.get('name'):
                        name = meta['name']
            except Exception:
                pass
        if not adapter_id and not is_remote_source(source):
            raise ValueError('id is required')
        if not name and not is_remote_source(source):
            raise ValueError('name is required')
    if not source:
        raise ValueError('source is required')

    if is_remote_source(source):
        validate_subscription_url(source)

    if not _is_safe_subscription_name(adapter_id):
        raise ValueError('invalid adapter id')
    if not _is_safe_subscription_name(name):
        raise ValueError('invalid adapter name')

    if not is_remote_source(source):
        from site_adapters.services.subscriptions import resolve_adapter_path
        full = resolve_adapter_path(name, source)
        if not os.path.exists(full):
            raise ValueError(f'local adapter file not found: {source} (resolved: {full})')

    try:
        update_interval = int(interval_raw or 86400)
    except ValueError as exc:
        raise ValueError('update_interval must be an integer') from exc
    if update_interval <= 0:
        raise ValueError('update_interval must be positive')

    item = {'name': name, 'update_interval': update_interval}
    item['id'] = adapter_id
    item['source'] = source
    return item


def _adapter_cache_info(adapter: dict) -> dict:
    if not isinstance(adapter, dict):
        return {'cached': False, 'domain_count': 0}

    from site_adapters.services.subscriptions import (
        _read_subscription_file,
        list_cached_domains_from_file,
    )
    from site_adapters.services.base import _adapter_dir
    from site_adapters.services.base import _get_adapters_dir
    
    name = adapter.get('name', '')
    source = adapter.get('source')
    dir_name = _adapter_dir(adapter)
    adapters_root = _get_adapters_dir()
    file_path = os.path.join(adapters_root, dir_name, 'adapters.jsonc')
    if source and not source.startswith('http'):
        # Local path source
        resolved = os.path.normpath(os.path.join(adapters_root, source)) if not os.path.isabs(source) else source
        if os.path.exists(resolved):
            file_path = resolved
    cached = os.path.exists(file_path)
    cached_domains = list_cached_domains_from_file(file_path) if cached else []
    is_remote = source and (source.startswith('https://') or source.startswith('http://'))
    info = {
        'name': name,
        'cached': cached,
        'cache_missing': is_remote and not cached,
        'domain_count': len(cached_domains),
        'domains': cached_domains,
    }
    if cached:
        try:
            sub_data = _read_subscription_file(file_path)
            if sub_data and isinstance(sub_data.get('_meta'), dict):
                meta = sub_data['_meta']
                info.update({
                    'last_fetch': meta.get('last_fetch'),
                    'version': meta.get('version', ''),
                    'changelog': meta.get('changelog', ''),
                    'source_name': meta.get('name', ''),
                    'description': meta.get('description', ''),
                })
        except (json.JSONDecodeError, OSError):
            pass
    return info


def _adapter_payload(index: int, adapter) -> dict:
    item = dict(adapter) if isinstance(adapter, dict) else {'name': str(adapter)}
    item.setdefault('id', '')
    item.setdefault('source', '')
    item.setdefault('name', '')
    item.setdefault('update_interval', 86400)
    item.setdefault('enabled', True)
    item.setdefault('description', '')
    item['index'] = index
    from site_adapters.services.base import _adapter_dir
    item['dir'] = _adapter_dir(item)
    info = _adapter_cache_info(item)
    item.update(info)
    return item


def _adapters_response() -> JsonResponse:
    adapters = _get_adapters_list()
    visible = [a for a in adapters if isinstance(a, dict)]
    payload = [_adapter_payload(i, a) for i, a in enumerate(visible)]
    return JsonResponse({'adapters': payload})


def _adapter_index(request, adapters: list) -> int:
    try:
        index = int(request.POST.get('index', ''))
    except ValueError as exc:
        raise ValueError('invalid adapter index') from exc
    if index < 0 or index >= len(adapters):
        raise ValueError('invalid adapter index')
    return index


def _adapter_cache_name(adapter: dict) -> str:
    from site_adapters.services.subscriptions import _sub_name
    source = adapter.get('source', '') if isinstance(adapter, dict) else ''
    name = adapter.get('name', '') if isinstance(adapter, dict) else ''
    return _sub_name(source or name, name) if isinstance(adapter, dict) else ''


def _adapter_cache_dir(adapter: dict) -> str:
    from site_adapters.services.base import _adapter_dir, _get_adapters_dir
    if not isinstance(adapter, dict):
        return ''
    dir_name = _adapter_dir(adapter)
    return os.path.join(_get_adapters_dir(), dir_name)


def _has_adapter_conflict(adapters: list, item: dict, ignore_index: int | None = None) -> bool:
    """检查适配器是否与已有列表冲突。

    仅按 id+name 组合判断重复，不检查 source 是否相同。
    """
    new_id = item.get('id', '')
    new_name = item.get('name', '')
    for i, a in enumerate(adapters):
        if i == ignore_index or not isinstance(a, dict):
            continue
        # id+name 均相等时视为冲突
        if a.get('id') == new_id and a.get('name') == new_name:
            return True
    return False




def build_domain_files_meta() -> list[dict]:
    """Lightweight variant: returns domain list WITHOUT config data.
    Includes 'sections' key listing config top-level keys for tag display."""
    full = build_domain_files()
    result = []
    for d in full:
        item = {k: v for k, v in d.items() if k != 'config'}
        config = d.get('config') or {}
        item['sections'] = sorted(k for k in config.keys() if isinstance(config.get(k), dict))
        result.append(item)
    return result

def build_domain_files() -> list[dict]:
    """Build the full domain list from all enabled adapters, including config,
    disabled state, and shadow info. Reusable by page view and API."""
    from site_adapters.services.auth.credentials import get_shared_cookie
    from site_adapters.services.config import load_jsonc_file
    from site_adapters.services.subscriptions import resolve_adapter_path
    import os

    adapters_list = _get_adapters_list()
    adapters_dir = _get_adapters_dir()
    disabled_domains = _get_disabled_domains()
    domain_files = []
    seen_domains: dict[str, str] = {}

    for adapter in adapters_list:
        if not isinstance(adapter, dict):
            continue
        if adapter.get('enabled') is False:
            continue
        name = adapter.get('name', '')
        source = adapter.get('source')
        # Resolve file path
        dir_name = _adapter_dir(adapter)
        file_path = os.path.join(adapters_dir, dir_name, 'adapters.jsonc')
        if source and not source.startswith('http') and os.path.exists(os.path.join(adapters_dir, source) if not os.path.isabs(source) else source):
            file_path = os.path.normpath(os.path.join(adapters_dir, source)) if not os.path.isabs(source) else source

        if not os.path.exists(file_path):
            continue

        try:
            data = load_jsonc_file(file_path)
        except (json.JSONDecodeError, OSError):
            continue

        if not isinstance(data, dict):
            continue

        domains = data.get('domains', {})
        if not isinstance(domains, dict):
            domains = {}

        for domain_key, domain_config in domains.items():
            is_alias = isinstance(domain_config, dict) and domain_config.get('type') == 'alias'
            target = domain_config.get('target', '') if is_alias else ''
            shared, _ = get_shared_cookie(domain_key)
            has_cookie = bool(shared)
            requires_cookie = (
                not is_alias
                and isinstance(domain_config, dict)
                and (bool(domain_config.get('auth', {}).get('cookie'))
                     or bool(domain_config.get('cookie')))
            )
            disabled = domain_key in disabled_domains

            if domain_key in seen_domains:
                domain_files.append({
                    'domain_key': domain_key,
                    'adapter': name,
                    'is_alias': is_alias,
                    'target': target,
                    'has_cookie': has_cookie,
                    'requires_cookie': requires_cookie,
                    'disabled': disabled,
                    'shadowed': True,
                    'shadowed_by': seen_domains[domain_key],
                    'config': domain_config,
                })
                continue

            seen_domains[domain_key] = name
            domain_files.append({
                'domain_key': domain_key,
                'adapter': name,
                'is_alias': is_alias,
                'target': target,
                'has_cookie': has_cookie,
                'requires_cookie': requires_cookie,
                'disabled': disabled,
                'shadowed': False,
                'shadowed_by': '',
                'config': domain_config,
            })

    return domain_files


def _adapter_dir(adapter: dict) -> str:
    """Get the directory name for an adapter."""
    from site_adapters.services.base import _adapter_dir as _base_adapter_dir
    return _base_adapter_dir(adapter)
