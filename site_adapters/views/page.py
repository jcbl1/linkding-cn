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
from site_adapters.services.auth.credentials import (
    get_auth_requirements_for_domain_key,
    list_shared_credentials,
)
from site_adapters.services.config.loader import _cache


def _get_domains_needing_auth(base_dir):
    """Return list of {domain, needs_cookie, needs_headers, needs_token}."""
    domains = []
    if not base_dir or not os.path.isdir(base_dir):
        return domains
    all_config = _cache.load(base_dir)
    for key in sorted(k for k in all_config if k != 'defaults' and not k.startswith('_')):
        auth = get_auth_requirements_for_domain_key(key, base_dir=base_dir)
        if auth['cookie'] or auth['headers'] or auth['token']:
            domains.append({
                'domain': key,
                'needs_cookie': auth['cookie'],
                'needs_headers': auth['headers'],
                'needs_token': auth['token'],
            })
    return domains


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
            {'d': d['domain'], 'c': d['needs_cookie'], 'h': d['needs_headers'], 't': d['needs_token']}
            for d in domains_needing_auth
        ], ensure_ascii=False),
    })
