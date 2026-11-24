"""Rehearse the ownership migration with a populated pre-account PostgreSQL schema."""
from datetime import timedelta
from unittest import skipUnless
import uuid

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import learning
from .models import LOCAL_TEST_ACCOUNT_ID, OWNER_ACCOUNT_ID
from .test_accounts import google_sign_in
from .tests import GOOGLE_SETTINGS


@skipUnless(connection.vendor == "postgresql", "The migration rehearsal uses an isolated PostgreSQL schema")
@override_settings(**GOOGLE_SETTINGS)
class AccountMigrationTests(TransactionTestCase):
    def test_legacy_collection_settings_queue_undo_and_import_survive(self):
        schema = connection.ops.quote_name(f"cardy_legacy_{uuid.uuid4().hex}")
        with connection.cursor() as cursor:
            cursor.execute("SHOW search_path")
            previous_path = cursor.fetchone()[0]
            cursor.execute(f"CREATE SCHEMA {schema}")
            cursor.execute(f"SET search_path TO {schema}")
        try:
            self.rehearse_migration()
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('search_path', %s, false)", [previous_path])
                cursor.execute(f"DROP SCHEMA {schema} CASCADE")

    def rehearse_migration(self):
        legacy = [("cards", "0011_word_soft_delete"), ("sessions", "0001_initial")]
        executor = MigrationExecutor(connection)
        executor.migrate(legacy)
        apps = executor.loader.project_state(legacy).apps
        word = apps.get_model("cards", "Word")
        session = apps.get_model("cards", "StudySession")
        review = apps.get_model("cards", "StudyReview")
        preferences = apps.get_model("cards", "LearningSettings")
        word_import = apps.get_model("cards", "WordImport")
        now = timezone.now()
        queued = word.objects.create(chunk="queued", stage=2, due_at=now)
        learned = word.objects.create(chunk="learned", stage=7, archived_at=now, due_at=now + timedelta(days=3))
        word.objects.create(chunk="deleted", stage=3, deleted_at=now)
        unfinished = session.objects.create(
            queue=[queued.pk], initial_stages={str(queued.pk): 2, str(learned.pk): 6},
            setbacks={str(queued.pk): 1}, total_words=2, revealed=True,
        )
        review.objects.create(
            session=unfinished, word=learned, choice="know",
            word_before={"stage": 6, "due_at": now.isoformat(), "archived_at": None},
            word_after=learning.word_progress(learned),
            session_before={"queue": [learned.pk, queued.pk], "initial_stages": unfinished.initial_stages,
                            "setbacks": unfinished.setbacks},
        )
        preferences.objects.filter(pk=1).update(review_target=7)
        pending = word_import.objects.create(rows=[{"chunk": "pending", "meaning": "", "example": ""}])
        names = ("Word", "StudySession", "StudyReview", "LearningSettings", "WordImport")
        snapshots = {name: list(apps.get_model("cards", name).objects.order_by("pk").values()) for name in names}

        target = [("cards", "0013_require_account_ownership"), ("sessions", "0001_initial")]
        with override_settings(RAILWAY_ENVIRONMENT_ID="disposable-test"):
            with self.assertRaisesRegex(RuntimeError, "Pause the existing Cardy deployment"):
                MigrationExecutor(connection).migrate(target)
            with override_settings(CARDY_MAINTENANCE_MODE=True):
                executor = MigrationExecutor(connection)
                executor.migrate(target)
        apps = executor.loader.project_state(target).apps
        for name, before in snapshots.items():
            rows = apps.get_model("cards", name).objects
            if name != "StudyReview":
                rows = rows.filter(account_id=OWNER_ACCOUNT_ID)
            after = list(rows.order_by("pk").values(*before[0].keys()))
            self.assertEqual(after, before, name)
        self.assertEqual(apps.get_model("cards", "LearningSettings").objects.get(
            account_id=LOCAL_TEST_ACCOUNT_ID,
        ).review_target, 5)

        self.assertRedirects(google_sign_in(self.client, "owner", "owner@example.test"), reverse("home"))
        self.assertEqual(self.client.get(reverse("study")).context["word"].pk, queued.pk)
        self.assertRedirects(self.client.post(reverse("study-undo"), {
            "response_token": unfinished.response_token,
        }), reverse("study"))
        self.assertEqual(self.client.get(reverse("study")).context["word"].pk, learned.pk)
        preview_url = reverse("word-import-preview", args=[pending.pk])
        self.assertContains(self.client.get(preview_url), "pending")
        self.assertRedirects(self.client.post(preview_url), preview_url)

        with override_settings(CARDY_PUBLIC_SIGN_IN=True):
            self.client.post(reverse("logout"))
            self.assertRedirects(google_sign_in(self.client, "new", "new@example.test"), reverse("home"))
            self.assertEqual(self.client.get(reverse("home")).context["total_count"], 0)
