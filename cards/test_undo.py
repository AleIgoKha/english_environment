"""Durable, atomic and repeatable undo of study answers."""
from datetime import timedelta
from unittest.mock import patch
import uuid

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import learning
from .models import LearningSettings, StudyReview, StudySession, Word
from .security import auth_version
from .tests import GOOGLE_SETTINGS


@override_settings(**GOOGLE_SETTINGS)
class UndoTests(TestCase):
    def setUp(self):
        session = self.client.session
        session.update({"auth_kind": "google", "google_email": "owner@example.test", "google_subject": "test-owner", "auth_version": auth_version()})
        session.save()

    def respond(self, choice):
        session = learning.active_session()
        learning.reveal(session.response_token)
        learning.answer(session.response_token, choice)
        session.refresh_from_db()
        return session

    def undo(self, session):
        session.refresh_from_db()
        learning.undo(session.response_token)
        session.refresh_from_db()
        return session

    def test_every_choice_restores_exact_word_state_and_queue(self):
        for choice in ("know", "repeat", "dont_know"):
            with self.subTest(choice=choice):
                StudySession.objects.all().delete()
                Word.objects.all().delete()
                word = Word.objects.create(chunk="chosen", stage=3, due_at=timezone.now()-timedelta(days=3))
                other = Word.objects.create(chunk="other")
                before = learning.word_progress(word)
                session = learning.start_or_resume()
                queue = list(session.queue)
                self.respond(choice)
                self.assertTrue(learning.can_undo(session))
                self.undo(session)
                word.refresh_from_db()
                self.assertEqual(learning.word_progress(word), before)
                self.assertEqual(session.queue, queue)
                self.assertEqual(session.initial_stages[str(word.pk)], 3)
                self.assertEqual(session.setbacks, {})
                self.assertFalse(session.revealed)
                self.assertIsNone(session.completed_at)
                self.assertFalse(learning.can_undo(session))
                other.refresh_from_db()
                self.assertEqual(other.stage, 0)

    def test_multiple_undos_restore_repeated_responses_and_setbacks(self):
        word = Word.objects.create(chunk="practice", stage=3)
        original = learning.word_progress(word)
        session = learning.start_or_resume()
        self.respond("repeat")
        first = StudyReview.objects.latest("pk")
        self.respond("dont_know")
        self.respond("know")
        self.assertEqual(StudyReview.objects.count(), 3)
        self.undo(session)
        self.assertEqual(session.setbacks[str(word.pk)], 2)
        self.undo(session)
        self.assertEqual(session.setbacks[str(word.pk)], 1)
        word.refresh_from_db()
        self.assertEqual(learning.word_progress(word), first.word_after)
        self.undo(session)
        word.refresh_from_db()
        self.assertEqual(learning.word_progress(word), original)
        self.assertEqual(session.setbacks, {})
        self.assertEqual(session.queue, [word.pk])
        self.assertEqual(StudyReview.objects.count(), 0)

    def test_undo_while_waiting_restores_learned_word(self):
        word = Word.objects.create(chunk="almost learned", stage=4)
        session = learning.start_or_resume()
        self.assertNotContains(self.client.get(reverse("study")), '>Undo</button>')
        self.respond("know")
        word.refresh_from_db()
        self.assertIsNotNone(word.archived_at)
        summary = self.client.get(reverse("study"))
        self.assertContains(summary, "Well done!")
        self.assertContains(summary, '>Undo</button>')
        session.refresh_from_db()
        self.assertRedirects(self.client.post(reverse("study-undo"), {"response_token": session.response_token}), reverse("study"))
        word.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(word.stage, 4)
        self.assertIsNone(word.archived_at)
        self.assertIsNone(session.completed_at)
        self.assertContains(self.client.get(reverse("study")), "almost learned")

    def test_undo_stack_survives_reopening_browser(self):
        first = Word.objects.create(chunk="first")
        second = Word.objects.create(chunk="second")
        session = learning.start_or_resume()
        self.respond("know")
        self.respond("know")
        reopened = Client()
        reopened.cookies = self.client.cookies.copy()
        for expected in (second, first):
            session.refresh_from_db()
            reopened.post(reverse("study-undo"), {"response_token": session.response_token})
            page = reopened.get(reverse("study"))
            self.assertEqual(page.context["word"].pk, expected.pk)
            self.assertFalse(page.context["study_session"].revealed)
        self.assertFalse(StudyReview.objects.exists())

    def test_duplicate_undo_and_stale_answer_cannot_apply_twice(self):
        first = Word.objects.create(chunk="first")
        second = Word.objects.create(chunk="second")
        session = learning.start_or_resume()
        self.respond("know")
        self.respond("know")
        session.refresh_from_db()
        token = session.response_token
        learning.undo(token)
        restored_token = StudySession.objects.get(pk=session.pk).response_token
        learning.undo(token)
        learning.answer(token, "know")
        session.refresh_from_db()
        self.assertEqual(session.response_token, restored_token)
        self.assertEqual(session.queue, [second.pk])
        self.assertEqual(StudyReview.objects.count(), 1)
        second.refresh_from_db()
        self.assertEqual(second.stage, 0)
        first.refresh_from_db()
        self.assertEqual(first.stage, 1)

    def test_answering_again_after_undo_creates_a_new_branch(self):
        word = Word.objects.create(chunk="choose again", stage=3)
        session = learning.start_or_resume()
        self.respond("dont_know")
        self.undo(session)
        learning.reveal(session.response_token)
        learning.answer(session.response_token, "know")
        word.refresh_from_db()
        self.assertEqual(word.stage, 4)
        self.assertEqual(StudyReview.objects.count(), 1)
        self.undo(session)
        word.refresh_from_db()
        self.assertEqual(word.stage, 3)

    def test_resuming_waiting_session_keeps_history_but_invalidates_old_forms(self):
        first = Word.objects.create(chunk="first")
        session = learning.start_or_resume()
        self.respond("know")
        old_token = StudySession.objects.get(pk=session.pk).response_token
        second = Word.objects.create(chunk="second")
        new_session = learning.start_or_resume()
        self.assertTrue(learning.can_undo(new_session))
        self.assertEqual(new_session.pk, session.pk)
        learning.undo(old_token)
        first.refresh_from_db()
        self.assertEqual(first.stage, 1)
        self.assertEqual(learning.active_session().queue, [second.pk])

    def test_undo_preserves_text_edits_and_current_settings(self):
        word = Word.objects.create(chunk="old text", stage=3)
        session = learning.start_or_resume()
        self.respond("know")
        self.client.post(reverse("word-edit", args=[word.pk]), {"chunk": "new text", "meaning": "new meaning"})
        learning.change_target(2)
        self.undo(session)
        word.refresh_from_db()
        self.assertEqual((word.chunk, word.meaning, word.stage), ("new text", "new meaning", 3))
        self.assertEqual(LearningSettings.objects.get(pk=1).review_target, 2)

    def test_reset_clears_history_but_outside_deletion_keeps_other_undo(self):
        first = Word.objects.create(chunk="first")
        second = Word.objects.create(chunk="second")
        session = learning.start_or_resume()
        self.respond("know")
        learning.reset_word(first.pk)
        self.assertFalse(StudyReview.objects.exists())
        self.respond("know")
        self.assertTrue(StudyReview.objects.exists())
        self.client.post(reverse("word-delete", args=[first.pk]))
        self.assertEqual(list(StudyReview.objects.values_list("word_id", flat=True)), [second.pk])
        self.undo(session)
        self.assertFalse(Word.objects.filter(pk=first.pk).exists())
        second.refresh_from_db()
        self.assertEqual(second.stage, 0)

    def test_delete_during_learning_shows_undo_and_restores_deleted_card(self):
        first = Word.objects.create(chunk="delete from Learning", meaning="keep reverse", stage=3)
        other = Word.objects.create(chunk="next card")
        before = learning.word_progress(first)
        session = learning.start_or_resume()
        page = self.client.post(reverse("word-delete", args=[first.pk]), {"return_to": "study"}, follow=True)
        self.assertFalse(Word.objects.filter(pk=first.pk).exists())
        self.assertContains(page, '>Undo</button>')
        self.assertEqual(page.context["word"].pk, other.pk)
        self.assertEqual(StudyReview.objects.get().choice, "delete")
        self.undo(session)
        first.refresh_from_db()
        self.assertIsNone(first.deleted_at)
        self.assertEqual(learning.word_progress(first), before)
        self.assertEqual(first.meaning, "keep reverse")
        self.assertEqual(session.queue, [first.pk, other.pk])
        self.assertFalse(session.revealed)

    def test_undo_last_card_deletion_is_available_while_waiting_and_survives_reopen(self):
        word = Word.objects.create(chunk="last card")
        session = learning.start_or_resume()
        page = self.client.post(reverse("word-delete", args=[word.pk]), {"return_to": "study"}, follow=True)
        self.assertContains(page, "Well done!")
        self.assertContains(page, '>Undo</button>')
        reopened = Client()
        reopened.cookies = self.client.cookies.copy()
        session.refresh_from_db()
        token = session.response_token
        page = reopened.post(reverse("study-undo"), {"response_token": token}, follow=True)
        self.assertContains(page, "last card")
        self.assertTrue(Word.objects.filter(pk=word.pk).exists())
        self.assertEqual(page.context["remaining"], 1)
        new_token = page.context["study_session"].response_token
        reopened.post(reverse("study-undo"), {"response_token": token})
        session.refresh_from_db()
        self.assertEqual(session.response_token, new_token)
        self.assertFalse(StudyReview.objects.exists())

    def test_undo_learning_deletion_then_previous_answer(self):
        first = Word.objects.create(chunk="answered first")
        second = Word.objects.create(chunk="deleted second")
        session = learning.start_or_resume()
        self.respond("know")
        self.client.post(reverse("word-delete", args=[second.pk]), {"return_to": "study"})
        self.assertEqual(StudyReview.objects.count(), 2)
        self.undo(session)
        self.assertEqual(session.queue, [second.pk])
        self.undo(session)
        self.assertEqual(session.queue, [first.pk, second.pk])
        first.refresh_from_db()
        self.assertEqual(first.stage, 0)

    def test_outside_deletion_skips_latest_answer_and_undoes_previous_card(self):
        first = Word.objects.create(chunk="keep undo for first")
        second = Word.objects.create(chunk="deleted outside Learning")
        session = learning.start_or_resume()
        self.respond("know")
        self.respond("know")
        self.client.post(reverse("word-delete", args=[second.pk]))
        self.assertContains(self.client.get(reverse("study")), '>Undo</button>')
        self.undo(session)
        self.assertEqual(session.queue, [first.pk])
        first.refresh_from_db()
        self.assertEqual(first.stage, 0)
        second.refresh_from_db()
        self.assertIsNotNone(second.deleted_at)
        self.assertFalse(Word.objects.filter(pk=second.pk).exists())

    def test_bulk_deletion_outside_learning_preserves_other_history(self):
        first = Word.objects.create(chunk="delete first")
        second = Word.objects.create(chunk="keep second")
        third = Word.objects.create(chunk="delete third")
        fourth = Word.objects.create(chunk="still learning")
        session = learning.start_or_resume()
        for _ in range(3):
            self.respond("know")
        self.client.post(reverse("word-bulk"), {"action": "delete", "confirmed": "yes",
                                               "word_ids": [first.pk, third.pk]})
        self.assertEqual(list(StudyReview.objects.values_list("word_id", flat=True)), [second.pk])
        self.undo(session)
        self.assertEqual(session.queue, [second.pk, fourth.pk])
        self.assertEqual(Word.all_objects.filter(deleted_at__isnull=False).count(), 2)

    def test_undo_skips_directly_deleted_word_without_discarding_earlier_history(self):
        first = Word.objects.create(chunk="valid earlier action")
        second = Word.objects.create(chunk="deleted externally")
        session = learning.start_or_resume()
        self.respond("know")
        self.respond("know")
        second.delete()
        self.undo(session)
        self.assertEqual(session.queue, [first.pk])
        self.assertFalse(Word.objects.filter(pk=second.pk).exists())
        self.assertFalse(StudyReview.objects.exists())

    def test_outside_progress_changes_are_not_overwritten(self):
        word = Word.objects.create(chunk="changed")
        session = learning.start_or_resume()
        self.respond("know")
        Word.objects.filter(pk=word.pk).update(stage=3)
        self.undo(session)
        word.refresh_from_db()
        self.assertEqual(word.stage, 3)
        self.assertFalse(StudyReview.objects.exists())

    def test_undo_rollback_does_not_lose_history(self):
        word = Word.objects.create(chunk="atomic undo")
        session = learning.start_or_resume()
        self.respond("know")
        session.refresh_from_db()
        token = session.response_token
        with patch("cards.learning.StudySession.save", side_effect=RuntimeError("simulated failure")):
            with self.assertRaises(RuntimeError):
                learning.undo(token)
        word.refresh_from_db()
        self.assertEqual(word.stage, 1)
        self.assertEqual(StudySession.objects.get(pk=session.pk).response_token, token)
        self.assertTrue(StudyReview.objects.exists())

    def test_undo_requires_authentication_post_csrf_and_valid_token(self):
        url = reverse("study-undo")
        self.assertRedirects(Client().post(url), reverse("login"))
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(Client(enforce_csrf_checks=True).post(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"response_token": "invalid"}).status_code, 400)
        self.assertRedirects(self.client.post(url, {"response_token": uuid.uuid4()}), reverse("study"))


    def test_undo_returns_to_hidden_answer_and_requires_reveal_again(self):
        word = Word.objects.create(chunk="remember", meaning="hidden meaning", example="hidden example")
        session = learning.start_or_resume()
        self.respond("know")
        session.refresh_from_db()
        page = self.client.post(reverse("study-undo"), {"response_token": session.response_token}, follow=True)
        self.assertContains(page, "Reveal answer")
        self.assertNotContains(page, "hidden meaning")
        self.assertNotContains(page, "hidden example")
        session.refresh_from_db()
        learning.answer(session.response_token, "know")
        word.refresh_from_db()
        self.assertEqual(word.stage, 0)
        self.respond("know")
        word.refresh_from_db()
        self.assertEqual(word.stage, 1)
