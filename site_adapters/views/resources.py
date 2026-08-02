"""
File browser: CRUD for site adapter resources.
"""
import json
import logging
import os
import shutil

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from bookmarks.utils import atomic_write
from site_adapters.services.config import parse_jsonc
from site_adapters.views.helpers import (
    _ensure_base_dirs,
    _get_base_dir,
    _invalidate_site_adapters_cache,
    _is_readonly_resource_path,
    _is_safe_resource_name,
    _resolve_site_adapter_path,
    _resource_full_path_and_rel,
    _save_global_scope,
    site_adapters_required,
)

logger = logging.getLogger(__name__)

@site_adapters_required
def resources(request):
    """文件浏览器 API。"""
    path = request.GET.get('path', '')
    _ensure_base_dirs()

    try:
        full_path, rel_path = _resource_full_path_and_rel(path)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)

    if not os.path.exists(full_path):
        return JsonResponse({'error': 'not found'}, status=404)

    if os.path.isdir(full_path):
        items = []
        for name in os.listdir(full_path):
            if name.startswith('.'):
                continue
            item_path = os.path.join(full_path, name)
            items.append({
                'name': name,
                'is_dir': os.path.isdir(item_path),
                'size': os.path.getsize(item_path) if os.path.isfile(item_path) else 0,
        })
        # 文件夹在前，文件在后，各自按名称排序
        items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
        return JsonResponse({'path': rel_path, 'items': items})

    if os.path.isfile(full_path):
        try:
            with open(full_path, encoding='utf-8') as f:
                content = f.read()
            return JsonResponse({'path': rel_path, 'content': content})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'not found'}, status=404)


@site_adapters_required
@require_POST
def resource_manage(request):
    """创建、删除、移动、重命名资源文件。"""
    action = request.POST.get('action', '')
    path = request.POST.get('path', '')

    try:
        full_path, rel_path = _resource_full_path_and_rel(path)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)

    if action in {'create_file', 'create_dir'}:
        name = request.POST.get('name', '').strip()
        template = request.POST.get('template', '').strip()
        if not _is_safe_resource_name(name):
            return JsonResponse({'error': 'invalid name'}, status=400)
        if _is_readonly_resource_path(rel_path):
            return JsonResponse({'error': 'etc resources are read-only'}, status=400)
        if os.path.exists(full_path) and not os.path.isdir(full_path):
            return JsonResponse({'error': 'target path must be a directory'}, status=400)
        new_path = os.path.join(full_path, name)
        new_rel = os.path.relpath(new_path, _get_base_dir())
        if _is_readonly_resource_path(new_rel):
            return JsonResponse({'error': 'cannot create in etc directory'}, status=400)
        if os.path.exists(new_path):
            return JsonResponse({'error': 'path already exists'}, status=409)
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        if action == 'create_dir':
            os.makedirs(new_path)
        else:
            # Read template content if specified
            content = ''
            if template:
                template_path = os.path.join(_get_base_dir(), 'etc', 'templates', template)
                if os.path.exists(template_path):
                    with open(template_path, encoding='utf-8') as f:
                        content = f.read()
            atomic_write(new_path, content)
        _invalidate_site_adapters_cache()
        return JsonResponse({'success': True, 'path': os.path.relpath(new_path, _get_base_dir())})

    if action == 'delete':
        if not rel_path:
            return JsonResponse({'error': 'cannot delete root'}, status=400)
        if _is_readonly_resource_path(rel_path):
            return JsonResponse({'error': 'etc resources are read-only'}, status=400)
        if not os.path.exists(full_path):
            return JsonResponse({'error': 'not found'}, status=404)
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
        else:
            os.remove(full_path)
        _invalidate_site_adapters_cache()
        return JsonResponse({'success': True})

    if action == 'rename':
        name = request.POST.get('name', '').strip()
        if not rel_path:
            return JsonResponse({'error': 'cannot rename root'}, status=400)
        if not _is_safe_resource_name(name):
            return JsonResponse({'error': 'invalid name'}, status=400)
        if _is_readonly_resource_path(rel_path):
            return JsonResponse({'error': 'etc resources are read-only'}, status=400)
        if not os.path.exists(full_path):
            return JsonResponse({'error': 'not found'}, status=404)
        new_path = os.path.join(os.path.dirname(full_path), name)
        new_rel_path = os.path.relpath(new_path, _get_base_dir())
        if os.path.abspath(new_path) == os.path.abspath(full_path):
            return JsonResponse({'success': True, 'path': rel_path})
        if os.path.exists(new_path):
            return JsonResponse({'error': 'path already exists'}, status=409)
        os.rename(full_path, new_path)
        _invalidate_site_adapters_cache()
        return JsonResponse({'success': True, 'path': new_rel_path})

    if action == 'move':
        target_dir = request.POST.get('target_dir', '')
        try:
            target_full_path, target_rel_path = _resource_full_path_and_rel(target_dir)
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)
        if not rel_path:
            return JsonResponse({'error': 'cannot move root'}, status=400)
        if _is_readonly_resource_path(rel_path):
            return JsonResponse({'error': 'etc resources are read-only'}, status=400)
        if not os.path.exists(full_path):
            return JsonResponse({'error': 'not found'}, status=404)
        if not os.path.isdir(target_full_path):
            return JsonResponse({'error': 'target path must be a directory'}, status=400)
        dest_path = os.path.join(target_full_path, os.path.basename(full_path))
        dest_rel_path = os.path.relpath(dest_path, _get_base_dir())
        if _is_readonly_resource_path(dest_rel_path):
            return JsonResponse({'error': 'cannot move into etc directory'}, status=400)
        if os.path.abspath(dest_path) == os.path.abspath(full_path):
            return JsonResponse({'success': True, 'path': rel_path})
        if os.path.exists(dest_path):
            return JsonResponse({'error': 'path already exists'}, status=409)
        if os.path.isdir(full_path) and os.path.commonpath([os.path.abspath(full_path), os.path.abspath(dest_path)]) == os.path.abspath(full_path):
            return JsonResponse({'error': 'cannot move a directory into itself'}, status=400)
        shutil.move(full_path, dest_path)
        _invalidate_site_adapters_cache()
        return JsonResponse({'success': True, 'path': dest_rel_path})

    return JsonResponse({'error': 'unknown action'}, status=400)


@site_adapters_required
@require_POST
def resource_save(request):
    """保存资源模式中的文本文件。"""
    path = request.POST.get('path', '')
    content = request.POST.get('content', '')
    scope = request.POST.get('scope', '')
    if not path:
        return JsonResponse({'error': 'path required'}, status=400)

    if scope:
        if path != 'global.jsonc' or scope != '*':
            return JsonResponse({'error': 'invalid scope'}, status=400)
        try:
            new_content = _save_global_scope(scope, content)
        except (ValueError, json.JSONDecodeError) as e:
            return JsonResponse({'error': str(e)}, status=400)
        return JsonResponse({'success': True, 'path': path, 'content': new_content})

    try:
        full_path = _resolve_site_adapter_path(path)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)

    if os.path.isdir(full_path):
        return JsonResponse({'error': 'cannot save directory'}, status=400)
    if path == 'etc' or path.startswith('etc/'):
        return JsonResponse({'error': 'etc resources are read-only'}, status=400)

    if path.endswith('.json'):
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            return JsonResponse({'error': f'JSON 解析失败: {e}'}, status=400)
    elif path.endswith('.jsonc'):
        try:
            parsed = parse_jsonc(content)
        except json.JSONDecodeError as e:
            return JsonResponse({'error': f'JSON 解析失败: {e}'}, status=400)
        # Structural validation for global.jsonc
        if path == 'global.jsonc' or path.endswith('/global.jsonc'):
            if not isinstance(parsed, dict):
                return JsonResponse({'error': 'global.jsonc 顶层必须是对象'}, status=400)
            subs = parsed.get('_subscriptions')
            if subs is not None and not isinstance(subs, list):
                return JsonResponse({'error': '_subscriptions 必须是数组'}, status=400)

    atomic_write(full_path, content)

    _invalidate_site_adapters_cache()
    return JsonResponse({'success': True, 'path': path})


