"""
Main page rendering + defaults adapter management.
"""
import json
import os

from django.shortcuts import render

from site_adapters.views.helpers import (
    get_defuddle_params_set,
    get_singlefile_args_set,
    _get_adapters_dir,
    _get_base_dir,
    site_adapters_required,
)
from site_adapters.views.credentials import _get_domains_needing_auth
from site_adapters.services.auth.credentials import list_shared_credentials


@site_adapters_required
def site_adapters_page(request):
    base_dir = _get_base_dir()
    adapters_dir = _get_adapters_dir()

    # 读取 config.jsonc 内容
    config_content = ''
    config_path = os.path.join(adapters_dir, 'config.jsonc')
    if os.path.exists(config_path):
        try:
            with open(config_path, encoding='utf-8') as f:
                config_content = f.read()
        except Exception:
            pass

    # ── Shared credentials context (for credentials_manage.html partial) ──
    credentials = list_shared_credentials(include_values=True)
    domains_needing_auth = _get_domains_needing_auth(base_dir)

    return render(request, 'site_adapters/site_adapters.html', {
        'config_content': config_content,
        'base_dir': base_dir,
        'adapters_dir': adapters_dir,
        'authority_lists_json': json.dumps({
            'singlefile_args': sorted(get_singlefile_args_set()),
            'defuddle_params': sorted(get_defuddle_params_set()),
        }, ensure_ascii=False),
        # Credentials partial context
        'credentials': credentials,
        'cred_q': '',
        'credentials_json': json.dumps(credentials, ensure_ascii=False),
        'auth_domains_json': json.dumps([
            {
                'd': d['domain'],
                'c': d['needs_cookie'],
                'h': d['needs_headers'],
                't': d.get('needs_oauth2', d.get('needs_token', False)),
                'b': d.get('needs_basic_auth', False),
                'ct': d.get('cookie_type', 'auto'),
                'help': {
                    'c': d.get('cookie_help', ''),
                    'h': d.get('headers_help', ''),
                    't': d.get('oauth2_help', ''),
                    'b': d.get('basic_help', ''),
                },
            }
            for d in domains_needing_auth
        ], ensure_ascii=False),
    })
