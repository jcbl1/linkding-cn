"""
Main page rendering + defaults adapter management.
"""
import json
import os

from django.shortcuts import render

from site_adapters.views.helpers import (
    get_defuddle_params_set,
    get_http_headers_descs,
    get_http_headers_set,
    get_singlefile_args_set,
    _ensure_base_dirs,
    _get_adapters_dir,
    _get_base_dir,
    _schema_section_fields,
    site_adapters_required,
)


@site_adapters_required
def site_adapters_page(request):
    base_dir = _get_base_dir()
    adapters_dir = _get_adapters_dir()
    _ensure_base_dirs()

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
