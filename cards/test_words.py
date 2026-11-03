"""Word-management behavior, persistence, and protection of private content."""
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Word
from .security import pin_version
from .tests import TEST_HASH


@override_settings(APP_PIN_HASH=TEST_HASH)
class WordManagementTests(TestCase):
    def setUp(self):
        session = self.client.session
        session["pin_version"] = pin_version()
        session.save()

    def test_add_chunk_only_creates_new_word_due_immediately(self):
        before = timezone.now()
        response = self.client.post(reverse("word-add"), {"chunk": "  take your time  "})
        self.assertRedirects(response, reverse("home"))
        word = Word.objects.get()
        self.assertEqual(word.chunk, "take your time")
        self.assertEqual((word.meaning, word.example, word.stage), ("", "", 0))
        self.assertIsNone(word.archived_at)
        self.assertGreaterEqual(word.due_at, before)
        self.assertLessEqual(word.due_at, timezone.now())
        self.assertContains(self.client.get(reverse("home")), "take your time")

    def test_add_all_fields_persists_multiline_and_unicode_content(self):
        values = {"chunk": "keep in mind", "meaning": "помнить\nremember", "example": "Keep this in mind.\nIt matters."}
        self.client.post(reverse("word-add"), values)
        word = Word.objects.get()
        for field, value in values.items():
            self.assertEqual(getattr(word, field), value)
        reopened = Client()
        reopened.cookies = self.client.cookies.copy()
        self.assertContains(reopened.get(reverse("home")), "Keep this in mind.")

    def test_missing_or_whitespace_chunk_is_rejected_without_losing_other_fields(self):
        for chunk in ["", "   \n\t"]:
            response = self.client.post(reverse("word-add"), {"chunk": chunk, "meaning": "keep this draft"})
            self.assertEqual(response.status_code, 200)
            self.assertFormError(response.context["form"], "chunk", "This field is required.")
            self.assertContains(response, "keep this draft")
        self.assertFalse(Word.objects.exists())

    def test_content_limits_are_validated_server_side(self):
        for field, limit in [("chunk", 300), ("meaning", 2000), ("example", 4000)]:
            with self.subTest(field=field):
                values = {"chunk": "valid chunk", field: "a" * (limit + 1)}
                response = self.client.post(reverse("word-add"), values)
                self.assertIn(field, response.context["form"].errors)
        self.assertFalse(Word.objects.exists())

    def test_submitted_learning_state_cannot_override_new_word_defaults(self):
        self.client.post(reverse("word-add"), {
            "chunk": "hello", "stage": "9", "archived_at": "2025-01-01", "due_at": "2030-01-01",
        })
        word = Word.objects.get()
        self.assertEqual(word.stage, 0)
        self.assertIsNone(word.archived_at)
        self.assertLessEqual(word.due_at, timezone.now())

    def test_home_shows_only_active_words_and_correct_due_count(self):
        Word.objects.create(chunk="due chunk")
        Word.objects.create(chunk="future chunk", due_at=timezone.now() + timedelta(days=1))
        Word.objects.create(chunk="archived chunk", archived_at=timezone.now())
        response = self.client.get(reverse("home"))
        self.assertContains(response, "due chunk")
        self.assertContains(response, "future chunk")
        self.assertNotContains(response, "archived chunk")
        self.assertEqual(response.context["word_count"], 2)
        self.assertEqual(response.context["due_count"], 1)
        self.assertContains(response, 'href="/words/add/"')

    def test_user_content_is_escaped_in_lists_forms_and_confirmation(self):
        unsafe = '<script>alert("private")</script>'
        word = Word.objects.create(chunk=unsafe, meaning=unsafe, example=unsafe)
        for url in [reverse("home"), reverse("word-edit", args=[word.pk]), reverse("word-delete", args=[word.pk])]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, "&lt;script&gt;")
                self.assertNotContains(response, "<script>")

    def test_edit_preserves_learning_state_and_can_clear_optional_fields(self):
        due_at = timezone.now() + timedelta(days=7)
        word = Word.objects.create(chunk="before", meaning="old meaning", example="old example", stage=3, due_at=due_at)
        response = self.client.post(reverse("word-edit", args=[word.pk]), {
            "chunk": "after", "meaning": "", "example": "", "stage": "0", "due_at": "2030-01-01",
        })
        self.assertRedirects(response, reverse("home"))
        word.refresh_from_db()
        self.assertEqual((word.chunk, word.meaning, word.example), ("after", "", ""))
        self.assertEqual(word.stage, 3)
        self.assertEqual(word.due_at, due_at)

    def test_invalid_edit_leaves_stored_content_unchanged(self):
        word = Word.objects.create(chunk="original", meaning="original meaning")
        response = self.client.post(reverse("word-edit", args=[word.pk]), {"chunk": "", "meaning": "changed"})
        self.assertEqual(response.status_code, 200)
        word.refresh_from_db()
        self.assertEqual((word.chunk, word.meaning), ("original", "original meaning"))

    def test_delete_get_only_confirms_and_post_deletes_only_chosen_word(self):
        chosen = Word.objects.create(chunk="chosen")
        other = Word.objects.create(chunk="keep me")
        url = reverse("word-delete", args=[chosen.pk])
        self.assertContains(self.client.get(url), "Delete this word?")
        self.assertTrue(Word.objects.filter(pk=chosen.pk).exists())
        self.assertRedirects(self.client.post(url), reverse("home"))
        self.assertFalse(Word.objects.filter(pk=chosen.pk).exists())
        self.assertTrue(Word.objects.filter(pk=other.pk).exists())

    def test_archive_shows_only_archived_words_with_restore_action(self):
        Word.objects.create(chunk="still learning")
        word = Word.objects.create(chunk="learned chunk", stage=5, archived_at=timezone.now())
        response = self.client.get(reverse("archive"))
        self.assertContains(response, "learned chunk")
        self.assertNotContains(response, "still learning")
        self.assertContains(response, reverse("word-restore", args=[word.pk]))
        self.assertEqual(response.context["word_count"], 1)
        self.assertIn("no-store", response["Cache-Control"])

    def test_restore_requires_post_and_resets_to_first_stage_due_now(self):
        word = Word.objects.create(chunk="remember", stage=5, archived_at=timezone.now(), due_at=timezone.now() + timedelta(days=14))
        url = reverse("word-restore", args=[word.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        word.refresh_from_db()
        self.assertIsNotNone(word.archived_at)
        before = timezone.now()
        self.assertRedirects(self.client.post(url), reverse("home"))
        word.refresh_from_db()
        self.assertIsNone(word.archived_at)
        self.assertEqual(word.stage, 0)
        self.assertGreaterEqual(word.due_at, before)
        self.assertLessEqual(word.due_at, timezone.now())

    def test_stale_restore_does_not_reset_an_active_words_progress(self):
        due_at = timezone.now() + timedelta(days=3)
        word = Word.objects.create(chunk="active word", stage=2, due_at=due_at)
        self.assertRedirects(self.client.post(reverse("word-restore", args=[word.pk])), reverse("home"))
        word.refresh_from_db()
        self.assertEqual(word.stage, 2)
        self.assertEqual(word.due_at, due_at)

    def test_archived_words_can_be_edited_or_deleted_without_restoring(self):
        archived_at = timezone.now()
        word = Word.objects.create(chunk="learned", stage=5, archived_at=archived_at)
        self.assertRedirects(self.client.post(reverse("word-edit", args=[word.pk]), {"chunk": "learned phrase"}), reverse("archive"))
        word.refresh_from_db()
        self.assertEqual(word.archived_at, archived_at)
        self.assertEqual(word.stage, 5)
        self.assertRedirects(self.client.post(reverse("word-delete", args=[word.pk])), reverse("archive"))

    def test_every_word_route_requires_authentication_and_cannot_mutate_anonymously(self):
        word = Word.objects.create(chunk="private word", stage=5, archived_at=timezone.now())
        browser = Client()
        urls = [reverse("home"), reverse("archive"), reverse("word-add")]
        urls += [reverse(name, args=[word.pk]) for name in ["word-edit", "word-delete", "word-restore"]]
        for url in urls:
            for method in [browser.get, browser.post]:
                with self.subTest(url=url, method=method.__name__):
                    response = method(url, {"chunk": "intrusion"})
                    self.assertRedirects(response, reverse("login"))
                    self.assertNotContains(response, "private word", status_code=302)
        word.refresh_from_db()
        self.assertEqual(word.chunk, "private word")
        self.assertIsNotNone(word.archived_at)
        self.assertEqual(Word.objects.count(), 1)

    def test_mutating_routes_enforce_csrf(self):
        browser = Client(enforce_csrf_checks=True)
        browser.cookies = self.client.cookies.copy()
        word = Word.objects.create(chunk="private word", archived_at=timezone.now())
        urls = [reverse("word-add")]
        urls += [reverse(name, args=[word.pk]) for name in ["word-edit", "word-delete", "word-restore"]]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(browser.post(url, {"chunk": "intrusion"}).status_code, 403)
        self.assertEqual(Word.objects.count(), 1)
        word.refresh_from_db()
        self.assertEqual(word.chunk, "private word")
        self.assertIsNotNone(word.archived_at)

    def test_missing_words_return_not_found(self):
        for name in ["word-edit", "word-delete", "word-restore"]:
            with self.subTest(name=name):
                url = reverse(name, args=[99999])
                self.assertEqual(self.client.post(url).status_code, 404)

    def test_empty_archive_and_form_pages_render(self):
        self.assertContains(self.client.get(reverse("archive")), "Room for your progress")
        response = self.client.get(reverse("word-add"))
        self.assertContains(response, "Chunk")
        self.assertContains(response, "Meaning")
        self.assertContains(response, "Example")
        self.assertIn("no-store", response["Cache-Control"])
