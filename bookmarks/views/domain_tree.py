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


def _get_domain_context(request, search, ctx_key):
    domain_ctx_cls = _DOMAIN_CONTEXT_MAP.get(ctx_key, contexts.ActiveDomainsContext)
    return domain_ctx_cls(request, search)


@cache_control(private=True, max_age=0)
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
