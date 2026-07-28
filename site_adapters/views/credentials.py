"""
User credential management + cookie page.
"""
import logging
import os

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from site_adapters.views.helpers import _get_base_dir
from bookmarks.utils import is_safe_domain_key
from site_adapters.services.auth.credentials import (
    get_auth_requirements_for_domain_key,
    list_user_credentials,
    save_user_cookie,
    save_user_header,
    save_user_token,
    delete_user_cookie,
    delete_user_header,
    delete_user_token,
)

logger = logging.getLogger(__name__)

@login_required
@require_http_methods(["GET", "POST"])
def user_cookies(request):
    """User credentials management API."""
    username = request.user.username

    if request.method == 'GET':
        base_dir = _get_base_dir()
        domains_needing_auth = []
        if base_dir and os.path.isdir(base_dir):
            from site_adapters.services.config.loader import _cache
            all_config = _cache.load(base_dir)
            domain_keys = sorted(
                key for key in all_config
                if key != '*' and not key.startswith('_')
            )
            for domain_key in domain_keys:
                auth = get_auth_requirements_for_domain_key(domain_key, base_dir=base_dir)
                if auth['cookie'] or auth['headers'] or auth['token']:
                    domains_needing_auth.append({
                        'domain': domain_key,
                        'needs_cookie': auth['cookie'],
                        'needs_headers': auth['headers'],
                        'needs_token': auth['token'],
                    })

        # Get user's existing credentials
        credentials = list_user_credentials(username)

        return JsonResponse({
            'domains': domains_needing_auth,
            'credentials': credentials,
        })

    # POST: save or delete
    action = request.POST.get('action', '')
    domain = request.POST.get('domain', '').strip()
    if not domain:
        return JsonResponse({'error': 'domain required'}, status=400)
    if not is_safe_domain_key(domain):
        return JsonResponse({'error': 'invalid domain'}, status=400)

    cred_type = request.POST.get('type', 'cookie')

    if action == 'save':
        if cred_type == 'cookie':
            cookie_str = request.POST.get('value', '').strip()
            save_user_cookie(username, domain, cookie_str)
        elif cred_type == 'header':
            header_name = request.POST.get('header_name', '').strip()
            header_value = request.POST.get('value', '').strip()
            if not header_name:
                return JsonResponse({'error': 'header_name required'}, status=400)
            save_user_header(username, domain, header_name, header_value)
        elif cred_type == 'token':
            token_value = request.POST.get('value', '').strip()
            if not token_value:
                return JsonResponse({'error': 'token value required'}, status=400)
            save_user_token(username, domain, token_value)
        else:
            return JsonResponse({'error': 'invalid type'}, status=400)
        return JsonResponse({'success': True})
    elif action == 'delete':
        if cred_type == 'cookie':
            delete_user_cookie(username, domain)
        elif cred_type == 'header':
            header_name = request.POST.get('header_name', '').strip()
            delete_user_header(username, domain, header_name)
        elif cred_type == 'token':
            delete_user_token(username, domain)
        else:
            return JsonResponse({'error': 'invalid type'}, status=400)
        return JsonResponse({'success': True})

    return JsonResponse({'error': 'invalid action'}, status=400)


@login_required
def cookies_page(request):
    """Render the cookie management page."""
    return render(request, 'settings/site_adapters_user.html')


@login_required
@require_http_methods(["GET", "POST"])
def user_toggles(request):
    """User snapshot toggle preferences API."""
    from django.conf import settings as django_settings
    from site_adapters.services.auth.credentials import (
        get_user_preferences,
        save_user_preferences,
        list_domains_with_toggles,
    )

    username = request.user.username
    base_dir = getattr(django_settings, 'LD_SITE_ADAPTERS_DIR', '')

    if request.method == 'GET':
        # List all domains with toggles and user's current preferences
        domains_with_toggles = list_domains_with_toggles(base_dir) if base_dir else []
        user_prefs = get_user_preferences(username)
        return JsonResponse({
            'domains': domains_with_toggles,
            'preferences': user_prefs,
        })

    # POST: save toggle preference
    domain = request.POST.get('domain', '').strip()
    toggle_id = request.POST.get('toggle_id', '').strip()
    enabled = request.POST.get('enabled', 'true') == 'true'

    if not domain or not toggle_id:
        return JsonResponse({'error': 'domain and toggle_id required'}, status=400)
    if not is_safe_domain_key(domain):
        return JsonResponse({'error': 'invalid domain'}, status=400)

    save_user_preferences(username, domain, toggle_id, enabled)
    return JsonResponse({'success': True})
