import html

from django.contrib.auth.decorators import login_required
from django.http import (
    HttpResponseBadRequest,
    HttpResponseRedirect,
)
from django.shortcuts import render
from django.utils import timezone
from django.urls import reverse
from django.utils.translation import gettext as _

from bookmarks.models import Bookmark, BookmarkAsset
from bookmarks.services import tasks, website_loader
from bookmarks.type_defs import HttpRequest
from bookmarks.views import access


@login_required
def read(request: HttpRequest, bookmark_id: int):
    from bookmarks.services.articles import remove_article
    from bookmarks.services.wayback import generate_fallback_webarchive_url

    bookmark = access.bookmark_read(request, bookmark_id)
    is_owner = bookmark.owner == request.user

    # Non-owners cannot trigger article generation
    if not is_owner:
        has_article = (
            bookmark.latest_article
            and bookmark.latest_article.status == BookmarkAsset.STATUS_COMPLETE
        )
        if not has_article:
            # Build snapshot URL (same logic as BookmarkItem)
            if bookmark.latest_snapshot_id:
                snapshot_url = reverse(
                    "linkding:assets.view", args=[bookmark.latest_snapshot_id]
                )
            else:
                snapshot_url = bookmark.web_archive_snapshot_url
                if not snapshot_url:
                    snapshot_url = generate_fallback_webarchive_url(
                        bookmark.url, bookmark.date_added
                    )

            web_archive_url = generate_fallback_webarchive_url(
                bookmark.url, bookmark.date_added
            )

            from bookmarks.models import FaviconCache
            from bookmarks.utils import extract_hostname, parse_domain_roots, resolve_favicon_domain
            hostname = extract_hostname(bookmark.url)
            favicon_file = ""
            favicon_unavailable = False
            if hostname:
                domain_config = parse_domain_roots(request.user_profile.custom_domain_root)
                domain = resolve_favicon_domain(hostname, config=domain_config)
                fc = FaviconCache.objects.filter(domain=domain).first()
                if fc and fc.status == FaviconCache.STATUS_SUCCESS and fc.favicon_file:
                    favicon_file = fc.favicon_file
                elif fc and fc.status == FaviconCache.STATUS_MISSING:
                    favicon_unavailable = True
                elif fc and fc.status == FaviconCache.STATUS_FAILED:
                    # 与 FaviconLookup 逻辑一致：只有未到重试时间的才标记不可用
                    if fc.next_retry_at and fc.next_retry_at > timezone.now():
                        favicon_unavailable = True

            return render(
                request,
                "bookmarks/reader/read_unavailable.html",
                {
                    "bookmark": bookmark,
                    "favicon_file": favicon_file,
                    "favicon_unavailable": favicon_unavailable,
                    "snapshot_url": snapshot_url,
                    "web_archive_url": web_archive_url,
                    "is_authenticated": request.user.is_authenticated,
                    "api_base_url": reverse("linkding:api-root").rstrip("/"),
                },
            )

    # Check if current user already has a bookmark with the same URL
    existing_bookmark_id = None
    if not is_owner and request.user.is_authenticated:
        existing = Bookmark.query_existing(request.user, bookmark.url).first()
        if existing:
            existing_bookmark_id = existing.id

    bookmark_data = {
        "id": bookmark.id,
        "url": bookmark.url,
        "title": bookmark.resolved_title,
        "description": bookmark.resolved_description,
        "notes": bookmark.notes,
        "tag_names": bookmark.tag_names,
        "is_archived": bookmark.is_archived,
        "unread": bookmark.unread,
        "shared": bookmark.shared,
        "is_editable": is_owner,
        "existing_bookmark_id": existing_bookmark_id,
        "date_added": bookmark.date_added.isoformat() if bookmark.date_added else None,
        "date_modified": bookmark.date_modified.isoformat() if bookmark.date_modified else None,
        "snapshot_exists": bookmark.latest_snapshot is not None,
        "snapshot_id": bookmark.latest_snapshot.id if bookmark.latest_snapshot else None,
        "latest_article": bookmark.latest_article_id,
    }
    api_base_url = reverse("linkding:api-root").rstrip("/")

    # Try to load from stored article first
    if bookmark.latest_article:
        asset = bookmark.latest_article
        if asset.status == BookmarkAsset.STATUS_COMPLETE:
            # Content loaded asynchronously by client from /assets/<id>/
            return render(
                request,
                "bookmarks/reader/read.html",
                {
                    "bookmark_id": bookmark_id,
                    "asset_id": asset.id,
                    "bookmark_data": bookmark_data,
                    "from_param": request.GET.get("from", ""),
                    "api_base_url": api_base_url,
                    "assets_base_url": reverse(
                        "linkding:assets.view", args=[0]
                    ).rsplit("/0", 1)[0],
                    "bookmarks_index_url": reverse("linkding:bookmarks.index"),
                },
            )
        elif asset.status == BookmarkAsset.STATUS_PENDING:
            # Article is being processed — show loading page
            return render(
                request,
                "bookmarks/reader/read_pending.html",
                {
                    "bookmark_id": bookmark_id,
                    "asset_id": asset.id,
                    "api_base_url": api_base_url,
                },
            )
        elif asset.status == BookmarkAsset.STATUS_FAILURE:
            # Previous attempt failed — retry with a new asset
            remove_article(asset)
            new_asset = tasks.create_article(bookmark)
            return render(
                request,
                "bookmarks/reader/read_pending.html",
                {
                    "bookmark_id": bookmark_id,
                    "asset_id": new_asset.id,
                    "api_base_url": api_base_url,
                },
            )

    # No article yet — create one via huey task
    asset = tasks.create_article(bookmark)
    return render(
        request,
        "bookmarks/reader/read_pending.html",
        {
            "bookmark_id": bookmark_id,
            "asset_id": asset.id,
            "api_base_url": api_base_url,
        },
    )


@login_required
def reparse(request: HttpRequest, bookmark_id: int):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    bookmark = access.bookmark_write(request, bookmark_id)

    try:
        content = website_loader.load_full_page(bookmark.url)
    except Exception as e:
        content = f"<html><body><p>{_('Unable to load page content: %(error)s') % {'error': str(e)}}</p></body></html>"

    from bookmarks.services.articles import create_article

    create_article(bookmark, content, title=bookmark.resolved_title)

    return HttpResponseRedirect(reverse("linkding:bookmarks.read", args=[bookmark_id]))


@login_required
def export(request: HttpRequest, bookmark_id: int):
    """Export article with inline annotations as HTML."""
    from django.http import HttpResponse

    from bookmarks.models import Annotation
    from bookmarks.services.articles import get_article_content

    bookmark = access.bookmark_read(request, bookmark_id)
    format_type = request.GET.get("format", "html")

    if not bookmark.latest_article:
        return HttpResponseBadRequest("No article to export")

    content = get_article_content(bookmark.latest_article)
    annotations = Annotation.objects.filter(
        bookmark=bookmark, article_asset=bookmark.latest_article
    )

    if format_type == "html":
        # Inject highlights as <mark> tags into the HTML
        for ann in annotations.order_by("-selector__start"):
            try:
                exact = ann.selector.get("exact", ann.selected_text)
                color_style = {
                    "yellow": "background-color: rgba(255,235,0,0.35)",
                    "green": "background-color: rgba(0,200,83,0.3)",
                    "blue": "background-color: rgba(66,165,245,0.3)",
                    "pink": "background-color: rgba(236,64,122,0.3)",
                }.get(ann.color, "background-color: rgba(255,235,0,0.35)")

                # Simple text replacement for export
                escaped_exact = html.escape(exact)
                mark_tag = f'<mark style="{color_style}" data-annotation-id="{ann.id}" title="{html.escape(ann.note_content or "")}">{escaped_exact}</mark>'
                # Find and replace the first occurrence that isn't already wrapped
                idx = content.find(exact)
                if idx != -1:
                    content = content[:idx] + mark_tag + content[idx + len(exact) :]
            except Exception:
                continue

        response = HttpResponse(content, content_type="text/html")
        response["Content-Disposition"] = (
            f'attachment; filename="{bookmark.resolved_title}.html"'
        )
        return response

    elif format_type == "markdown":
        # Simple markdown: article text + annotations as footnotes
        from django.utils.html import strip_tags

        text = strip_tags(content)
        md = f"# {bookmark.resolved_title}\n\n{text}\n\n"
        if annotations.exists():
            md += "## Highlights\n\n"
            for ann in annotations:
                md += f"> {ann.selected_text}\n"
                if ann.note_content:
                    md += f"  \n  Note: {ann.note_content}\n"
                md += "\n"

        response = HttpResponse(md, content_type="text/markdown")
        response["Content-Disposition"] = (
            f'attachment; filename="{bookmark.resolved_title}.md"'
        )
        return response

    else:
        return HttpResponseBadRequest(f"Unknown format: {format_type}")
