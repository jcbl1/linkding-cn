from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookmarks", "0095_merge_20260903"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userprofile",
            name="theme",
            field=models.CharField(
                choices=[
                    ("auto", "Auto"),
                    ("light", "Light"),
                    ("dark", "Dark"),
                    ("nord", "Nord"),
                    ("wireframe", "Wireframe"),
                ],
                default="auto",
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="userprofile",
            name="theme_light",
            field=models.CharField(
                choices=[
                    ("light", "Light"),
                    ("dark", "Dark"),
                    ("nord", "Nord"),
                    ("wireframe", "Wireframe"),
                ],
                default="light",
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="userprofile",
            name="theme_dark",
            field=models.CharField(
                choices=[
                    ("light", "Light"),
                    ("dark", "Dark"),
                    ("nord", "Nord"),
                    ("wireframe", "Wireframe"),
                ],
                default="dark",
                max_length=10,
            ),
        ),
    ]
