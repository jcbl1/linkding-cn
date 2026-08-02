import gzip
import re
from pathlib import Path

from django.conf import settings
from django.http import (
    Http404,
    HttpResponse,
)

from bookmarks.views import access


def _safe_content_disposition_filename(name: str) -> str:
    """Sanitize filename for Content-Disposition header to prevent injection."""
    name = name.replace('"', '_').replace('\n', '_').replace('\r', '_')
    return re.sub(r'[^\w\s.\-()\u4e00-\u9fff]', '_', name)



def _resolve_asset_file_path(asset):
    base_dir = Path(settings.LD_ASSET_FOLDER).resolve()
    candidate_path = Path(asset.file)

    # Prevent absolute-path reads and path traversal outside LD_ASSET_FOLDER.
    if candidate_path.is_absolute():
        raise Http404("Asset file does not exist")

    resolved_path = (base_dir / candidate_path).resolve()
    if not resolved_path.is_file() or base_dir not in resolved_path.parents:
        raise Http404("Asset file does not exist")

    return resolved_path


def _get_asset_content(asset):
    filepath = _resolve_asset_file_path(asset)

    if asset.gzip:
        with gzip.open(filepath, "rb") as f:
            content = f.read()
    else:
        with open(filepath, "rb") as f:
            content = f.read()

    return content


def view(request, asset_id: int):
    asset = access.asset_read(request, asset_id)
    content = _get_asset_content(asset)

    content_type = asset.content_type
    if "charset" not in content_type.lower() and content_type.startswith("text/"):
        content_type = f"{content_type}; charset=utf-8"

    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'inline; filename="{asset.download_name}"'
    if asset.content_type and asset.content_type.startswith("video/"):
        response["Content-Security-Policy"] = "default-src 'none'; media-src 'self';"
    elif asset.content_type == "application/pdf":
        response["Content-Security-Policy"] = "default-src 'none'; object-src 'self';"
    else:
        response["Content-Security-Policy"] = "sandbox allow-scripts"
    return response
