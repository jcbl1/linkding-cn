"""
Subscription management.
"""
import fnmatch
import json
import logging
import os
import shutil
from urllib.parse import urlparse

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from site_adapters.views.helpers import (
    _get_base_dir,
    _get_global_subscriptions,
    _global_config_path,
    _has_subscription_conflict,
    _invalidate_site_adapters_cache,
    _is_safe_subscription_name,
    _load_global_config,
    _save_global_scope,
    _save_global_subscriptions,
    _subscription_cache_dir,
    _subscription_cache_name,
    _subscription_from_post,
    _subscription_index,
    _subscription_payload,
    _subscription_response,
    site_adapters_required,
)

logger = logging.getLogger(__name__)

@site_adapters_required
@require_http_methods(["GET", "POST"])
def subscription_manage(request):
    """管理订阅源列表。列表真源是 global.jsonc 的 _subscriptions。"""
    try:
        subscriptions = _get_global_subscriptions()
    except (json.JSONDecodeError, OSError, ValueError) as e:
        return JsonResponse({'error': str(e)}, status=400)

    if request.method == 'GET':
        return _subscription_response()

    action_name = request.POST.get('action', '')

    try:
        if action_name == 'add':
            item = _subscription_from_post(request)
            if _has_subscription_conflict(subscriptions, item):
                return JsonResponse({'error': 'subscription already exists'}, status=409)
            subscriptions.append(item)
            _save_global_subscriptions(subscriptions)

        elif action_name == 'save':
            index = _subscription_index(request, subscriptions)
            item = _subscription_from_post(request)
            if _has_subscription_conflict(subscriptions, item, ignore_index=index):
                return JsonResponse({'error': 'subscription already exists'}, status=409)
            old = subscriptions[index]
            old_cache_dir = _subscription_cache_dir(old) if isinstance(old, dict) else ''
            if isinstance(old, dict):
                old.update(item)
                item = old
            subscriptions[index] = item
            _save_global_subscriptions(subscriptions)
            new_cache_dir = _subscription_cache_dir(item)
            if old_cache_dir and old_cache_dir != new_cache_dir and os.path.isdir(old_cache_dir):
                shutil.rmtree(old_cache_dir)

        elif action_name == 'set_interval':
            index = _subscription_index(request, subscriptions)
            interval_raw = request.POST.get('update_interval', '').strip()
            try:
                update_interval = int(interval_raw)
            except ValueError as exc:
                raise ValueError('update_interval must be an integer') from exc
            if update_interval <= 0:
                raise ValueError('update_interval must be positive')
            if not isinstance(subscriptions[index], dict):
                raise ValueError('invalid subscription item')
            subscriptions[index]['update_interval'] = update_interval
            _save_global_subscriptions(subscriptions)

        elif action_name == 'delete':
            index = _subscription_index(request, subscriptions)
            old = subscriptions.pop(index)
            _save_global_subscriptions(subscriptions)
            if isinstance(old, dict):
                cache_dir = _subscription_cache_dir(old)
                if os.path.isdir(cache_dir):
                    shutil.rmtree(cache_dir)

        elif action_name == 'move':
            index = _subscription_index(request, subscriptions)
            direction = request.POST.get('direction', '')
            new_index = index - 1 if direction == 'up' else index + 1 if direction == 'down' else index
            if new_index < 0 or new_index >= len(subscriptions):
                return _subscription_response()
            subscriptions[index], subscriptions[new_index] = subscriptions[new_index], subscriptions[index]
            _save_global_subscriptions(subscriptions)

        elif action_name == 'reorder':
            old_index = int(request.POST.get('old_index', '0'))
            new_index = int(request.POST.get('new_index', '0'))
            if 0 <= old_index < len(subscriptions) and 0 <= new_index < len(subscriptions):
                item = subscriptions.pop(old_index)
                subscriptions.insert(new_index, item)
                _save_global_subscriptions(subscriptions)

        elif action_name == 'toggle_enabled':
            index = _subscription_index(request, subscriptions)
            sub = subscriptions[index]
            if not isinstance(sub, dict):
                raise ValueError('invalid subscription item')
            sub['enabled'] = not sub.get('enabled', True)
            _save_global_subscriptions(subscriptions)

        elif action_name == 'update':
            index = _subscription_index(request, subscriptions)
            sub = subscriptions[index]
            if not isinstance(sub, dict):
                raise ValueError('invalid subscription item')
            from site_adapters.services.subscriptions import validate_subscription_url
            try:
                validate_subscription_url(sub.get('url', ''))
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            if not _is_safe_subscription_name(sub.get('name', '')):
                raise ValueError('invalid subscription name')
            from site_adapters.services.subscriptions import (
                fetch_subscription,
            )
            if not fetch_subscription(sub['url'], name=sub.get('name', ''), force=True):
                return JsonResponse({'error': 'subscription fetch failed'}, status=502)
            _invalidate_site_adapters_cache()

        else:
            return JsonResponse({'error': 'unknown action'}, status=400)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        return JsonResponse({'error': str(e)}, status=400)

    return _subscription_response()


# ---------------------------------------------------------------------------
# View Snapshot (§14.2)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Domain listing and toggles
# ---------------------------------------------------------------------------

@site_adapters_required
def all_domains(request):
    """Return all domains grouped by source (local + subscriptions)."""
    base_dir = _get_base_dir()
    domains_dir = os.path.join(base_dir, 'domains')
    subs_dir = os.path.join(base_dir, 'subscriptions')
    global_data, _ = _load_global_config()
    subscriptions = global_data.get('_subscriptions', [])

    # Local domains
    local_domains = []
    disabled_set = set((global_data.get('*', {}) or {}).get('_disabled_domains', []))
    if os.path.isdir(domains_dir):
        for fname in sorted(os.listdir(domains_dir)):
            if not (fname.endswith('.jsonc') or fname.endswith('.json')):
                continue
            domain_key = fname[:-6] if fname.endswith('.jsonc') else fname[:-5]
            local_domains.append(domain_key)

    # Subscription domains (single-file format)
    sub_groups = []
    for sub in subscriptions:
        if not isinstance(sub, dict) or sub.get('enabled') is False:
            continue
        sub_name = sub.get('name', '') or sub.get('url', '')
        cache_name = _subscription_cache_name(sub)
        sub_file = os.path.join(subs_dir, cache_name, 'subscription.jsonc')
        exclude = sub.get('exclude', [])
        domains = []

        if os.path.exists(sub_file):
            try:
                from site_adapters.services.subscriptions import _read_subscription_file
                sub_data = _read_subscription_file(sub_file)
                if sub_data and isinstance(sub_data.get('domains'), dict):
                    for domain_key in sorted(sub_data['domains'].keys()):
                        is_excluded = any(fnmatch.fnmatch(domain_key, pat) for pat in exclude)
                        is_overridden = domain_key in local_domains and domain_key not in disabled_set
                        domains.append({
                            'domain': domain_key,
                            'enabled': not is_excluded,
                            'overridden': is_overridden,
                        })
            except (json.JSONDecodeError, OSError):
                pass

        if domains:
            sub_groups.append({
                'name': sub_name,
                'index': subscriptions.index(sub),
                'domains': domains,
            })

    return JsonResponse({
        'local': local_domains,
        'subscriptions': sub_groups,
    })


@site_adapters_required
@require_POST
def subscription_domain_toggle(request):
    """Enable/disable a domain within a subscription."""
    index_raw = request.POST.get('index', '')
    domain = request.POST.get('domain', '').strip()
    enable = request.POST.get('enable', 'true') == 'true'

    global_data, global_text = _load_global_config()
    subscriptions = global_data.get('_subscriptions', [])
    try:
        index = int(index_raw)
    except ValueError:
        return JsonResponse({'error': 'invalid index'}, status=400)
    if index < 0 or index >= len(subscriptions):
        return JsonResponse({'error': 'index out of range'}, status=400)

    sub = subscriptions[index]
    if not isinstance(sub, dict):
        return JsonResponse({'error': 'invalid subscription'}, status=400)

    exclude = list(sub.get('exclude', []))
    if enable:
        exclude = [p for p in exclude if not fnmatch.fnmatch(domain, p)]
    else:
        if domain not in exclude:
            exclude.append(domain)
    sub['exclude'] = exclude
    _save_global_subscriptions(subscriptions)

    _invalidate_site_adapters_cache()
    return JsonResponse({'success': True, 'exclude': exclude})


@site_adapters_required
@require_POST
def local_domain_toggle(request):
    """Enable/disable a local domain."""
    domain = request.POST.get('domain', '').strip()
    enable = request.POST.get('enable', 'true') == 'true'
    if not domain:
        return JsonResponse({'error': 'domain required'}, status=400)

    global_data, global_text = _load_global_config()
    star = global_data.get('*', {})
    disabled = list(star.get('_disabled_domains', []))

    if enable:
        disabled = [d for d in disabled if d != domain]
    else:
        if domain not in disabled:
            disabled.append(domain)

    star['_disabled_domains'] = disabled
    global_data['*'] = star
    # Save both _subscriptions and * scope
    _save_global_scope('*', json.dumps(star, ensure_ascii=False))
    _save_global_subscriptions(global_data.get('_subscriptions', []))

    _invalidate_site_adapters_cache()
    return JsonResponse({'success': True, 'disabled': disabled})


@site_adapters_required
def subscription_domain_read(request):
    """Read a subscription domain's config file."""
    index_raw = request.GET.get('index', '')
    domain = request.GET.get('domain', '').strip()
    if not domain:
        return JsonResponse({'error': 'domain required'}, status=400)

    global_data, _ = _load_global_config()
    subscriptions = global_data.get('_subscriptions', [])
    try:
        index = int(index_raw)
    except ValueError:
        return JsonResponse({'error': 'invalid index'}, status=400)
    if index < 0 or index >= len(subscriptions):
        return JsonResponse({'error': 'index out of range'}, status=400)

    sub = subscriptions[index]
    cache_name = _subscription_cache_name(sub)
    sub_file = os.path.join(_get_base_dir(), 'subscriptions', cache_name, 'subscription.jsonc')

    if os.path.exists(sub_file):
        try:
            from site_adapters.services.subscriptions import _read_subscription_file
            sub_data = _read_subscription_file(sub_file)
            if sub_data and isinstance(sub_data.get('domains'), dict):
                domain_config = sub_data['domains'].get(domain)
                if domain_config is not None:
                    content = json.dumps(domain_config, indent=2, ensure_ascii=False)
                    return JsonResponse({'domain': domain, 'content': content, 'source': sub.get('name', '') or sub.get('url', '')})
        except (json.JSONDecodeError, OSError):
            pass

    return JsonResponse({'error': 'domain not found in subscription'}, status=404)

