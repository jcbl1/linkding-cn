import ipaddress
import logging
from urllib.parse import urljoin, urlparse

import requests
from django.conf import settings
from django.http import HttpResponseBadRequest, StreamingHttpResponse
from django.utils.translation import gettext as _

from site_adapters.views.helpers import site_adapters_required

logger = logging.getLogger(__name__)

_IMAGE_ACCEPT_HEADER = (
    "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
)
_IMAGE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
_REDIRECT_STATUS_CODES = (301, 302, 303, 307, 308)
_MAX_REDIRECTS = 5


def _validate_image_url(image_url: str) -> str:
    parsed = urlparse(image_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(_("Only HTTP(S) URLs are allowed"))

    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        # Hostname is a domain name; requests performs the DNS lookup.
        return image_url

    if address.is_private or address.is_loopback or address.is_link_local:
        raise ValueError(_("URL targets a private address"))

    return image_url


def _iter_image(response, max_size: int):
    downloaded = 0
    try:
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue

            previous = downloaded
            downloaded += len(chunk)
            if downloaded > max_size:
                remaining = max_size - previous
                if remaining > 0:
                    yield chunk[:remaining]
                break

            yield chunk
    finally:
        response.close()


def _fetch_image_response(image_url: str):
    headers = {
        "Accept": _IMAGE_ACCEPT_HEADER,
        "Accept-Encoding": "identity",
        "User-Agent": _IMAGE_USER_AGENT,
    }
    current_url = image_url

    for _redirect_index in range(_MAX_REDIRECTS + 1):
        current_url = _validate_image_url(current_url)
        response = requests.get(
            current_url,
            headers=headers,
            stream=True,
            allow_redirects=False,
            timeout=(5, 30),
        )

        if response.status_code in _REDIRECT_STATUS_CODES:
            location = response.headers.get("Location")
            if not location:
                response.close()
                return None
            response.close()
            current_url = urljoin(current_url, location)
            continue

        if response.status_code < 200 or response.status_code >= 300:
            response.close()
            return None
        return response

    return None


@site_adapters_required
def preview_image_proxy(request):
    image_url = request.GET.get("url")
    if not image_url:
        return HttpResponseBadRequest(_("URL parameter is missing"))

    try:
        response = _fetch_image_response(image_url)
        if response is None:
            return HttpResponseBadRequest(_("Failed to fetch image"))

        content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
        content_type = content_type.strip().lower()
        if not content_type.startswith("image/"):
            response.close()
            return HttpResponseBadRequest(_("URL does not point to an image"))

        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > settings.LD_PREVIEW_MAX_SIZE:
                    response.close()
                    return HttpResponseBadRequest(_("Image is too large"))
            except ValueError:
                response.close()
                return HttpResponseBadRequest(_("Invalid image size"))

        stream_response = StreamingHttpResponse(
            _iter_image(response, settings.LD_PREVIEW_MAX_SIZE),
            content_type=content_type,
        )
        stream_response["Cache-Control"] = "private, max-age=300"
        stream_response["X-Content-Type-Options"] = "nosniff"
        return stream_response
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    except requests.exceptions.RequestException as exc:
        logger.debug("Failed to proxy preview image: %s", image_url, exc_info=exc)
        return HttpResponseBadRequest(_("Failed to fetch image"))
