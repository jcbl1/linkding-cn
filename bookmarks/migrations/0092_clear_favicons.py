# Data migration: clear all cached favicon files so they get re-fetched
# through the new pipeline (32px preferred, ICO single-frame extraction).

from pathlib import Path

from django.conf import settings
from django.db import migrations


def clear_favicon_files(apps, schema_editor):
    favicon_folder = Path(settings.LD_FAVICON_FOLDER)
    if not favicon_folder.exists():
        return

    removed = 0
    for f in favicon_folder.iterdir():
        if f.is_file() and f.name not in (".DS_Store",):
            f.unlink()
            removed += 1

    if removed:
        print(f"\n  Cleared {removed} cached favicon files")


class Migration(migrations.Migration):
    dependencies = [
        ("bookmarks", "0091_add_bookmark_toolbar_auto_hide"),
    ]

    operations = [
        migrations.RunPython(clear_favicon_files, migrations.RunPython.noop),
    ]
