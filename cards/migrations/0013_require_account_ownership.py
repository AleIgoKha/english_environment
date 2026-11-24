import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Max


def sync_settings_sequence(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        model = apps.get_model("cards", "LearningSettings")
        maximum = model.objects.using(schema_editor.connection.alias).aggregate(value=Max("pk"))["value"]
        schema_editor.execute("SELECT setval(pg_get_serial_sequence(%s, 'id'), %s)",
                              [model._meta.db_table, maximum])


class Migration(migrations.Migration):
    dependencies = [("cards", "0012_account_ownership")]

    operations = [
        migrations.AlterField(
            model_name="word", name="account",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="cards.account"),
        ),
        migrations.AlterField(
            model_name="wordimport", name="account",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="cards.account"),
        ),
        migrations.AlterField(
            model_name="studysession", name="account",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="cards.account"),
        ),
        migrations.AlterField(
            model_name="learningsettings", name="account",
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to="cards.account"),
        ),
        migrations.AlterField(
            model_name="learningsettings", name="id",
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
        ),
        migrations.RunPython(sync_settings_sequence, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name="studysession", name="one_open_study_session"),
        migrations.AddConstraint(
            model_name="studysession",
            constraint=models.UniqueConstraint(
                fields=("account",), condition=models.Q(completed_at__isnull=True),
                name="one_open_study_session_per_account",
            ),
        ),
    ]
