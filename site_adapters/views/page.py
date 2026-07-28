"""
Main page rendering + global.jsonc management.
"""
import json
import os

from django.conf import settings as django_settings
from django.http import JsonResponse
from django.shortcuts import render

from site_adapters.views.helpers import (
    get_defuddle_params_set,
    get_http_headers_descs,
    get_http_headers_set,
    get_singlefile_args_set,
    _ensure_base_dirs,
    _get_base_dir,
    _get_global_subscriptions,
    _load_global_config,
    _schema_section_fields,
    site_adapters_required,
    _save_global_scope,
    _save_global_subscriptions,
    _invalidate_site_adapters_cache,
)
from site_adapters.services.auth.cookies import has_cookie_for_domain
from site_adapters.services.config import load_jsonc_file

@site_adapters_required
def site_adapters_page(request):
    base_dir = _get_base_dir()
    _ensure_base_dirs()
    domains_dir = os.path.join(base_dir, 'domains')

    # 读取域名列表
    domain_files = []
    if os.path.isdir(domains_dir):
        for fname in sorted(os.listdir(domains_dir)):
            if not (fname.endswith('.jsonc') or fname.endswith('.json')):
                continue
            fpath = os.path.join(domains_dir, fname)
            # 域名 key（去掉扩展名）
            domain_key = fname[:-6] if fname.endswith('.jsonc') else fname[:-5]

            # 检查是否是别名
            data = {}
            try:
                data = load_jsonc_file(fpath)
                is_alias = isinstance(data, dict) and data.get('type') == 'alias'
                target = data.get('target', '') if is_alias else ''
            except (json.JSONDecodeError, OSError):
                is_alias = False
                target = ''

            # Auth 状态
            has_cookie = has_cookie_for_domain(domain_key)
            requires_cookie = False
            if not is_alias and isinstance(data, dict):
                auth = data.get('auth', {})
                requires_cookie = bool(auth.get('cookie'))

            domain_files.append({
                'filename': fname,
                'domain_key': domain_key,
                'is_alias': is_alias,
                'target': target,
                'has_cookie': has_cookie,
                'requires_cookie': requires_cookie,
            })

    # 读取 global.jsonc
    global_content = ''
    global_path = os.path.join(base_dir, 'global.jsonc')
    if os.path.exists(global_path):
        try:
            with open(global_path, encoding='utf-8') as f:
                global_content = f.read()
        except Exception:
            pass

    # Load disabled domains from global config
    disabled_domains = []
    try:
        global_data, _ = _load_global_config()
        disabled_domains = (global_data.get('*', {}) or {}).get('_disabled_domains', [])
    except Exception:
        pass

    for df in domain_files:
        df['disabled'] = df['domain_key'] in disabled_domains

    return render(request, 'site_adapters/site_adapters.html', {
        'domain_files': domain_files,
        'domain_files_json': json.dumps(domain_files, ensure_ascii=False),
        'global_content': global_content,
        'base_dir': base_dir,
        'authority_lists_json': json.dumps({
            'http_headers': sorted(get_http_headers_set()),
            'http_headers_descs': get_http_headers_descs(),
            'singlefile_args': sorted(get_singlefile_args_set()),
            'defuddle_params': sorted(get_defuddle_params_set()),
        }, ensure_ascii=False),
        'section_fields_json': json.dumps(_schema_section_fields(), ensure_ascii=False),
    })


