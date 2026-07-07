from django.db import migrations, models


def copy_collapse_to_show(apps, schema_editor):
    UserProfile = apps.get_model("bookmarks", "UserProfile")
    for profile in UserProfile.objects.all():
        profile.show_sidebar = not profile.collapse_side_panel
        profile.save(update_fields=["show_sidebar"])


class Migration(migrations.Migration):

    dependencies = [
        ("bookmarks", "0080_add_bookmark_date_route"),
    ]

    operations = [
        # 1. Add new fields (with defaults, safe for existing rows)
        migrations.AddField(
            model_name="userprofile",
            name="show_sidebar",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="show_highlights_sidebar",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="highlights_sidebar_modules",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="highlights_domain_view_mode",
            field=models.CharField(
                choices=[("full", "Full"), ("icon", "Icon")],
                default="icon",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="highlights_domain_compact_mode",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="highlights_tag_grouping",
            field=models.CharField(
                choices=[("alphabetical", "Alphabetical"), ("disabled", "Disabled")],
                default="alphabetical",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="highlights_sticky_header_controls",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="highlights_sticky_pagination",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="highlights_sticky_side_panel",
            field=models.BooleanField(default=True),
        ),
        # 2. Copy inverted data from collapse_side_panel to show_sidebar
        migrations.RunPython(copy_collapse_to_show, migrations.RunPython.noop),
        # 3. Remove old field
        migrations.RemoveField(
            model_name="userprofile",
            name="collapse_side_panel",
        ),
        # 4. Unrelated field alteration
        migrations.AlterField(
            model_name="userprofile",
            name="bookmark_date_route",
            field=models.CharField(
                choices=[
                    ("disabled", "Disabled"),
                    ("snapshot", "Latest snapshot"),
                    ("reader", "Reader mode"),
                    ("web_archive", "Internet Archive"),
                    ("highlights", "Highlights & Annotations"),
                ],
                default="snapshot",
                max_length=12,
            ),
        ),
    ]
