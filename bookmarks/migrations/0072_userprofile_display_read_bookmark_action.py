from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bookmarks", "0071_readingprogress"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="display_read_bookmark_action",
            field=models.BooleanField(default=True),
        ),
    ]
