from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookmarks", "0079_add_bookmark_toolbar_modules"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="bookmark_date_route",
            field=models.CharField(
                choices=[
                    ("disabled", "Disabled"),
                    ("snapshot", "Latest snapshot"),
                    ("reader", "Reader mode"),
                    ("web_archive", "Internet Archive"),
                ],
                default="snapshot",
                max_length=12,
            ),
        ),
    ]
