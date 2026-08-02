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
from site_adapters.services.base import _get_base_dir


def _ensure_base_dirs():
    base_dir = _get_base_dir()
    for name in ('domains', 'cookies', 'scripts', 'logs', 'test_assets'):
        os.makedirs(os.path.join(base_dir, name), exist_ok=True)


def site_adapters_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not (request.user.is_active and request.user.is_superuser):
            raise PermissionDenied()
        return view_func(request, *args, **kwargs)

    return login_required(wrapped)


def _resolve_site_adapter_path(path: str) -> str:
    base_dir = os.path.abspath(_get_base_dir())
    full_path = os.path.abspath(os.path.normpath(os.path.join(base_dir, path)))
    if os.path.commonpath([base_dir, full_path]) != base_dir:
        raise ValueError('invalid path')
    return full_path


def _resolve_domain_path(filename: str) -> str:
    if (
        not filename
        or filename != os.path.basename(filename)
        or not (filename.endswith('.jsonc') or filename.endswith('.json'))
    ):
        raise ValueError('invalid filename')
    return _resolve_site_adapter_path(os.path.join('domains', filename))


def _invalidate_site_adapters_cache():
    from site_adapters.services.config.loader import _cache
    _cache.invalidate()


def _is_safe_subscription_name(name: str) -> bool:
    """Validate subscription name. Empty is allowed (falls back to hash)."""
    if not name:
        return True
    if name.startswith('.'):
        return False
    if '/' in name or '\\' in name or '..' in name:
        return False
    return bool(re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]*$', name))


def _is_safe_resource_name(name: str) -> bool:
    return bool(name) and name == os.path.basename(name) and not name.startswith('.') and '..' not in name


def _resource_full_path_and_rel(path: str) -> tuple[str, str]:
    full_path = _resolve_site_adapter_path(path)
    base_dir = os.path.abspath(_get_base_dir())
    rel_path = os.path.relpath(full_path, base_dir)
    return full_path, '' if rel_path == '.' else rel_path


def _is_readonly_resource_path(rel_path: str) -> bool:
    return rel_path == 'etc' or rel_path.startswith('etc' + os.sep)


# ---------------------------------------------------------------------------
# Global config (global.jsonc) management
# ---------------------------------------------------------------------------

def _global_config_path() -> str:
    return os.path.join(_get_base_dir(), 'global.jsonc')


def _load_global_config() -> tuple[dict, str]:
    path = _global_config_path()
    if not os.path.exists(path):
        return {}, ''
    with open(path, encoding='utf-8') as f:
        text = f.read()
    if not text.strip():
        return {}, text
    data = parse_jsonc(text)
    if not isinstance(data, dict):
        raise ValueError('global.jsonc must be an object')
    return data, text




def _save_global_subscriptions(subscriptions: list[dict]):
    # Read once, update, write — avoids TOCTOU race with concurrent requests.
    path = _global_config_path()
    text = ''
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            text = f.read()
    if text.strip():
        data = parse_jsonc(text)
        if not isinstance(data, dict):
            raise ValueError('global.jsonc must be an object')
    new_text = _replace_top_level_jsonc_value(text, '_subscriptions', subscriptions)
    atomic_write(path, new_text)
    _invalidate_site_adapters_cache()


def _save_global_scope(scope: str, content: str) -> str:
    value = parse_jsonc(content)
    if not isinstance(value, dict):
        raise ValueError('global scope must be an object')
    path = _global_config_path()
    # Single read to avoid TOCTOU race: use the same text for both
    # validation and update.
    text = ''
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            text = f.read()
    if text.strip():
        existing = parse_jsonc(text)
        if not isinstance(existing, dict):
            raise ValueError('global.jsonc must be an object')
    new_text = _replace_top_level_jsonc_value(text, scope, value)
    atomic_write(path, new_text)
    _invalidate_site_adapters_cache()
    return new_text


def _get_global_subscriptions() -> list:
    data, _ = _load_global_config()
    subscriptions = data.get("_subscriptions", [])
    return subscriptions if isinstance(subscriptions, list) else []

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
    # Support both single and double quotes
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


# Subscription helpers
def _subscription_from_post(request) -> dict:
    url = request.POST.get('url', '').strip()
    name = request.POST.get('name', '').strip()
    interval_raw = request.POST.get('update_interval', '').strip()
    from site_adapters.services.subscriptions import validate_subscription_url
    validate_subscription_url(url)
    if not _is_safe_subscription_name(name):
        raise ValueError('invalid subscription name')
    try:
        update_interval = int(interval_raw or 86400)
    except ValueError as exc:
        raise ValueError('update_interval must be an integer') from exc
    if update_interval <= 0:
        raise ValueError('update_interval must be positive')
    item = {'url': url, 'update_interval': update_interval, 'name': name}
    return item


def _subscription_cache_info(sub: dict) -> dict:
    if not isinstance(sub, dict):
        return {'cached': False, 'domain_count': 0}
    from site_adapters.services.subscriptions import (
        _sub_name,
        list_cached_domains_from_file,
    )

    url = sub.get('url', '')
    name = sub.get('name', '')
    cache_name = _sub_name(url, name)
    sub_file = os.path.join(_get_base_dir(), 'subscriptions', cache_name, 'subscription.jsonc')
    cached = os.path.exists(sub_file)
    cached_domains = list_cached_domains_from_file(sub_file) if cached else []
    info = {
        'cache_name': cache_name,
        'cached': cached,
        'domain_count': len(cached_domains),
        'domains': cached_domains,
    }
    if cached:
        try:
            from site_adapters.services.subscriptions import _read_subscription_file
            sub_data = _read_subscription_file(sub_file)
            if sub_data and isinstance(sub_data.get('_meta'), dict):
                meta = sub_data['_meta']
                info.update({
                    'last_fetch': meta.get('last_fetch'),
                    'version': meta.get('version', ''),
                    'changelog': meta.get('changelog', ''),
                    'source_name': meta.get('name', ''),
                })
        except (json.JSONDecodeError, OSError):
            pass
    return info


def _subscription_payload(index: int, sub) -> dict:
    item = dict(sub) if isinstance(sub, dict) else {'url': str(sub)}
    item.setdefault('name', '')
    item.setdefault('update_interval', 86400)
    item.setdefault('enabled', True)
    item['index'] = index
    info = _subscription_cache_info(item)
    item.update(info)
    item.setdefault('source_name', '')
    if not item['source_name']:
        from urllib.parse import urlparse
        path = urlparse(item.get('url', '')).path
        fname = os.path.basename(path)
        for ext in ('.jsonc', '.json'):
            if fname.endswith(ext):
                item['source_name'] = fname[:-len(ext)]
                break
    return item

def _subscription_response() -> JsonResponse:
    subscriptions = _get_global_subscriptions()
    payload = [_subscription_payload(i, sub) for i, sub in enumerate(subscriptions)]
    return JsonResponse({'subscriptions': payload})


def _subscription_index(request, subscriptions: list) -> int:
    try:
        index = int(request.POST.get('index', ''))
    except ValueError as exc:
        raise ValueError('invalid subscription index') from exc
    if index < 0 or index >= len(subscriptions):
        raise ValueError('invalid subscription index')
    return index


def _subscription_cache_name(sub: dict) -> str:
    from site_adapters.services.subscriptions import _sub_name
    return _sub_name(sub.get('url', ''), sub.get('name', '')) if isinstance(sub, dict) else ''


def _subscription_cache_dir(sub: dict) -> str:
    return os.path.join(_get_base_dir(), 'subscriptions', _subscription_cache_name(sub))


def _has_subscription_conflict(subscriptions: list, item: dict, ignore_index: int | None = None) -> bool:
    new_cache_name = _subscription_cache_name(item)
    for i, sub in enumerate(subscriptions):
        if i == ignore_index or not isinstance(sub, dict):
            continue
        if sub.get('url') == item.get('url') or _subscription_cache_name(sub) == new_cache_name:
            return True
    return False

