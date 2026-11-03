from django.db import migrations, models


def create_login_guard(apps, schema_editor):
    apps.get_model("cards", "LoginGuard").objects.using(schema_editor.connection.alias).create(pk=1)


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="LoginGuard",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ("failed_attempts", models.PositiveSmallIntegerField(default=0)),
                ("blocked_until", models.DateTimeField(blank=True, null=True)),
            ],
        ),
        migrations.RunPython(create_login_guard, migrations.RunPython.noop),
    ]
