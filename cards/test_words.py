"""Word-management behavior, persistence, and protection of private content."""
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Word
from .security import auth_version
from .tests import GOOGLE_SETTINGS


@override_settings(**GOOGLE_SETTINGS)
class WordManagementTests(TestCase):
    def setUp(self):
        session = self.client.session
        session.update({"auth_kind": "google", "google_email": "owner@example.test", "google_subject": "test-owner", "auth_version": auth_version()})
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

    def test_home_includes_all_statuses_and_correct_due_count(self):
        Word.objects.create(chunk="due chunk")
        Word.objects.create(chunk="future chunk", due_at=timezone.now() + timedelta(days=1))
        Word.objects.create(chunk="archived chunk", archived_at=timezone.now())
        response = self.client.get(reverse("home"))
        self.assertContains(response, "due chunk")
        self.assertContains(response, "future chunk")
        self.assertContains(response, "archived chunk")
        self.assertEqual(response.context["word_count"], 3)
        self.assertEqual(response.context["due_count"], 1)
        self.assertContains(response, 'href="/words/add/"')

    def test_user_content_is_escaped_in_lists_forms_and_confirmation(self):
        unsafe = '<script>alert("private")</script>'
        word = Word.objects.create(chunk=unsafe, meaning=unsafe, example=unsafe)
        for url in [reverse("home"), reverse("word-edit", args=[word.pk]), reverse("word-delete", args=[word.pk])]:
            with self.subTest(url=url):
                response = self.client.get(url)
                if url != reverse("word-delete", args=[word.pk]):
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

    def test_learned_filter_shows_only_learned_words_with_reset_action(self):
        Word.objects.create(chunk="still learning")
        word = Word.objects.create(chunk="learned chunk", stage=5, archived_at=timezone.now())
        response = self.client.get(reverse("home"), {"progress": "learned"})
        self.assertContains(response, "learned chunk")
        self.assertNotContains(response, "still learning")
        self.assertContains(response, reverse("word-reset", args=[word.pk]))
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
        self.assertRedirects(self.client.post(reverse("word-edit", args=[word.pk]), {"chunk": "learned phrase"}), reverse("home") + "?progress=learned")
        word.refresh_from_db()
        self.assertEqual(word.archived_at, archived_at)
        self.assertEqual(word.stage, 5)
        self.assertRedirects(self.client.post(reverse("word-delete", args=[word.pk])), reverse("home") + "?progress=learned")

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
        self.assertRedirects(self.client.get(reverse("archive")), reverse("home") + "?progress=learned")
        self.assertContains(self.client.get(reverse("home"), {"progress": "learned"}), "No words yet")
        response = self.client.get(reverse("word-add"))
        self.assertContains(response, "Obverse")
        self.assertContains(response, "Reverse")
        self.assertContains(response, "Extra")
        self.assertIn("no-store", response["Cache-Control"])


    def test_status_counts_filters_and_search(self):
        new = Word.objects.create(chunk="alpha", meaning="first meaning")
        practiced = Word.objects.create(chunk="beta", stage=2, example="A hidden phrase", due_at=timezone.now() + timedelta(days=1))
        learned = Word.objects.create(chunk="gamma", stage=5, archived_at=timezone.now())
        response = self.client.get(reverse("home"))
        for name in ("to_learn_count", "practiced_count", "learned_count"):
            self.assertEqual(response.context[name], 1)
        self.assertFalse(response.context["cards_open"])
        for status, word in (("to-learn", new), ("practiced", practiced), ("learned", learned)):
            response = self.client.get(reverse("home"), {"progress": status})
            self.assertEqual(list(response.context["words"]), [word])
            self.assertTrue(response.context["cards_open"])
            self.assertEqual(response.context["total_count"], 3)
        for query, word in (("ALPHA", new), ("first meaning", new), ("hidden phrase", practiced)):
            response = self.client.get(reverse("home"), {"q": query})
            self.assertEqual(list(response.context["words"]), [word])
        response = self.client.get(reverse("home"), {"q": "gamma", "progress": "practiced"})
        self.assertContains(response, "No words match")
        self.assertEqual(self.client.get(reverse("home"), {"progress": "invalid"}).context["word_count"], 3)

    def test_home_structure_and_no_archive_button(self):
        Word.objects.create(chunk="test card")
        response = self.client.get(reverse("home"))
        for text in ("To learn", "Practiced", "Learned", "Start learning", "My cards", "Reset", "Edit", "Select", "Delete"):
            self.assertContains(response, text)
        self.assertContains(response, '<details class="cards-browser"')
        self.assertContains(response, 'aria-label="Add word"')
        self.assertNotContains(response, '<img')
        self.assertNotContains(self.client.get(reverse("learning-settings")), "Archive")

    def test_reset_confirms_then_resets_text_and_progress(self):
        word = Word.objects.create(chunk="keep text", meaning="keep meaning", stage=5,
                                   archived_at=timezone.now(), due_at=timezone.now()+timedelta(days=14))
        url = reverse("word-reset", args=[word.pk])
        self.assertContains(self.client.get(url), "Reset this word?")
        word.refresh_from_db()
        self.assertEqual(word.stage, 5)
        before = timezone.now()
        self.assertRedirects(self.client.post(url), reverse("home") + "?progress=to-learn")
        word.refresh_from_db()
        self.assertEqual((word.chunk, word.meaning, word.stage), ("keep text", "keep meaning", 0))
        self.assertIsNone(word.archived_at)
        self.assertGreaterEqual(word.due_at, before)
        self.assertLessEqual(word.due_at, timezone.now())

    def test_bulk_preview_does_not_mutate_and_confirm_changes_only_selected(self):
        one = Word.objects.create(chunk="one", stage=2)
        two = Word.objects.create(chunk="two", stage=5, archived_at=timezone.now())
        keep = Word.objects.create(chunk="keep", stage=3)
        url = reverse("word-bulk")
        data = {"word_ids": [one.pk, two.pk], "action": "reset"}
        self.assertContains(self.client.post(url, data), "Reset 2 cards?")
        one.refresh_from_db()
        self.assertEqual(one.stage, 2)
        self.assertRedirects(self.client.post(url, {**data, "confirmed": "yes"}), reverse("home") + "?cards=open")
        for word in (one, two):
            word.refresh_from_db()
            self.assertEqual(word.stage, 0)
            self.assertIsNone(word.archived_at)
        keep.refresh_from_db()
        self.assertEqual(keep.stage, 3)
        data["action"] = "delete"
        self.assertContains(self.client.post(url, data), "Delete 2 cards?")
        self.assertEqual(Word.objects.count(), 3)
        self.assertRedirects(self.client.post(url, {**data, "confirmed": "yes"}), reverse("home") + "?cards=open")
        self.assertEqual(list(Word.objects.values_list("pk", flat=True)), [keep.pk])

    def test_invalid_bulk_requests_do_not_change_words(self):
        word = Word.objects.create(chunk="safe", stage=3)
        url = reverse("word-bulk")
        for data in ({}, {"action": "delete", "word_ids": []},
                     {"action": "invalid", "word_ids": [word.pk]},
                     {"action": "delete", "word_ids": [word.pk, 99999], "confirmed": "yes"},
                     {"action": "reset", "word_ids": [word.pk] * 501}):
            self.assertEqual(self.client.post(url, data).status_code, 400)
            word.refresh_from_db()
            self.assertEqual(word.stage, 3)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_reset_and_bulk_require_authentication_and_csrf(self):
        word = Word.objects.create(chunk="private")
        for url in (reverse("word-reset", args=[word.pk]), reverse("word-bulk")):
            self.assertRedirects(Client().post(url), reverse("login"))
            self.assertEqual(Client(enforce_csrf_checks=True).post(url).status_code, 403)


    def test_readiness_drives_counts_filters_and_each_card_status(self):
        from .models import StudySession
        now = timezone.now()
        due = Word.objects.create(chunk="due again", stage=3, due_at=now-timedelta(days=1))
        future_zero = Word.objects.create(chunk="reviewed at stage zero", stage=0, due_at=now+timedelta(days=1))
        future = Word.objects.create(chunk="waiting", stage=2, due_at=now+timedelta(days=3))
        retry = Word.objects.create(chunk="pending retry", stage=1, due_at=now+timedelta(days=1))
        learned = Word.objects.create(chunk="learned", stage=5, archived_at=now)
        StudySession.objects.create(queue=[retry.pk])
        response = self.client.get(reverse("home"))
        self.assertEqual(response.context["to_learn_count"], 2)
        self.assertEqual(response.context["practiced_count"], 2)
        self.assertEqual(response.context["learned_count"], 1)
        listed = {word.pk: word for word in response.context["words"]}
        for word in (due, retry):
            self.assertTrue(listed[word.pk].ready_to_practice)
        for word in (future_zero, future, learned):
            self.assertFalse(listed[word.pk].ready_to_practice)
        for status, expected in (("to-learn", {due.pk, retry.pk}), ("practiced", {future_zero.pk, future.pk}), ("learned", {learned.pk})):
            page = self.client.get(reverse("home"), {"progress": status})
            self.assertEqual({word.pk for word in page.context["words"]}, expected)
            if status == "to-learn":
                self.assertContains(page, "To learn · Ready to review", count=2)
            elif status == "practiced":
                self.assertContains(page, "Practiced · Next review:", count=2)

    def test_status_becomes_to_learn_when_review_time_arrives(self):
        from unittest.mock import patch
        due = timezone.now() + timedelta(days=1)
        word = Word.objects.create(chunk="scheduled", stage=2, due_at=due)
        with patch("cards.views.timezone.now", return_value=due-timedelta(seconds=1)):
            response = self.client.get(reverse("home"))
            self.assertEqual(response.context["to_learn_count"], 0)
            self.assertContains(response, "Practiced · Next review:")
        with patch("cards.views.timezone.now", return_value=due):
            response = self.client.get(reverse("home"))
            self.assertEqual(response.context["to_learn_count"], 1)
            self.assertEqual(response.context["practiced_count"], 0)
            self.assertContains(response, "To learn · Ready to review")
        word.refresh_from_db()
        self.assertEqual(word.stage, 2)
        self.assertEqual(word.due_at, due)
