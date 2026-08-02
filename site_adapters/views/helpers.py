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


def _save_defaults_scope(content: str) -> str:
    """保存 defaults 适配器的 '*' 配置。"""
    value = parse_jsonc(content)
    if not isinstance(value, dict):
        raise ValueError('defaults scope must be an object')

    defaults_dir = os.path.join(_get_adapters_dir(), 'defaults')
    os.makedirs(defaults_dir, exist_ok=True)
    defaults_path = os.path.join(defaults_dir, 'defaults.jsonc')

    # 读取现有文件
    text = ''
    if os.path.exists(defaults_path):
        with open(defaults_path, encoding='utf-8') as f:
            text = f.read()
    new_text = _replace_top_level_jsonc_value(text, '*', value)
    atomic_write(defaults_path, new_text)
    _invalidate_site_adapters_cache()
    return new_text


def _get_adapters_list() -> list:
    data, _ = _load_config()
    adapters = data.get("_adapters", [])
    return adapters if isinstance(adapters, list) else []


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

    if is_remote_source(source):
        validate_subscription_url(source)

    if adapter_id and not _is_safe_subscription_name(adapter_id):
        raise ValueError('invalid adapter id')
    if name and not _is_safe_subscription_name(name):
        raise ValueError('invalid adapter name')

    if source and not is_remote_source(source):
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
    if adapter_id:
        item['id'] = adapter_id
    if source:
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
    info = {
        'name': name,
        'cached': cached,
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
    # 过滤掉 defaults（它是内部适配器，不在 UI 显示）
    visible = [a for a in adapters if isinstance(a, dict) and a.get('name') != 'defaults']
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
    new_id = item.get('id', '')
    new_name = item.get('name', '')
    for i, a in enumerate(adapters):
        if i == ignore_index or not isinstance(a, dict):
            continue
        if a.get('id') == new_id and a.get('name') == new_name:
            return True
    return False
