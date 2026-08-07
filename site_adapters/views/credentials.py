"""
User adapters page — credentials + snapshot preferences.
"""
import json
import logging
import os

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from site_adapters.views.helpers import _get_base_dir
from bookmarks.utils import is_safe_domain_key
from site_adapters.services.auth.credentials import (
    get_auth_requirements_for_domain_key,
    list_user_credentials,
    list_shared_credentials,
    save_user_cookie,
    save_user_header,
    save_user_token,
    save_shared_cookie,
    save_shared_header,
    save_shared_token,
    delete_user_cookie,
    delete_user_header,
    delete_user_token,
    delete_shared_cookie,
    delete_shared_header,
    delete_shared_token,
    get_user_preferences,
    save_user_preferences,
    list_domains_with_toggles,
)
from django.conf import settings as django_settings

logger = logging.getLogger(__name__)

TOGGLES_PAGE_SIZE = 50


def _get_domains_needing_auth(base_dir):
    """Return list of {domain, needs_cookie, needs_headers, needs_token}."""
    domains = []
    if not base_dir or not os.path.isdir(base_dir):
        return domains
    from site_adapters.services.config.loader import _cache
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


@login_required
def adapters_page(request):
    """Render the adapters page and handle all form actions."""
    username = request.user.username
    base_dir = _get_base_dir()
    adapters_url = reverse('linkding:settings.adapters')

    # ── Bookmarklet auto-save ──
    if request.method == 'GET':
        bm_domain = request.GET.get('domain', '')
        bm_cookie = request.GET.get('cookie', '')
        if bm_domain and bm_cookie and is_safe_domain_key(bm_domain):
            save_user_cookie(username, bm_domain, bm_cookie)
            return redirect(adapters_url)

    # ── POST actions ──
    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'save_credential':
            domain = request.POST.get('domain', '').strip()
            cred_type = request.POST.get('type', 'cookie')
            if not domain or not is_safe_domain_key(domain):
                return redirect(adapters_url)
            if cred_type == 'cookie':
                cookie_str = request.POST.get('value', '').strip()
                if cookie_str:
                    save_user_cookie(username, domain, cookie_str)
            elif cred_type == 'header':
                header_name = request.POST.get('header_name', '').strip()
                header_value = request.POST.get('value', '').strip()
                if header_name and header_value:
                    save_user_header(username, domain, header_name, header_value)
            elif cred_type == 'token':
                token_value = request.POST.get('value', '').strip()
                if token_value:
                    save_user_token(username, domain, token_value)
            return redirect(request.META.get('HTTP_REFERER', adapters_url))

        elif action == 'delete_credential':
            domain = request.POST.get('domain', '').strip()
            cred_type = request.POST.get('type', 'cookie')
            if domain and is_safe_domain_key(domain):
                if cred_type == 'cookie':
                    delete_user_cookie(username, domain)
                elif cred_type == 'header':
                    header_name = request.POST.get('header_name', '').strip()
                    if header_name:
                        delete_user_header(username, domain, header_name)
                elif cred_type == 'token':
                    delete_user_token(username, domain)
            return redirect(request.META.get('HTTP_REFERER', adapters_url))

        elif action == 'toggle_pref':
            domain = request.POST.get('domain', '').strip()
            toggle_id = request.POST.get('toggle_id', '').strip()
            enabled = request.POST.get('enabled', 'true') == 'true'
            if domain and toggle_id and is_safe_domain_key(domain):
                save_user_preferences(username, domain, toggle_id, enabled)
            return redirect(request.META.get('HTTP_REFERER', adapters_url))

        return redirect(adapters_url)

    # ── GET: build page context ──
    ctx = {}

    # Credentials
    cred_q = request.GET.get('q', '').strip().lower()
    all_credentials = list_user_credentials(username)
    if cred_q:
        credentials = [c for c in all_credentials if cred_q in c['domain'].lower()]
    else:
        credentials = all_credentials

    ctx['credentials'] = credentials
    ctx['cred_q'] = cred_q

    # Pass full credential values as JSON for the edit modal
    ctx['credentials_json'] = json.dumps([
        {
            'domain': cr['domain'],
            'type': cr['type'],
            'cookie': cr.get('cookie', ''),
            'token': cr.get('token', ''),
            'header_names': cr.get('header_names', []),
            'header_values': cr.get('header_values', {}),
        }
        for cr in all_credentials
    ])

    # Domains needing auth (for add credential modal autocomplete)
    domains_needing_auth = _get_domains_needing_auth(base_dir)
    ctx['auth_domains_json'] = json.dumps([
        {'d': d['domain'], 'c': d['needs_cookie'], 'h': d['needs_headers'], 't': d['needs_token']}
        for d in domains_needing_auth
    ])

    # Toggles
    toggle_q = request.GET.get('tq', '').strip().lower()
    modified_only = request.GET.get('modified_only') == '1'
    page_num = request.GET.get('page', '1')

    all_toggle_domains = list_domains_with_toggles(base_dir) if base_dir else []
    user_prefs = get_user_preferences(username)

    # Enrich with counts and filter
    enriched = []
    for d in all_toggle_domains:
        toggles = d.get('toggles', {})
        domain = d['domain']
        total = len(toggles)
        modified = 0
        prefs = user_prefs.get(domain, {})

        # Pre-compute toggle items with resolved checked state
        items = []
        for tid, tdef in toggles.items():
            default = tdef.get('default', True)
            user_val = prefs.get(tid)
            checked = user_val if user_val is not None else default
            if user_val is not None and user_val != default:
                modified += 1
            items.append({
                'id': tid,
                'label': tdef.get('label', tid),
                'selector': tdef.get('selector', ''),
                'default': default,
                'checked': checked,
            })

        # Search filter
        if toggle_q and toggle_q not in domain.lower():
            continue
        # Modified-only filter
        if modified_only and modified == 0:
            continue

        enriched.append({
            'domain': domain,
            'total': total,
            'modified': modified,
            'items': items,
        })

    paginator = Paginator(enriched, TOGGLES_PAGE_SIZE)
    page_obj = paginator.get_page(page_num)

    ctx['toggle_domains'] = page_obj
    ctx['toggle_q'] = toggle_q
    ctx['modified_only'] = modified_only
    ctx['page_title'] = 'Adapters'  # for <title> in frame responses

    # If this is a Turbo Frame request, return <title> +
    # <turbo-frame> wrapper.  Turbo needs the matching frame tag to
    # extract content; <title> prevents it from clearing the page title.
    if request.headers.get('Turbo-Frame') == 'toggle-prefs-frame':
        from django.template.loader import render_to_string
        from django.http import HttpResponse
        inner = render_to_string('settings/adapters_toggle_list.html', ctx, request=request)
        title = ctx.get('page_title', 'Adapters')
        html = f'<title>{title} - Linkding</title><turbo-frame id="toggle-prefs-frame">{inner}</turbo-frame>'
        return HttpResponse(html)

    return render(request, 'settings/adapters.html', ctx)


@login_required
@require_http_methods(["GET"])
def user_credentials(request):
    """Return domains needing auth as JSON (for add-credential modal dropdown)."""
    base_dir = _get_base_dir()
    domains = _get_domains_needing_auth(base_dir)
    return JsonResponse({
        'domains': [{'domain': d['domain'], 'needs_cookie': d['needs_cookie'],
                      'needs_headers': d['needs_headers'], 'needs_token': d['needs_token']}
                     for d in domains],
    })


@login_required
@require_http_methods(["POST"])
def snapshot_toggles(request):
    """Save a single toggle preference. Returns JSON."""
    username = request.user.username
    domain = request.POST.get('domain', '').strip()
    toggle_id = request.POST.get('toggle_id', '').strip()
    enabled = request.POST.get('enabled', 'true') == 'true'

    if not domain or not toggle_id or not is_safe_domain_key(domain):
        return JsonResponse({'error': 'invalid parameters'}, status=400)

    try:
        save_user_preferences(username, domain, toggle_id, enabled)
        return JsonResponse({'success': True})
    except Exception as e:
        logger.exception('Failed to save toggle preference')
        return JsonResponse({'error': str(e)}, status=500)


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
                      'needs_headers': d['needs_headers'], 'needs_token': d['needs_token']}
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
    elif cred_type == 'token':
        token_value = request.POST.get('value', '').strip()
        if not token_value:
            return JsonResponse({'error': 'Token value required'}, status=400)
        save_shared_token(domain, token_value)
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
    elif cred_type == 'token':
        delete_shared_token(domain)
    else:
        return JsonResponse({'error': 'Invalid credential type'}, status=400)

    return JsonResponse({'success': True})
