"""Snapshot and reader preview for site adapter tests."""
import json
import os

from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse

from site_adapters.views.helpers import TEST_ASSETS_DIR, site_adapters_required


def _resolve_test_asset_path(filename: str) -> str | None:
    """Resolve a test asset filename, rejecting traversal outside TEST_ASSETS_DIR."""
    if not filename or filename != os.path.basename(filename) or not filename.endswith('.html'):
        raise Http404
    base_dir = os.path.abspath(TEST_ASSETS_DIR)
    full_path = os.path.abspath(os.path.join(base_dir, filename))
    if os.path.commonpath([base_dir, full_path]) != base_dir:
        return None
    if not os.path.exists(full_path):
        raise Http404
    return full_path


@site_adapters_required
def view_snapshot(request):
    """查看快照文件。"""
    filename = request.GET.get('file', '')
    if not filename:
        path = request.GET.get('path', '')
        filename = os.path.basename(path)
    full_path = _resolve_test_asset_path(filename)
    if full_path is None:
        return JsonResponse({'error': 'invalid path'}, status=400)
    # FileResponse handles closing the file handle when the response is finalized.
    f = open(full_path, 'rb')
    try:
        response = FileResponse(f, content_type='text/html; charset=utf-8')
    except Exception:
        f.close()
        raise
    response['Content-Security-Policy'] = 'sandbox'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@site_adapters_required
def view_reader(request):
    """Render a reader test asset with the same reader UI as a bookmark read page."""
    filename = request.GET.get('file', '')
    full_path = _resolve_test_asset_path(filename)
    if full_path is None:
        raise Http404
    with open(full_path, encoding='utf-8') as f:
        article_html = f.read()

    title = ''
    word_count = 0
    original_url = ''
    snapshot_url = ''
    metadata_path = os.path.splitext(full_path)[0] + '.json'
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, encoding='utf-8') as f:
                metadata = json.load(f)
            title = metadata.get('title', '')
            word_count = metadata.get('word_count', 0)
            original_url = metadata.get('original_url', '')
            snapshot_url = metadata.get('snapshot_url', '')
        except (OSError, ValueError, TypeError):
            pass

    return render(request, 'bookmarks/reader/read.html', {
        'reader_preview_data': {
            'title': title,
            'word_count': word_count,
            'content': article_html,
            'original_url': original_url,
            'snapshot_url': snapshot_url,
        },
        'bookmark_data': {},
        'bookmark_id': 0,
        'asset_id': 0,
        'from_param': '',
        'api_base_url': reverse('linkding:api-root').rstrip('/'),
        'assets_base_url': reverse('linkding:assets.view', args=[0]).rsplit('/0', 1)[0],
        'bookmarks_index_url': reverse('linkding:bookmarks.index'),
    })
