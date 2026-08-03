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

            # id 和 name 均为必填
            if not item.get('id', '').strip():
                return JsonResponse({'error': 'id is required'}, status=400)
            if not item.get('name', '').strip():
                return JsonResponse({'error': 'name is required'}, status=400)

            if _has_adapter_conflict(adapters, item):
                return JsonResponse({'error': 'adapter already exists'}, status=409)
            adapters.append(item)
            _save_adapters_list(adapters)

        elif action_name == 'save':
            index = _adapter_index(request, adapters)
            item = _adapter_from_post(request)
            old = adapters[index]
            # 保留未提供的 id 和 name
            if not item.get('id', '').strip():
                item['id'] = old.get('id', '')
            if not item.get('name', '').strip():
                item['name'] = old.get('name', '')
            if not item.get('id', '').strip():
                return JsonResponse({'error': 'id is required'}, status=400)
            if not item.get('name', '').strip():
                return JsonResponse({'error': 'name is required'}, status=400)
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
            # Append any adapters not in the sent indices
            for i, ad in enumerate(adapters):
                if i not in seen:
                    reordered.append(ad)
            # 强制 defaults 排第一位
            _ensure_defaults_first(reordered)
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
            path = fetch_subscription(source, name=name, adapter_id=adapter.get('id', ''), force=request.POST.get('force') == '1')
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
    """返回域名列表（元数据，不含 config）。
    支持 ?q= 参数进行服务端过滤。"""
    from site_adapters.views.helpers import build_domain_files_meta
    try:
        domain_files = build_domain_files_meta()
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

    q = request.GET.get('q', '').strip().lower()
    if q:
        domain_files = [d for d in domain_files if q in d.get('domain_key', '').lower()]

    return JsonResponse({'domain_files': domain_files, 'total_count': len(domain_files)})


@site_adapters_required
@require_POST
def local_domain_toggle(request):
    """开关域名启用/禁用状态。写入 config.jsonc 的 _disabled_domains。"""
    from site_adapters.views.helpers import _toggle_domain_disabled
    domain_key = request.POST.get('domain', '')
    if not domain_key:
        return JsonResponse({'error': 'domain key required'}, status=400)
    enabled = request.POST.get('enabled', '1') == '1'
    _toggle_domain_disabled(domain_key, disabled=not enabled)
    return JsonResponse({'success': True, 'domain': domain_key, 'enabled': enabled})


@site_adapters_required
def subscription_domain_read(request):
    """读取订阅域名配置（兼容旧 API）。"""
    domain_key = request.GET.get('domain', '')
    sub_name = request.GET.get('sub', '')
    from site_adapters.services.subscriptions import _read_subscription_file

    # 在已注册的适配器中查找
    for adapter in adapters:
        if not isinstance(adapter, dict):
            continue
        if adapter.get('name') == sub_name:
            from site_adapters.services.subscriptions import resolve_adapter_path
            file_path = resolve_adapter_path(sub_name, adapter.get('source', ''))
            data = _read_subscription_file(file_path)
            if data and isinstance(data.get('domains'), dict) and domain_key in data['domains']:
                return JsonResponse({'config': data['domains'][domain_key], 'domain': domain_key})
            break
    return JsonResponse({'error': 'not found'}, status=404)


@site_adapters_required
@require_POST
def subscription_domain_toggle(request):
    """开关订阅域名（兼容旧 API）。"""
    return JsonResponse({'success': True})
