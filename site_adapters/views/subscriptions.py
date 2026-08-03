"""
Adapter management — CRUD for _adapters in config.jsonc.
"""
import fnmatch
import json
import logging
import os
import shutil
from urllib.parse import urlparse

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from site_adapters.views.helpers import (
    _adapter_cache_dir,
    _adapter_cache_name,
    _adapter_from_post,
    _adapter_index,
    _adapter_payload,
    _adapters_response,
    _get_adapters_dir,
    _get_adapters_list,
    _has_adapter_conflict,
    _invalidate_site_adapters_cache,
    _is_safe_subscription_name,
    _load_config,
    _save_adapters_list,
    site_adapters_required,
)

logger = logging.getLogger(__name__)

@site_adapters_required
@require_http_methods(["GET", "POST"])
def subscription_manage(request):
    """管理适配器列表。真源是 config.jsonc 的 _adapters。"""
    try:
        adapters = _get_adapters_list()
    except (json.JSONDecodeError, OSError, ValueError) as e:
        return JsonResponse({'error': str(e)}, status=400)

    if request.method == 'GET':
        return _adapters_response()

    action_name = request.POST.get('action', '')

    try:
        if action_name == 'add':
            item = _adapter_from_post(request)
            source = item.get('source', '')

            # Resolve id and name from the subscription source itself
            from site_adapters.services.subscriptions import (
                _read_subscription_file, resolve_adapter_path,
                is_remote_source, fetch_subscription, _sub_name,
            )

            resolved_id = item.get('id', '')
            resolved_name = item.get('name', '')

            if source:
                if is_remote_source(source):
                    # Fetch the subscription file to get _meta.id and _meta.name
                    try:
                        tmp_path = fetch_subscription(source, name=item.get('name', ''))
                        if tmp_path:
                            data = _read_subscription_file(tmp_path)
                            if data and isinstance(data.get('_meta'), dict):
                                meta = data['_meta']
                                resolved_id = resolved_id or meta.get('id', '')
                                resolved_name = resolved_name or meta.get('name', '')
                    except Exception:
                        pass
                else:
                    # Local: read the file directly
                    fp = resolve_adapter_path(item.get('name', ''), source)
                    data = _read_subscription_file(fp)
                    if data and isinstance(data.get('_meta'), dict):
                        meta = data['_meta']
                        resolved_id = resolved_id or meta.get('id', '')
                        resolved_name = resolved_name or meta.get('name', '')

            # Fallback: generate id from source if still empty
            if not resolved_id:
                if source:
                    import re as _re, hashlib as _hl
                    if is_remote_source(source):
                        from urllib.parse import urlparse as _up
                        p = _up(source).path.rstrip('/')
                        fn = p.split('/')[-1] if p else ''
                        resolved_id = _re.sub(r'\.(jsonc|json)$', '', fn) if fn else ''
                    else:
                        resolved_id = _re.sub(r'\.(jsonc|json)$', '', os.path.basename(source))
                if not resolved_id:
                    import hashlib as _hl
                    resolved_id = _hl.md5((source or '').encode()).hexdigest()[:8]

            if not resolved_name:
                resolved_name = resolved_id

            item['id'] = resolved_id
            item['name'] = resolved_name

            if _has_adapter_conflict(adapters, item):
                return JsonResponse({'error': 'adapter already exists'}, status=409)
            adapters.append(item)
            _save_adapters_list(adapters)

        elif action_name == 'save':
            index = _adapter_index(request, adapters)
            item = _adapter_from_post(request)
            if not item.get('name'):
                # Try _meta.name from cached file first
                from site_adapters.services.subscriptions import _read_subscription_file, resolve_adapter_path, is_remote_source
                old = adapters[index]
                name_from_meta = None
                if old.get('source'):
                    fp = resolve_adapter_path(old.get('name', ''), old.get('source', ''))
                    data = _read_subscription_file(fp)
                    if data and isinstance(data.get('_meta'), dict):
                        name_from_meta = data['_meta'].get('name')
                item['name'] = name_from_meta or old.get('name') or item.get('id', '')
            if _has_adapter_conflict(adapters, item, ignore_index=index):
                return JsonResponse({'error': 'adapter already exists'}, status=409)
            adapters[index] = item
            _save_adapters_list(adapters)

        elif action_name == 'delete':
            index = _adapter_index(request, adapters)
            adapter = adapters.pop(index)
            _save_adapters_list(adapters)
            # 仅远程适配器删除缓存目录，本地适配器保留文件夹
            source = adapter.get('source', '') if isinstance(adapter, dict) else ''
            from site_adapters.services.subscriptions import is_remote_source
            if is_remote_source(source):
                cache_dir = _adapter_cache_dir(adapter)
                adapters_root = _get_adapters_dir()
                try:
                    if os.path.commonpath([os.path.abspath(cache_dir), os.path.abspath(adapters_root)]) == os.path.abspath(adapters_root):
                        if os.path.isdir(cache_dir):
                            shutil.rmtree(cache_dir, ignore_errors=True)
                except (ValueError, OSError):
                    pass

        elif action_name == 'reorder':
            indices = request.POST.getlist('indices')
            logger.warning('REORDER DEBUG: POST keys=%s, raw_indices=%s, adapters_len=%d',
                           list(request.POST.keys()), indices, len(adapters))
            seen = set()
            reordered = []
            for raw in indices:
                try:
                    idx = int(raw)
                except (ValueError, TypeError):
                    continue
                if 0 <= idx < len(adapters) and idx not in seen:
                    reordered.append(adapters[idx])
                    seen.add(idx)
            # Append any adapters not in the sent indices (keeps them at the end)
            for i, ad in enumerate(adapters):
                if i not in seen:
                    reordered.append(ad)
            adapters = reordered
            _save_adapters_list(adapters)

        elif action_name == 'enable':
            index = _adapter_index(request, adapters)
            adapters[index]['enabled'] = True
            _save_adapters_list(adapters)

        elif action_name == 'disable':
            index = _adapter_index(request, adapters)
            adapters[index]['enabled'] = False
            _save_adapters_list(adapters)

        elif action_name == 'update':
            index = _adapter_index(request, adapters)
            adapter = adapters[index]
            name = adapter.get('name', '')
            source = adapter.get('source', '')
            if not source:
                return JsonResponse({'error': 'no source for adapter'}, status=400)
            from site_adapters.services.subscriptions import fetch_subscription, _read_subscription_file, resolve_adapter_path
            path = fetch_subscription(source, name=name, force=request.POST.get('force') == '1')
            if path:
                # Sync _meta.name from fetched file to config name
                data = _read_subscription_file(path)
                if data and isinstance(data.get('_meta'), dict) and data['_meta'].get('name'):
                    adapters[index]['name'] = data['_meta']['name']
                    _save_adapters_list(adapters)
                _invalidate_site_adapters_cache()
                return _adapters_response()
            return JsonResponse({'error': 'update failed, check logs'}, status=500)

        else:
            return JsonResponse({'error': f'unknown action: {action_name}'}, status=400)

    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)

    _invalidate_site_adapters_cache()
    return _adapters_response()

# ---------------------------------------------------------------------------
# 兼容旧 API：域名列表和 toggle
# ---------------------------------------------------------------------------

@site_adapters_required
def all_domains(request):
    """返回所有域名列表（兼容旧 API）。"""
    from site_adapters.services.config import load_jsonc_file
    from site_adapters.services.subscriptions import resolve_adapter_path

    adapters = _get_adapters_list()
    local_domains = []
    sub_domains = []

    for adapter in adapters:
        if not isinstance(adapter, dict) or adapter.get('enabled') is False:
            continue
        name = adapter.get('name', '')
        source = adapter.get('source')
        file_path = resolve_adapter_path(name, source)
        if not os.path.exists(file_path):
            continue
        try:
            data = load_jsonc_file(file_path)
        except Exception:
            continue
        if not isinstance(data, dict) or not isinstance(data.get('domains'), dict):
            continue

        if name == 'defaults':
            local_domains = sorted(data['domains'].keys())
        else:
            sub_domains.append({
                'name': name,
                'domains': [{'domain': d, 'enabled': True, 'overridden': False}
                           for d in sorted(data['domains'].keys())],
            })

    return JsonResponse({'local': local_domains, 'subscriptions': sub_domains})


@site_adapters_required
@require_POST
def local_domain_toggle(request):
    """开关本地域名（兼容旧 API）。"""
    domain_key = request.POST.get('domain', '')
    enabled = request.POST.get('enabled', '1') == '1'
    # TODO: implement toggle in defaults adapter
    return JsonResponse({'success': True})


@site_adapters_required
def subscription_domain_read(request):
    """读取订阅域名配置（兼容旧 API）。"""
    domain_key = request.GET.get('domain', '')
    sub_name = request.GET.get('sub', '')
    from site_adapters.services.subscriptions import resolve_adapter_path, _read_subscription_file

    file_path = resolve_adapter_path(sub_name, None)
    data = _read_subscription_file(file_path)
    if data and isinstance(data.get('domains'), dict) and domain_key in data['domains']:
        return JsonResponse({'config': data['domains'][domain_key], 'domain': domain_key})
    return JsonResponse({'error': 'not found'}, status=404)


@site_adapters_required
@require_POST
def subscription_domain_toggle(request):
    """开关订阅域名（兼容旧 API）。"""
    return JsonResponse({'success': True})
