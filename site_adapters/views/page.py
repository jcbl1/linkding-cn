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
    _save_defaults_scope,
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

    # 读取所有适配器的域名列表
    domain_files = []
    seen_domains = set()

    for adapter in adapters_list:
        if not isinstance(adapter, dict):
            continue
        if adapter.get('enabled') is False:
            continue
        name = adapter.get('name', '')
        source = adapter.get('source')
        from site_adapters.services.base import _adapter_dir
        dir_name = _adapter_dir(adapter)
        file_path = os.path.join(adapters_dir, dir_name, 'adapters.jsonc')
        if source and not source.startswith('http') and os.path.exists(os.path.join(adapters_dir, source) if not os.path.isabs(source) else source):
            file_path = os.path.normpath(os.path.join(adapters_dir, source)) if not os.path.isabs(source) else source

        if not os.path.exists(file_path):
            continue

        try:
            data = load_jsonc_file(file_path)
        except (json.JSONDecodeError, OSError):
            continue

        if not isinstance(data, dict):
            continue

        domains = data.get('domains', {})
        if not isinstance(domains, dict):
            domains = {}

        for domain_key, domain_config in domains.items():
            if domain_key in seen_domains:
                continue
            seen_domains.add(domain_key)

            is_alias = isinstance(domain_config, dict) and domain_config.get('type') == 'alias'
            target = domain_config.get('target', '') if is_alias else ''
            has_cookie = has_cookie_for_domain(domain_key)
            requires_cookie = (
                not is_alias
                and isinstance(domain_config, dict)
                and bool(domain_config.get('auth', {}).get('cookie'))
            )

            domain_files.append({
                'domain_key': domain_key,
                'adapter': name,
                'is_alias': is_alias,
                'target': target,
                'has_cookie': has_cookie,
                'requires_cookie': requires_cookie,
            })

    # 读取 defaults.jsonc 的 "*" 内容（全局默认）
    defaults_content = ''
    defaults_path = os.path.join(adapters_dir, 'defaults', 'defaults.jsonc')
    if os.path.exists(defaults_path):
        try:
            with open(defaults_path, encoding='utf-8') as f:
                defaults_content = f.read()
        except Exception:
            pass

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
        'domain_files': domain_files,
        'domain_files_json': json.dumps(domain_files, ensure_ascii=False),
        'global_content': defaults_content,
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
