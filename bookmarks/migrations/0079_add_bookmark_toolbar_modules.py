from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookmarks", "0078_userprofile_bookmark_quick_tags"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="bookmark_toolbar_modules",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
