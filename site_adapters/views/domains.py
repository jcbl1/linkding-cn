"""
Domain CRUD — 操作适配器文件中的 domains 块。

域名现在存储在适配器文件中（如 defaults/defaults.jsonc 的 "domains" 键下），
不再作为独立文件存在。
"""
import json
import logging
import os
import re

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from bookmarks.utils import atomic_write, is_safe_domain_key
from site_adapters.services.config import parse_jsonc, load_jsonc_file
from site_adapters.services.config.bootstrap import is_defaults_adapter
from site_adapters.services.base import _get_adapters_dir
from site_adapters.services.subscriptions import resolve_adapter_path, _read_subscription_file
from site_adapters.views.helpers import (
    _ensure_defaults_adapter,
    _invalidate_site_adapters_cache,
    _get_adapters_list,
    site_adapters_required,
)

logger = logging.getLogger(__name__)


def _defaults_file_path() -> str:
    """获取 defaults 适配器（id="defaults"）的文件路径。"""
    adapters_list = _get_adapters_list()
    for adapter in adapters_list:
        if not isinstance(adapter, dict):
            continue
        if is_defaults_adapter(adapter):
            return resolve_adapter_path(
                adapter.get('name', ''),
                adapter.get('source', ''),
                adapter_id=adapter.get('id', ''),
            )
    # 回退：构造默认路径
    return os.path.join(_get_adapters_dir(), 'defaults', 'adapters.jsonc')


def _ensure_defaults_file():
    """Ensure and return the path to the runtime defaults adapter file."""
    _ensure_defaults_adapter(_get_adapters_dir(), sync=False)
    return _defaults_file_path()
def _read_domain_from_adapter(domain_key: str, adapter_name: str = '') -> tuple[str | None, str | None, dict | None]:
    """在所有适配器中查找域名，返回 (file_path, domain_key, config)。
    如果指定 adapter_name，只查找该适配器。"""
    adapters_list = _get_adapters_list()
    for adapter in adapters_list:
        if not isinstance(adapter, dict) or adapter.get('enabled') is False:
            continue
        name = adapter.get('name', '')
        if adapter_name and name != adapter_name:
            continue
        source = adapter.get('source', '')
        if not source:
            continue
        file_path = resolve_adapter_path(name, source, adapter_id=adapter.get('id', ''))
        if not os.path.exists(file_path):
            continue

        data = _read_subscription_file(file_path)
        if not data or not isinstance(data.get('domains'), dict):
            continue

        if domain_key in data['domains']:
            return file_path, domain_key, data['domains'][domain_key]

    return None, None, None


def _write_domain_to_file(file_path: str, domain_key: str, config: dict) -> str:
    """向适配器文件的 domains 块写入一个域名配置。"""
    # 读取现有文件
    text = ''
    if os.path.exists(file_path):
        with open(file_path, encoding='utf-8') as f:
            text = f.read()
    data = parse_jsonc(text) if text.strip() else {}

    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get('domains'), dict):
        data['domains'] = {}

    data['domains'][domain_key] = config

    # 重建 JSONC（保持 domains 在前）
    new_text = json.dumps(data, indent=2, ensure_ascii=False)
    atomic_write(file_path, new_text)
    _invalidate_site_adapters_cache()
    return new_text


def _delete_domain_from_file(file_path: str, domain_key: str):
    """从适配器文件的 domains 块中删除一个域名。"""
    text = ''
    if os.path.exists(file_path):
        with open(file_path, encoding='utf-8') as f:
            text = f.read()
    data = parse_jsonc(text) if text.strip() else {}

    if isinstance(data, dict) and isinstance(data.get('domains'), dict):
        data['domains'].pop(domain_key, None)
        new_text = json.dumps(data, indent=2, ensure_ascii=False)
        atomic_write(file_path, new_text)

    _invalidate_site_adapters_cache()


@site_adapters_required
def domain_read(request):
    """读取域名配置。支持 ?adapter= 参数指定适配器。"""
    domain_key = request.GET.get('domain_key', '')
    if not domain_key:
        return JsonResponse({'error': 'domain_key required'}, status=400)

    adapter_name = request.GET.get('adapter', '')
    file_path, found_key, config = _read_domain_from_adapter(domain_key, adapter_name)
    if found_key is None:
        return JsonResponse({'error': 'domain not found'}, status=404)

    return JsonResponse({
        'domain_key': found_key,
        'file_path': file_path,
        'config': config,
    })


@site_adapters_required
@require_POST
def domain_save(request):
    """保存域名配置。覆盖现有域或创建/更新在 defaults 适配器中。"""
    domain_key = request.POST.get('domain_key', '')
    content = request.POST.get('content', '')
    if not domain_key:
        return JsonResponse({'error': 'domain_key required'}, status=400)

    # 验证 JSON / JSONC
    try:
        config = parse_jsonc(content)
    except json.JSONDecodeError as e:
        return JsonResponse({'error': f'JSON 解析失败: {e}'}, status=400)

    if not isinstance(config, dict):
        return JsonResponse({'error': 'domain config must be an object'}, status=400)

    # 查找现有位置或使用 defaults
    file_path, _, _ = _read_domain_from_adapter(domain_key)
    if file_path is None:
        file_path = _ensure_defaults_file()

    _write_domain_to_file(file_path, domain_key, config)
    return JsonResponse({'success': True, 'domain_key': domain_key})


@site_adapters_required
@require_POST
def domain_delete(request):
    """删除域名配置。"""
    domain_key = request.POST.get('domain_key', '')
    if not domain_key:
        return JsonResponse({'error': 'domain_key required'}, status=400)

    file_path, found_key, _ = _read_domain_from_adapter(domain_key)
    if found_key is None:
        return JsonResponse({'error': 'domain not found'}, status=404)

    _delete_domain_from_file(file_path, found_key)
    return JsonResponse({'success': True})


@site_adapters_required
@require_POST
def domain_create(request):
    """创建新域名配置（写入 defaults 适配器）。"""
    domain_key = request.POST.get('domain_key', '')
    if not domain_key:
        return JsonResponse({'error': 'domain_key required'}, status=400)

    if not is_safe_domain_key(domain_key):
        return JsonResponse({'error': 'invalid domain key'}, status=400)

    # 检查是否已存在
    _, found, _ = _read_domain_from_adapter(domain_key)
    if found:
        return JsonResponse({'error': 'domain already exists'}, status=409)

    file_path = _ensure_defaults_file()

    default_config = {
        "defaults": {"http": {}},
        "metadata": {},
        "snapshot": {},
        "reader": {},
    }
    _write_domain_to_file(file_path, domain_key, default_config)
    return JsonResponse({'success': True, 'domain_key': domain_key})


@site_adapters_required
@require_POST
def domain_rename(request):
    """重命名域名（在同一适配器文件内移动）。"""
    old_domain = request.POST.get('old_domain_key', '')
    new_domain = request.POST.get('new_domain_key', '')
    if not old_domain or not new_domain:
        return JsonResponse({'error': 'old_domain_key and new_domain_key required'}, status=400)

    if not is_safe_domain_key(new_domain):
        return JsonResponse({'error': 'invalid new domain key'}, status=400)

    file_path, found_key, config = _read_domain_from_adapter(old_domain)
    if found_key is None:
        return JsonResponse({'error': 'domain not found'}, status=404)

    # 检查新名称是否已存在
    _, new_found, _ = _read_domain_from_adapter(new_domain)
    if new_found:
        return JsonResponse({'error': 'target domain already exists'}, status=409)

    # 删除旧域名，添加新域名
    _delete_domain_from_file(file_path, found_key)
    _write_domain_to_file(file_path, new_domain, config)
    return JsonResponse({'success': True, 'old_domain_key': old_domain, 'new_domain_key': new_domain})
