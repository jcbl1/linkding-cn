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
            if _has_adapter_conflict(adapters, item):
                return JsonResponse({'error': 'adapter already exists'}, status=409)
            adapters.append(item)
            _save_adapters_list(adapters)

        elif action_name == 'save':
            index = _adapter_index(request, adapters)
            item = _adapter_from_post(request)
            if _has_adapter_conflict(adapters, item, ignore_index=index):
                return JsonResponse({'error': 'adapter already exists'}, status=409)
            adapters[index] = item
            _save_adapters_list(adapters)

        elif action_name == 'delete':
            index = _adapter_index(request, adapters)
            adapter = adapters.pop(index)
            _save_adapters_list(adapters)
            # 清理缓存目录
            cache_dir = _adapter_cache_dir(adapter)
            adapters_root = _get_adapters_dir()
            try:
                # 只在目录是 adapters/ 的子目录时才删除
                if os.path.commonpath([os.path.abspath(cache_dir), os.path.abspath(adapters_root)]) == os.path.abspath(adapters_root):
                    if os.path.isdir(cache_dir):
                        shutil.rmtree(cache_dir, ignore_errors=True)
            except (ValueError, OSError):
                pass

        elif action_name == 'reorder':
            indices = request.POST.getlist('indices[]')
            reordered = []
            for raw in indices:
                try:
                    idx = int(raw)
                except (ValueError, TypeError):
                    continue
                if 0 <= idx < len(adapters):
                    reordered.append(adapters[idx])
            if len(reordered) != len(adapters):
                return JsonResponse({'error': 'invalid reorder indices'}, status=400)
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
