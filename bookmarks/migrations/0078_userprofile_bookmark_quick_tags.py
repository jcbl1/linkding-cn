from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookmarks", "0077_add_highlights_per_page"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="bookmark_quick_tags",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
