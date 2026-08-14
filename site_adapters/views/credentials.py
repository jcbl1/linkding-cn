"""
Credential management endpoints.
"""
import os

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from bookmarks.utils import is_safe_domain_key
from site_adapters.services.auth.credentials import (
    delete_shared_basic_auth,
    delete_shared_cookie,
    delete_shared_header,
    delete_shared_token,
    get_auth_requirements_for_domain_key,
    list_shared_credentials,
    save_shared_basic_auth,
    save_shared_cookie,
    save_shared_header,
    save_shared_token,
)
from site_adapters.views.helpers import _get_base_dir


def _get_domains_needing_auth(base_dir):
    """Return list of {domain, needs_cookie, needs_headers, needs_oauth2}."""
    domains = []
    if not base_dir or not os.path.isdir(base_dir):
        return domains
    from site_adapters.services.config.loader import _cache
    all_config = _cache.load(base_dir)
    for key in sorted(k for k in all_config if k != 'defaults' and not k.startswith('_')):
        auth = get_auth_requirements_for_domain_key(key, base_dir=base_dir)
        if auth['cookie'] or auth['headers'] or auth.get('oauth2', auth.get('token', False)) or auth.get('basic_auth'):
            domains.append({
                'domain': key,
                'needs_cookie': auth['cookie'],
                'needs_headers': auth['headers'],
                'needs_oauth2': auth.get('oauth2', auth.get('token', False)),
                'needs_basic_auth': bool(auth.get('basic_auth')),
                'cookie_help': auth.get('cookie_help', ''),
                'headers_help': auth.get('headers_help', ''),
                'oauth2_help': auth.get('oauth2_help', ''),
                'basic_help': auth.get('basic_help', ''),
                'cookie_type': auth.get('cookie_type', 'auto'),
            })
    return domains


@login_required
@require_http_methods(["GET"])
def user_credentials(request):
    """Return domains needing auth as JSON (for add-credential modal dropdown)."""
    base_dir = _get_base_dir()
    domains = _get_domains_needing_auth(base_dir)
    return JsonResponse({
        'domains': [{'domain': d['domain'], 'needs_cookie': d['needs_cookie'],
                      'needs_headers': d['needs_headers'], 'needs_token': d['needs_oauth2'],
                      'needs_basic_auth': d.get('needs_basic_auth', False), 'cookie_help': d.get('cookie_help', ''), 'headers_help': d.get('headers_help', ''), 'oauth2_help': d.get('oauth2_help', ''), 'basic_help': d.get('basic_help', ''), 'cookie_type': d.get('cookie_type', 'anon')}
                     for d in domains],
    })


# ── Shared credential management views ──

@login_required
@require_http_methods(["GET"])
def shared_credential_list(request):
    """List all shared credentials. Returns JSON for the admin page credentials tab."""
    base_dir = _get_base_dir()
    domains = _get_domains_needing_auth(base_dir)
    credentials = list_shared_credentials(include_values=True)
    return JsonResponse({
        'credentials': credentials,
        'domains': [{'domain': d['domain'], 'needs_cookie': d['needs_cookie'],
                      'needs_headers': d['needs_headers'], 'needs_token': d['needs_oauth2'],
                      'needs_basic_auth': d.get('needs_basic_auth', False), 'cookie_help': d.get('cookie_help', ''), 'headers_help': d.get('headers_help', ''), 'oauth2_help': d.get('oauth2_help', ''), 'basic_help': d.get('basic_help', ''), 'cookie_type': d.get('cookie_type', 'anon')}
                     for d in domains],
    })


@login_required
@require_http_methods(["POST"])
def shared_credential_save(request):
    """Save a shared credential. Requires staff/superuser access."""
    if not request.user.is_staff:
        return JsonResponse({'error': 'Permission denied'}, status=403)

    domain = request.POST.get('domain', '').strip()
    cred_type = request.POST.get('type', 'cookie')

    if not domain or not is_safe_domain_key(domain):
        return JsonResponse({'error': 'Invalid domain'}, status=400)

    if cred_type == 'cookie':
        cookie_str = request.POST.get('value', '').strip()
        if not cookie_str:
            return JsonResponse({'error': 'Cookie value required'}, status=400)
        save_shared_cookie(domain, cookie_str)
    elif cred_type == 'header':
        header_name = request.POST.get('header_name', '').strip()
        header_value = request.POST.get('value', '').strip()
        if not header_name or not header_value:
            return JsonResponse({'error': 'Header name and value required'}, status=400)
        save_shared_header(domain, header_name, header_value)
    elif cred_type == 'oauth2':
        token_value = request.POST.get('value', '').strip()
        if not token_value:
            return JsonResponse({'error': 'Token value required'}, status=400)
        save_shared_token(domain, token_value)
    elif cred_type == 'basic_auth':
        username_val = request.POST.get('username', '').strip()
        password_val = request.POST.get('password', '').strip()
        if not username_val or not password_val:
            return JsonResponse({'error': 'Username and password required'}, status=400)
        save_shared_basic_auth(domain, username_val, password_val)
    else:
        return JsonResponse({'error': 'Invalid credential type'}, status=400)

    return JsonResponse({'success': True})


@login_required
@require_http_methods(["POST"])
def shared_credential_delete(request):
    """Delete a shared credential. Requires staff/superuser access."""
    if not request.user.is_staff:
        return JsonResponse({'error': 'Permission denied'}, status=403)

    domain = request.POST.get('domain', '').strip()
    cred_type = request.POST.get('type', 'cookie')

    if not domain or not is_safe_domain_key(domain):
        return JsonResponse({'error': 'Invalid domain'}, status=400)

    if cred_type == 'cookie':
        delete_shared_cookie(domain)
    elif cred_type == 'header':
        header_name = request.POST.get('header_name', '').strip()
        if not header_name:
            return JsonResponse({'error': 'Header name required'}, status=400)
        delete_shared_header(domain, header_name)
    elif cred_type == 'oauth2':
        delete_shared_token(domain)
    elif cred_type == 'basic_auth':
        delete_shared_basic_auth(domain)
    else:
        return JsonResponse({'error': 'Invalid credential type'}, status=400)

    return JsonResponse({'success': True})
