"""
Snapshot toggle preference endpoint.
"""
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from bookmarks.utils import is_safe_domain_key
from site_adapters.services.config.resolver import save_user_preferences

logger = logging.getLogger(__name__)


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
