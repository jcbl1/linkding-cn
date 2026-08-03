"""
Main page rendering + defaults adapter management.
"""
import json
import os

from django.conf import settings as django_settings
from django.shortcuts import render

from site_adapters.views.helpers import (
    get_defuddle_params_set,
    get_http_headers_descs,
    get_http_headers_set,
    get_singlefile_args_set,
    _ensure_base_dirs,
    _get_adapters_dir,
    _get_base_dir,
    _get_adapters_list,
    _load_config,
    _schema_section_fields,
    _save_adapters_list,
    _invalidate_site_adapters_cache,
    site_adapters_required,
)
from site_adapters.services.auth.cookies import has_cookie_for_domain
from site_adapters.services.config import load_jsonc_file
from site_adapters.services.config.loader import _cache
from site_adapters.services.subscriptions import resolve_adapter_path


@site_adapters_required
def site_adapters_page(request):
    base_dir = _get_base_dir()
    adapters_dir = _get_adapters_dir()
    _ensure_base_dirs()

    # 读取适配器列表
    adapters_list = _get_adapters_list()

    domain_files = []


    # 读取 config.jsonc 内容
    config_content = ''
    config_path = os.path.join(adapters_dir, 'config.jsonc')
    if os.path.exists(config_path):
        try:
            with open(config_path, encoding='utf-8') as f:
                config_content = f.read()
        except Exception:
            pass

    return render(request, 'site_adapters/site_adapters.html', {
        'domain_files': [],
        'domain_files_json': '[]',
        'config_content': config_content,
        'base_dir': base_dir,
        'adapters_dir': adapters_dir,
        'authority_lists_json': json.dumps({
            'http_headers': sorted(get_http_headers_set()),
            'http_headers_descs': get_http_headers_descs(),
            'singlefile_args': sorted(get_singlefile_args_set()),
            'defuddle_params': sorted(get_defuddle_params_set()),
        }, ensure_ascii=False),
        'section_fields_json': json.dumps(_schema_section_fields(), ensure_ascii=False),
    })
