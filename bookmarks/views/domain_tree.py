from django.core.cache import cache
from django.http import HttpResponse, HttpResponseBadRequest
from django.template import loader
from django.views.decorators.cache import cache_control

from bookmarks.models import BookmarkSearch
from bookmarks.views import contexts

_DOMAIN_CONTEXT_MAP = {
    "active": contexts.ActiveDomainsContext,
    "archived": contexts.ArchivedDomainsContext,
    "shared": contexts.SharedDomainsContext,
    "trash": contexts.TrashedDomainsContext,
    "highlights": contexts.HighlightDomainsContext,
}

_CACHE_TTL = 60  # seconds

_FILTER_PARAMS = [
    "q", "shared", "unread", "tagged",
    "modified_since", "added_since", "deleted_since",
    "date_filter_by", "date_filter_type", "date_filter_relative_string",
    "date_filter_start", "date_filter_end",
    "html_snapshot", "preview_image", "favicon",
    "highlight", "annotation",
]


def _cache_key(user_id, ctx_key, request_get, view_mode):
    parts = [f"domain-tree:{user_id}:{ctx_key}:{view_mode}"]
    for param in _FILTER_PARAMS:
        val = request_get.get(param, "")
        if val:
            parts.append(f"{param}={val}")
    return "|".join(parts)


def _get_domain_context(request, search, ctx_key):
    # Prefer explicit URL param (set by JS to bust browser HTTP cache),
    # fall back to user profile setting.
    view_mode = request.GET.get("view_mode") or request.user_profile.domain_view_mode
    cache_key = _cache_key(request.user.id, ctx_key, request.GET, view_mode)
    domain_context = cache.get(cache_key)
    if domain_context is not None:
        return domain_context

    domain_ctx_cls = _DOMAIN_CONTEXT_MAP.get(ctx_key, contexts.ActiveDomainsContext)
    domain_context = domain_ctx_cls(request, search)
    cache.set(cache_key, domain_context, _CACHE_TTL)
    return domain_context


@cache_control(private=True, max_age=60)
def domain_tree_children(request):
    node_id = request.GET.get("node_id", "")
    if not node_id:
        return HttpResponseBadRequest("missing node_id")

    search = BookmarkSearch.from_request(
        request, request.GET, request.user_profile.search_preferences
    )
    ctx_key = request.GET.get("ctx", "active")

    domain_context = _get_domain_context(request, search, ctx_key)
    children = _find_node_children(domain_context.items, node_id)

    if children is None:
        return HttpResponseBadRequest("node not found")

    template = loader.get_template(
        "bookmarks/sidebar/modules/domains/tree_children.html"
    )
    return HttpResponse(
        template.render({"domains": domain_context, "children": children}, request)
    )


def _find_node_children(items, node_id):
    for item in items:
        if item.node_id == node_id:
            return item.children if hasattr(item, 'children') else []
        if hasattr(item, 'children') and item.children:
            result = _find_node_children(item.children, node_id)
            if result is not None:
                return result
    return None
