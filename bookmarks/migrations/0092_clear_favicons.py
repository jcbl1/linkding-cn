# This migration previously cleared all cached favicon files.
# It has been changed to a no-op because:
# 1. It was already applied (2026-07-23) and won't run again in production.
# 2. Deleting real files from a data migration is wrong — it wiped the
#    production data/favicons directory every time tests ran.
# 3. The favicon refresh pipeline handles stale/missing files automatically.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("bookmarks", "0091_add_bookmark_toolbar_auto_hide"),
    ]

    operations = [
        # Was: migrations.RunPython(clear_favicon_files, migrations.RunPython.noop)
        # Now: no-op. The migration record remains so Django doesn't complain.
    ]
