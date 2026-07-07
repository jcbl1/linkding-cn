from django.http import HttpResponse, HttpResponseBadRequest
from django.template import loader
from django.views.decorators.cache import cache_control

from bookmarks.models import BookmarkSearch
from bookmarks.views import contexts


# Maps the ``ctx`` query parameter to the appropriate RequestContext class.
_CONTEXT_MAP = {
    "active": contexts.ActiveBookmarksContext,
    "archived": contexts.ArchivedBookmarksContext,
    "shared": contexts.SharedBookmarksContext,
    "trash": contexts.TrashedBookmarksContext,
    "highlights": contexts.HighlightRequestContext,
}


@cache_control(private=True, max_age=60)
def tag_tree_children(request):
    """AJAX endpoint: return rendered HTML for the children of a tree node.

    Query params:
        path  – comma-separated tag names from root to the target node
        ctx   – context key (active|archived|shared|trash|highlights)
        q     – optional search query (same as the main search bar)
    """
    path_str = request.GET.get("path", "")
    path_names = [p.strip() for p in path_str.split(",") if p.strip()]
    if not path_names:
        return HttpResponseBadRequest("missing path")

    search = BookmarkSearch.from_request(
        request, request.GET, request.user_profile.search_preferences
    )

    ctx_key = request.GET.get("ctx", "active")
    rc_cls = _CONTEXT_MAP.get(ctx_key, contexts.ActiveBookmarksContext)
    rc = rc_cls(request)

    children = contexts.get_tag_tree_children(rc, request.user, search, path_names)

    template = loader.get_template(
        "bookmarks/sidebar/modules/tags/tree_children.html"
    )
    return HttpResponse(
        template.render({"nodes": children}, request)
    )
