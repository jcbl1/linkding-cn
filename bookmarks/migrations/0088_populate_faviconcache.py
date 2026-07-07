# Data migration: populate FaviconCache from existing Bookmark.favicon_file

from django.db import migrations


def populate_favicon_cache(apps, schema_editor):
    Bookmark = apps.get_model("bookmarks", "Bookmark")
    FaviconCache = apps.get_model("bookmarks", "FaviconCache")
    from urllib.parse import urlparse

    domain_favicons = {}
    for bookmark in (
        Bookmark.objects.filter(is_deleted=False)
        .exclude(favicon_file="")
        .exclude(favicon_file="favicon.svg")
        .values("url", "favicon_file")
        .iterator()
    ):
        try:
            hostname = urlparse(bookmark["url"]).hostname
            if hostname:
                hostname = hostname.rstrip(".").lower()
                if hostname not in domain_favicons:
                    domain_favicons[hostname] = bookmark["favicon_file"]
        except Exception:
            continue

    if not domain_favicons:
        return

    FaviconCache.objects.bulk_create(
        [
            FaviconCache(
                domain=domain,
                favicon_file=favicon_file,
                status="success",
            )
            for domain, favicon_file in domain_favicons.items()
        ],
        ignore_conflicts=True,
    )


def reverse(apps, schema_editor):
    FaviconCache = apps.get_model("bookmarks", "FaviconCache")
    FaviconCache.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("bookmarks", "0087_favicon_cache"),
    ]

    operations = [
        migrations.RunPython(populate_favicon_cache, reverse),
    ]
