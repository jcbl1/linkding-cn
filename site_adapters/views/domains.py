"""
Domain CRUD + rename.
"""
import json
import os

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from bookmarks.utils import atomic_write, is_safe_domain_key
from site_adapters.services.config import parse_jsonc
from site_adapters.views.helpers import (
    _invalidate_site_adapters_cache,
    _resolve_domain_path,
    site_adapters_required,
)


@site_adapters_required
def domain_read(request):
    """读取域名文件内容。"""
    fname = request.GET.get('filename', '')
    if not fname:
        return JsonResponse({'error': 'filename required'}, status=400)

    try:
        fpath = _resolve_domain_path(fname)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    if not os.path.exists(fpath):
        return JsonResponse({'error': 'file not found'}, status=404)

    try:
        with open(fpath, encoding='utf-8') as f:
            content = f.read()
        return JsonResponse({'filename': fname, 'content': content})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@site_adapters_required
@require_POST
def domain_save(request):
    """保存域名文件。"""
    fname = request.POST.get('filename', '')
    content = request.POST.get('content', '')
    if not fname:
        return JsonResponse({'error': 'filename required'}, status=400)

    # 验证 JSON / JSONC
    try:
        if fname.endswith('.json'):
            json.loads(content)
        else:
            parse_jsonc(content)
    except json.JSONDecodeError as e:
        return JsonResponse({'error': f'JSON 解析失败: {e}'}, status=400)

    try:
        fpath = _resolve_domain_path(fname)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    atomic_write(fpath, content)

    _invalidate_site_adapters_cache()
    return JsonResponse({'success': True, 'filename': fname})


@site_adapters_required
@require_POST
def domain_delete(request):
    """删除域名文件。"""
    fname = request.POST.get('filename', '')
    if not fname:
        return JsonResponse({'error': 'filename required'}, status=400)

    try:
        fpath = _resolve_domain_path(fname)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    if os.path.exists(fpath):
        os.remove(fpath)

    _invalidate_site_adapters_cache()
    return JsonResponse({'success': True})


@site_adapters_required
@require_POST
def domain_create(request):
    """创建新域名文件。"""
    domain_key = request.POST.get('domain_key', '')
    if not domain_key:
        return JsonResponse({'error': 'domain_key required'}, status=400)

    # 安全检查
    if not is_safe_domain_key(domain_key):
        return JsonResponse({'error': 'invalid domain key'}, status=400)

    fname = f'{domain_key}.jsonc'
    try:
        fpath = _resolve_domain_path(fname)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    if os.path.exists(fpath):
        return JsonResponse({'error': 'file already exists'}, status=409)

    # 创建默认配置（JSONC 格式）
    default_config = """{
  "default": {
    "http": {}
  },
  "metadata": {},
  "snapshot": {},
  "reader": {}
}"""

    atomic_write(fpath, default_config)

    _invalidate_site_adapters_cache()
    return JsonResponse({'success': True, 'filename': fname})



@site_adapters_required
@require_POST
def domain_rename(request):
    """重命名域名文件。"""
    old_filename = request.POST.get('old_filename', '')
    new_domain = request.POST.get('new_domain', '')
    if not old_filename or not new_domain:
        return JsonResponse({'error': 'old_filename and new_domain required'}, status=400)

    # 安全检查
    if not is_safe_domain_key(new_domain):
        return JsonResponse({'error': 'invalid domain'}, status=400)

    try:
        old_path = _resolve_domain_path(old_filename)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    if not os.path.exists(old_path):
        return JsonResponse({'error': 'old file not found'}, status=404)

    # 新文件名
    if old_filename.endswith('.jsonc'):
        new_filename = new_domain + '.jsonc'
    else:
        new_filename = new_domain + '.json'

    try:
        new_path = _resolve_domain_path(new_filename)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    if os.path.exists(new_path):
        return JsonResponse({'error': 'target already exists'}, status=409)

    os.rename(old_path, new_path)
    _invalidate_site_adapters_cache()
    return JsonResponse({'success': True, 'old_filename': old_filename, 'new_filename': new_filename})

