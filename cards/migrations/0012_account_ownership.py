import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


OWNER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
LOCAL_TEST_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


def require_maintenance_on_railway(apps, schema_editor):
    if settings.RAILWAY_ENVIRONMENT_ID and not settings.CARDY_MAINTENANCE_MODE:
        raise RuntimeError("Pause the existing Cardy deployment and verify its database backup before migrating.")


def assign_existing_data(apps, schema_editor):
    alias = schema_editor.connection.alias
    database = schema_editor.connection.settings_dict
    local_test = (settings.DEBUG and settings.LOCAL_TEST_LOGIN and not settings.RAILWAY_ENVIRONMENT_ID
                  and (database["ENGINE"] == "django.db.backends.sqlite3"
                       or database.get("HOST") in ("127.0.0.1", "localhost", "::1")))
    account_id = LOCAL_TEST_ID if local_test else OWNER_ID
    other_id = OWNER_ID if local_test else LOCAL_TEST_ID
    accounts = apps.get_model("cards", "Account").objects.using(alias)
    accounts.bulk_create([accounts.model(id=OWNER_ID), accounts.model(id=LOCAL_TEST_ID)])
    for name in ("Word", "StudySession", "WordImport"):
        # Historical models use the plain manager, including soft-deleted cards.
        apps.get_model("cards", name)._base_manager.using(alias).update(account_id=account_id)
    preferences = apps.get_model("cards", "LearningSettings").objects.using(alias)
    legacy, _ = preferences.get_or_create(pk=1, defaults={"account_id": account_id})
    if legacy.account_id is None:
        preferences.filter(pk=legacy.pk).update(account_id=account_id)
    preferences.create(pk=2, account_id=other_id)


class Migration(migrations.Migration):
    dependencies = [("cards", "0011_word_soft_delete")]

    operations = [
        migrations.RunPython(require_maintenance_on_railway, migrations.RunPython.noop),
        migrations.CreateModel(
            name="Account",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("google_subject", models.CharField(max_length=255, null=True, unique=True)),
            ],
        ),
        migrations.AddField(
            model_name="word", name="account",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to="cards.account"),
        ),
        migrations.AddField(
            model_name="wordimport", name="account",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to="cards.account"),
        ),
        migrations.AddField(
            model_name="studysession", name="account",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to="cards.account"),
        ),
        migrations.AddField(
            model_name="learningsettings", name="account",
            field=models.OneToOneField(null=True, on_delete=django.db.models.deletion.CASCADE, to="cards.account"),
        ),
        migrations.RunPython(assign_existing_data),
    ]
