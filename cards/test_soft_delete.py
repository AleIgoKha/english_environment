"""Deleted cards stay recoverable and continue to block duplicate imports."""
from datetime import timedelta
import json
import uuid

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import importing, learning
from .models import Word, WordImport
from .security import auth_version
from .test_mcp import TEST_TOKEN, TOKEN_HASH
from .test_mcp_write import WRITE_TOKEN, WRITE_HASH
from .tests import GOOGLE_SETTINGS


@override_settings(**GOOGLE_SETTINGS, CARDY_MCP_TOKEN_SHA256=TOKEN_HASH,
                   CARDY_MCP_WRITE_TOKEN_SHA256=WRITE_HASH)
class SoftDeleteTests(TestCase):
    def setUp(self):
        session = self.client.session
        session.update({"auth_kind": "google", "google_email": "owner@example.test",
                        "google_subject": "owner", "auth_version": auth_version()})
        session.save()

    def rpc(self, name, arguments, token=TEST_TOKEN):
        return self.client.post(reverse("mcp"), json.dumps({"jsonrpc": "2.0", "id": 1,
            "method": "tools/call", "params": {"name": name, "arguments": arguments}}),
            content_type="application/json", HTTP_AUTHORIZATION="Bearer " + token,
            HTTP_ACCEPT="application/json, text/event-stream")

    def test_single_delete_retains_card_and_invalidates_current_learning_form(self):
        word = Word.objects.create(chunk="keep this history", meaning="reverse", example="extra", stage=2,
                                   show_reverse_immediately=True)
        following = Word.objects.create(chunk="next")
        session = learning.start_or_resume()
        learning.reveal(session.response_token)
        old_token = session.response_token
        due_at = word.due_at
        response = self.client.post(reverse("word-delete", args=[word.pk]), {"return_to": "study"})
        self.assertRedirects(response, reverse("study"))
        self.assertFalse(Word.objects.filter(pk=word.pk).exists())
        retained = Word.all_objects.get(pk=word.pk)
        self.assertIsNotNone(retained.deleted_at)
        self.assertEqual((retained.chunk, retained.meaning, retained.example, retained.stage, retained.due_at,
                          retained.show_reverse_immediately),
                         ("keep this history", "reverse", "extra", 2, due_at, True))
        session.refresh_from_db()
        self.assertEqual(session.queue, [following.pk])
        self.assertNotEqual(session.response_token, old_token)
        learning.answer(old_token, "know")
        following.refresh_from_db()
        self.assertEqual(following.stage, 0)

    def test_bulk_delete_and_repeated_model_deletion_preserve_rows(self):
        words = [Word.objects.create(chunk=f"selected {index}") for index in range(2)]
        keep = Word.objects.create(chunk="keep visible")
        session = learning.start_or_resume()
        response = self.client.post(reverse("word-bulk"), {
            "action": "delete", "word_ids": [word.pk for word in words], "confirmed": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(Word.objects.values_list("pk", flat=True)), [keep.pk])
        self.assertEqual(Word.all_objects.count(), 3)
        self.assertEqual(Word.all_objects.filter(deleted_at__isnull=False).count(), 2)
        session.refresh_from_db()
        self.assertEqual(session.queue, [keep.pk])
        retained = Word.all_objects.get(pk=words[0].pk)
        deleted_at = retained.deleted_at
        self.assertEqual(retained.delete()[0], 0)
        retained.refresh_from_db()
        self.assertEqual(retained.deleted_at, deleted_at)

    def test_deleted_cards_are_excluded_from_learning_counts_and_normal_mutations(self):
        deleted = Word.objects.create(chunk="deleted private phrase", stage=5, archived_at=timezone.now())
        deleted.delete()
        Word.objects.create(chunk="visible")
        for progress in ("to-learn", "practiced", "learned"):
            page = self.client.get(reverse("home"), {"progress": progress})
            self.assertNotContains(page, "deleted private phrase")
            self.assertEqual(page.context["total_count"], 2)
            self.assertEqual(page.context["learned_count"], 0)
        for route in ("word-reset", "word-restore"):
            response = self.client.post(reverse(route, args=[deleted.pk]), {"chunk": "changed"})
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.post(reverse("word-delete", args=[deleted.pk])).status_code, 400)
        page = self.client.get(reverse("home"), {"progress": "deleted", "q": "private phrase"})
        self.assertContains(page, "deleted private phrase")
        self.assertContains(page, reverse("word-undelete", args=[deleted.pk]))
        self.assertContains(page, 'id="card-selection"')

    def test_all_cards_list_places_deleted_cards_below_every_other_status(self):
        active = Word.objects.create(chunk="active")
        learned = Word.objects.create(chunk="learned", archived_at=timezone.now())
        deleted = Word.objects.create(chunk="newest but deleted")
        deleted.delete()
        page = self.client.get(reverse("home"), {"progress": "all"})
        self.assertEqual(list(page.context["words"].values_list("pk", flat=True)), [learned.pk, active.pk, deleted.pk])
        self.assertEqual(page.context["total_count"], 3)
        self.assertEqual(page.context["learned_count"], 1)
        self.assertContains(page, '<option value="deleted"')
        self.assertContains(page, f'formaction="{reverse("word-undelete", args=[deleted.pk])}"')
        self.assertContains(page, f'aria-label="Select {deleted.chunk}"')
        searched = self.client.get(reverse("home"), {"progress": "all", "q": "deleted"})
        self.assertEqual(list(searched.context["words"].values_list("pk", flat=True)), [deleted.pk])

    def test_restore_resets_status_and_requires_owner_csrf_and_post(self):
        archived_at = timezone.now()
        due_at = archived_at + timedelta(days=14)
        word = Word.objects.create(chunk="restore original", stage=5, archived_at=archived_at, due_at=due_at)
        word.delete()
        url = reverse("word-undelete", args=[word.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(Client().post(url).status_code, 302)
        protected = Client(enforce_csrf_checks=True)
        protected.cookies = self.client.cookies.copy()
        self.assertEqual(protected.post(url).status_code, 403)
        self.assertRedirects(self.client.post(url), reverse("home") + "?progress=deleted")
        restored = Word.objects.get(pk=word.pk)
        self.assertEqual((restored.stage, restored.archived_at), (0, None))
        self.assertLessEqual(restored.due_at, timezone.now())
        self.assertNotEqual(restored.due_at, due_at)
        self.assertIsNone(restored.deleted_at)
        Word.objects.filter(pk=word.pk).update(stage=2, due_at=due_at)
        self.assertRedirects(self.client.post(url), reverse("home") + "?progress=deleted")
        restored.refresh_from_db()
        self.assertEqual((restored.stage, restored.due_at), (2, due_at))
        self.assertEqual(Word.all_objects.count(), 1)

    def test_deleted_card_has_ellipsis_menu_with_restore_and_delete(self):
        word = Word.objects.create(chunk="deleted menu")
        word.delete()
        page = self.client.get(reverse("home"), {"progress": "deleted"})
        self.assertContains(page, 'class="word-actions"')
        self.assertContains(page, 'aria-label="Actions for deleted menu"')
        self.assertContains(page, '>Restore</button>')
        self.assertContains(page, f'href="{reverse("word-edit", args=[word.pk])}">Edit</a>')
        self.assertContains(page, f'class="select-word" data-word-id="{word.pk}"')
        self.assertContains(page, f'href="{reverse("word-delete", args=[word.pk])}">Delete</a>')

    def test_edit_deleted_card_changes_text_without_restoring_or_resetting(self):
        word = Word.objects.create(chunk="deleted original", meaning="original reverse", stage=5,
                                   archived_at=timezone.now(), due_at=timezone.now() + timedelta(days=14))
        word.delete()
        before = (word.deleted_at, word.archived_at, word.stage, word.due_at)
        url = reverse("word-edit", args=[word.pk])
        self.assertContains(self.client.get(url), 'href="/?progress=deleted"')
        response = self.client.post(url, {"chunk": "edited while deleted", "meaning": "updated reverse",
                                         "deleted_at": "", "stage": "0", "archived_at": ""})
        self.assertRedirects(response, reverse("home") + "?progress=deleted")
        word.refresh_from_db()
        self.assertEqual((word.chunk, word.meaning), ("edited while deleted", "updated reverse"))
        self.assertEqual((word.deleted_at, word.archived_at, word.stage, word.due_at), before)
        self.assertFalse(Word.objects.filter(pk=word.pk).exists())

    def test_bulk_restore_resets_only_selected_deleted_cards(self):
        first = Word.objects.create(chunk="restore first", stage=3)
        second = Word.objects.create(chunk="restore second", stage=5, archived_at=timezone.now())
        keep = Word.objects.create(chunk="keep unchanged", stage=4)
        Word.objects.filter(pk__in=[first.pk, second.pk]).delete()
        data = {"action": "reset", "word_ids": [first.pk, second.pk]}
        preview = self.client.post(reverse("word-bulk"), data)
        self.assertContains(preview, "Restore 2 cards?")
        self.assertEqual(set(json.loads(preview.context["deleted_ids_json"])), {first.pk, second.pk})
        self.assertEqual(Word.objects.count(), 1)
        response = self.client.post(reverse("word-bulk"), {**data, "confirmed": "yes",
                                                           "deleted_ids": json.dumps([first.pk, second.pk])})
        self.assertEqual(response.status_code, 302)
        for word in (first, second):
            word.refresh_from_db()
            self.assertEqual(word.stage, 0)
            self.assertIsNone(word.deleted_at)
            self.assertIsNone(word.archived_at)
            self.assertLessEqual(word.due_at, timezone.now())
        keep.refresh_from_db()
        self.assertEqual(keep.stage, 4)

    def test_bulk_delete_confirms_permanent_targets_and_prevents_replay_escalation(self):
        active = Word.objects.create(chunk="soft delete selected")
        deleted = Word.objects.create(chunk="permanently delete selected")
        keep = Word.objects.create(chunk="keep me")
        deleted.delete()
        data = {"action": "delete", "word_ids": [active.pk, deleted.pk]}
        preview = self.client.post(reverse("word-bulk"), data)
        self.assertContains(preview, "permanently removed")
        self.assertContains(preview, "will move to Deleted")
        self.assertEqual(self.client.post(reverse("word-bulk"), {**data, "confirmed": "yes"}).status_code, 400)
        self.assertEqual(Word.all_objects.count(), 3)
        confirmed = {**data, "confirmed": "yes", "deleted_ids": json.dumps([deleted.pk])}
        self.assertEqual(self.client.post(reverse("word-bulk"), confirmed).status_code, 302)
        self.assertFalse(Word.all_objects.filter(pk=deleted.pk).exists())
        self.assertTrue(Word.all_objects.filter(pk=active.pk, deleted_at__isnull=False).exists())
        self.assertTrue(Word.objects.filter(pk=keep.pk).exists())
        self.assertEqual(self.client.post(reverse("word-bulk"), confirmed).status_code, 400)
        self.assertTrue(Word.all_objects.filter(pk=active.pk).exists())

    def test_bulk_confirmation_rejects_a_changed_deleted_status(self):
        word = Word.objects.create(chunk="changed after preview", stage=2)
        data = {"action": "reset", "word_ids": [word.pk]}
        self.assertEqual(self.client.post(reverse("word-bulk"), data).status_code, 200)
        word.delete()
        self.assertEqual(self.client.post(reverse("word-bulk"), {**data, "confirmed": "yes"}).status_code, 400)
        word.refresh_from_db()
        self.assertIsNotNone(word.deleted_at)
        self.assertEqual(word.stage, 2)

    def test_restore_of_five_hundred_selected_cards_stays_within_form_field_limits(self):
        Word.objects.bulk_create([Word(chunk=f"bulk deleted {index}") for index in range(500)])
        ids = list(Word.objects.values_list("pk", flat=True))
        Word.objects.all().delete()
        data = {"action": "reset", "word_ids": ids}
        preview = self.client.post(reverse("word-bulk"), data)
        self.assertContains(preview, "Restore 500 cards?")
        self.assertEqual(preview.content.decode().count('name="deleted_ids"'), 1)
        response = self.client.post(reverse("word-bulk"), {
            **data, "confirmed": "yes", "deleted_ids": json.dumps(ids)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Word.objects.count(), 500)

    def test_permanent_delete_requires_separate_confirmation_and_removes_duplicate_match(self):
        word = Word.objects.create(chunk="forget permanently")
        session = learning.start_or_resume()
        url = reverse("word-delete", args=[word.pk])
        self.client.post(url, {"return_to": "study"})
        self.assertTrue(Word.all_objects.filter(pk=word.pk).exists())
        # Retrying the first deletion must never become permanent deletion.
        self.assertEqual(self.client.post(url, {"return_to": "study"}).status_code, 400)
        confirmation = self.client.get(url)
        self.assertContains(confirmation, "Permanently delete this word?")
        self.assertContains(confirmation, 'name="permanent" value="yes"')
        self.assertTrue(Word.all_objects.filter(pk=word.pk).exists())
        self.assertEqual(Client().post(url, {"permanent": "yes"}).status_code, 302)
        client = Client(enforce_csrf_checks=True)
        client.cookies = self.client.cookies.copy()
        self.assertEqual(client.post(url, {"permanent": "yes"}).status_code, 403)
        self.assertRedirects(self.client.post(url, {"permanent": "yes"}), reverse("home") + "?progress=deleted")
        self.assertFalse(Word.all_objects.filter(pk=word.pk).exists())
        session.refresh_from_db()
        learning.undo(session.response_token)
        self.assertFalse(Word.all_objects.filter(pk=word.pk).exists())
        result = self.rpc("check_existing_cards", {"phrases": ["forget permanently"]}).json()["result"]
        self.assertFalse(result["structuredContent"]["matches"][0]["exists"])

    def test_stale_permanent_delete_cannot_remove_restored_or_active_card(self):
        word = Word.objects.create(chunk="protect active card")
        url = reverse("word-delete", args=[word.pk])
        self.assertEqual(self.client.post(url, {"permanent": "yes"}).status_code, 400)
        self.assertTrue(Word.objects.filter(pk=word.pk).exists())
        word.delete()
        self.client.get(url)
        self.client.post(reverse("word-undelete", args=[word.pk]))
        self.assertEqual(self.client.post(url, {"permanent": "yes"}).status_code, 400)
        self.assertTrue(Word.objects.filter(pk=word.pk).exists())

    def test_mcp_read_finds_deleted_cards_and_write_skips_without_restoring_them(self):
        word = Word.objects.create(chunk="Take your time", meaning="retain original")
        word.delete()
        result = self.rpc("check_existing_cards", {"phrases": [" TAKE  YOUR TIME ", "new phrase"]}).json()["result"]
        self.assertEqual([match["exists"] for match in result["structuredContent"]["matches"]], [True, False])
        result = self.rpc("add_cards", {"batch_id": str(uuid.uuid4()), "approved": True, "cards": [
            {"obverse": "TAKE YOUR TIME", "reverse": "overwrite", "extra": ""},
            {"obverse": "new phrase", "reverse": "new", "extra": ""}]}, WRITE_TOKEN).json()["result"]
        self.assertEqual((result["structuredContent"]["added_count"], result["structuredContent"]["skipped_count"]), (1, 1))
        self.assertEqual(Word.objects.count(), 1)
        word.refresh_from_db()
        self.assertIsNotNone(word.deleted_at)
        self.assertEqual(word.meaning, "retain original")

    def test_manual_add_and_csv_skip_deleted_obverses(self):
        word = Word.objects.create(chunk="reserved phrase")
        word.delete()
        response = self.client.post(reverse("word-add"), {"chunk": " RESERVED  PHRASE "})
        self.assertContains(response, "A card with this Obverse already exists")
        batch = WordImport.objects.create(rows=[{"chunk": "Reserved phrase", "meaning": "replacement", "example": ""}])
        batch = importing.confirm_import(batch.pk)
        self.assertEqual((batch.added_count, batch.skipped_count), (0, 1))
        self.assertFalse(Word.objects.exists())
        self.assertEqual(Word.all_objects.count(), 1)

    def test_stale_undo_cannot_bring_a_deleted_card_back(self):
        word = Word.objects.create(chunk="deleted after answer")
        session = learning.start_or_resume()
        learning.reveal(session.response_token)
        learning.answer(session.response_token, "know")
        session.refresh_from_db()
        word.delete()
        learning.undo(session.response_token)
        self.assertFalse(Word.objects.exists())
        word.refresh_from_db()
        self.assertIsNotNone(word.deleted_at)
        session.refresh_from_db()
        self.assertEqual(session.queue, [])
