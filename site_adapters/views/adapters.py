"""
User-facing site adapter settings page.
"""
import json
import logging

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.urls import reverse

from bookmarks.utils import is_safe_domain_key
from site_adapters.services.auth.credentials import (
    delete_user_basic_auth,
    delete_user_cookie,
    delete_user_header,
    delete_user_token,
    list_user_credentials,
    save_user_basic_auth,
    save_user_cookie,
    save_user_header,
    save_user_token,
)
from site_adapters.services.config.resolver import (
    get_user_preferences,
    list_domains_with_toggles,
    save_user_preferences,
)
from site_adapters.views.credentials import _get_domains_needing_auth
from site_adapters.views.helpers import _get_base_dir

logger = logging.getLogger(__name__)

TOGGLES_PAGE_SIZE = 50


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
            save_user_cookie(username=username, domain=bm_domain, cookie_str=bm_cookie)
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
                    save_user_cookie(username=username, domain=domain, cookie_str=cookie_str)
            elif cred_type == 'header':
                header_name = request.POST.get('header_name', '').strip()
                header_value = request.POST.get('value', '').strip()
                if header_name and header_value:
                    save_user_header(username=username, domain=domain, header_name=header_name, value=header_value)
            elif cred_type == 'oauth2':
                token_value = request.POST.get('value', '').strip()
                if token_value:
                    save_user_token(username=username, domain=domain, refresh_token=token_value)
            elif cred_type == 'basic_auth':
                username_val = request.POST.get('username', '').strip()
                password_val = request.POST.get('password', '').strip()
                if username_val and password_val:
                    save_user_basic_auth(username=username, domain=domain, username_val=username_val, password_val=password_val)
            return redirect(request.META.get('HTTP_REFERER', adapters_url))

        elif action == 'delete_credential':
            domain = request.POST.get('domain', '').strip()
            cred_type = request.POST.get('type', 'cookie')
            if domain and is_safe_domain_key(domain):
                if cred_type == 'cookie':
                    delete_user_cookie(username=username, domain=domain)
                elif cred_type == 'header':
                    header_name = request.POST.get('header_name', '').strip()
                    if header_name:
                        delete_user_header(username=username, domain=domain, header_name=header_name)
                elif cred_type == 'oauth2':
                    delete_user_token(username=username, domain=domain)
                elif cred_type == 'basic_auth':
                    delete_user_basic_auth(username=username, domain=domain)
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
            'scope': cr.get('scope', ''),
            'cookie': cr.get('cookie', ''),
            'token': cr.get('token', ''),
            'oauth2': cr.get('token', ''),
            'basic_auth_username': cr.get('basic_auth_username', ''),
            'basic_auth_password': cr.get('basic_auth_password', ''),
            'header_names': cr.get('header_names', []),
            'header_values': cr.get('header_values', {}),
        }
        for cr in all_credentials
    ])

    # Domains needing auth (for add credential modal autocomplete)
    domains_needing_auth = _get_domains_needing_auth(base_dir)
    ctx['auth_domains_json'] = json.dumps([
        {'d': d['domain'], 'c': d['domain_auth'].get('cookie', False), 'h': d['domain_auth'].get('headers', []), 't': d['domain_auth'].get('oauth2', False), 'b': d['domain_auth'].get('basic_auth', False), 'ct': d['domain_auth'].get('cookie_type', 'auto'), 'help': {'c': d['domain_auth'].get('cookie_help', ''), 'h': d['domain_auth'].get('headers_help', ''), 't': d['domain_auth'].get('oauth2_help', ''), 'b': d['domain_auth'].get('basic_help', '')}, 'sections': d.get('sections', {})}
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
    ctx['page_title'] = 'Adapters'

    # Turbo needs the matching frame tag; <title> prevents it from clearing the page title.
    if request.headers.get('Turbo-Frame') == 'toggle-prefs-frame':
        from django.http import HttpResponse
        from django.template.loader import render_to_string
        inner = render_to_string('settings/adapters_toggle_list.html', ctx, request=request)
        title = ctx.get('page_title', 'Adapters')
        html = f'<title>{title} - Linkding</title><turbo-frame id="toggle-prefs-frame">{inner}</turbo-frame>'
        return HttpResponse(html)

    return render(request, 'settings/adapters.html', ctx)
