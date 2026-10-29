from django.conf import settings
from django.http import JsonResponse


def manifest(request):
    response = {
        "short_name": "linkding",
        "name": "linkding",
        "description": "Self-hosted bookmark service",
        "start_url": "bookmarks",
        "display": "standalone",
        "scope": "/" + settings.LD_CONTEXT_PATH,
        "theme_color": _get_theme_color(request.user_profile.theme),
        "background_color": _get_background_color(request.user_profile.theme),
        "icons": [
            {
                "src": "/" + settings.LD_CONTEXT_PATH + "static/logo.svg",
                "type": "image/svg+xml",
                "sizes": "512x512",
                "purpose": "any",
            },
            {
                "src": "/" + settings.LD_CONTEXT_PATH + "static/logo-512.png",
                "type": "image/png",
                "sizes": "512x512",
                "purpose": "any",
            },
            {
                "src": "/" + settings.LD_CONTEXT_PATH + "static/logo-192.png",
                "type": "image/png",
                "sizes": "192x192",
                "purpose": "any",
            },
            {
                "src": "/" + settings.LD_CONTEXT_PATH + "static/maskable-logo.svg",
                "type": "image/svg+xml",
                "sizes": "512x512",
                "purpose": "maskable",
            },
            {
                "src": "/" + settings.LD_CONTEXT_PATH + "static/maskable-logo-512.png",
                "type": "image/png",
                "sizes": "512x512",
                "purpose": "maskable",
            },
            {
                "src": "/" + settings.LD_CONTEXT_PATH + "static/maskable-logo-192.png",
                "type": "image/png",
                "sizes": "192x192",
                "purpose": "maskable",
            },
        ],
        "shortcuts": [
            {
                "name": "Add bookmark",
                "url": "/" + settings.LD_CONTEXT_PATH + "bookmarks/new",
            },
            {
                "name": "Archived",
                "url": "/" + settings.LD_CONTEXT_PATH + "bookmarks/archived",
            },
            {
                "name": "Unread",
                "url": "/" + settings.LD_CONTEXT_PATH + "bookmarks?unread=yes",
            },
            {
                "name": "Untagged",
                "url": "/" + settings.LD_CONTEXT_PATH + "bookmarks?q=!untagged",
            },
            {
                "name": "Shared",
                "url": "/" + settings.LD_CONTEXT_PATH + "bookmarks/shared",
            },
        ],
        "screenshots": [
            {
                "src": "/"
                + settings.LD_CONTEXT_PATH
                + "static/linkding-screenshot.png",
                "type": "image/png",
                "sizes": "2158x1160",
                "form_factor": "wide",
            }
        ],
        "share_target": {
            "action": "/" + settings.LD_CONTEXT_PATH + "bookmarks/new",
            "method": "GET",
            "enctype": "application/x-www-form-urlencoded",
            "params": {
                "url": "url",
                "text": "url",
                "title": "title",
            },
        },
    }

    return JsonResponse(response, status=200)


def _get_theme_color(theme: str) -> str:
    colors = {
        "dark": "#161822",
        "nord": "#2E3440",
        "wireframe": "#404040",
    }
    return colors.get(theme, "#5856e0")


def _get_background_color(theme: str) -> str:
    colors = {
        "dark": "#161822",
        "nord": "#2E3440",
        "wireframe": "#F2F2F2",
    }
    return colors.get(theme, "#ffffff")
