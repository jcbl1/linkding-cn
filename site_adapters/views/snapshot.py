"""
Snapshot preview.
"""
import os

from django.http import FileResponse, Http404, JsonResponse

from site_adapters.views.helpers import TEST_ASSETS_DIR, site_adapters_required


@site_adapters_required
def view_snapshot(request):
    """查看快照文件。"""
    filename = request.GET.get('file', '')
    if not filename:
        path = request.GET.get('path', '')
        filename = os.path.basename(path)
    if not filename or filename != os.path.basename(filename) or not filename.endswith('.html'):
        raise Http404
    base_dir = os.path.abspath(TEST_ASSETS_DIR)
    full_path = os.path.abspath(os.path.join(base_dir, filename))
    if os.path.commonpath([base_dir, full_path]) != base_dir:
        return JsonResponse({'error': 'invalid path'}, status=400)
    if not os.path.exists(full_path):
        raise Http404
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
