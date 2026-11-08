"""Ongoing queues admit due cards once, preserve scheduling, and support Undo."""
from datetime import datetime, timedelta, timezone as utc_timezone
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import learning
from .models import StudyReview, StudySession, Word, WordImport
from .importing import confirm_import
from .security import auth_version
from .tests import GOOGLE_SETTINGS


@override_settings(**GOOGLE_SETTINGS)
class EndlessSessionTests(TestCase):
    def setUp(self):
        session = self.client.session
        session.update({"auth_kind": "google", "google_email": "owner@example.test", "google_subject": "owner", "auth_version": auth_version()})
        session.save()

    def respond(self, choice="know"):
        session = learning.active_session()
        learning.reveal(session.response_token)
        learning.answer(session.response_token, choice)
        session.refresh_from_db()
        return session

    def test_future_cards_wait_then_join_same_session_exactly_when_due(self):
        now = datetime(2026, 11, 8, 10, tzinfo=utc_timezone.utc)
        word = Word.objects.create(chunk="future", stage=2, due_at=now+timedelta(hours=1))
        with patch("cards.learning.timezone.now", return_value=now):
            session = learning.start_or_resume()
            self.assertEqual(session.queue, [])
            self.assertIsNone(session.completed_at)
            self.assertEqual(self.client.get(reverse("study-status")).json()["remaining"], 0)
        with patch("cards.learning.timezone.now", return_value=word.due_at):
            current, queued_word = learning.study_state()
            self.assertEqual(current.pk, session.pk)
            self.assertEqual(current.queue, [word.pk])
            self.assertEqual(queued_word.pk, word.pk)
            self.assertEqual(current.initial_stages[str(word.pk)], 2)
            self.assertEqual(learning.study_state()[0].queue, [word.pk])
            self.respond()
        word.refresh_from_db()
        self.assertEqual(word.stage, 3)
        self.assertEqual(StudySession.objects.count(), 1)

    def test_new_and_due_cards_append_without_changing_revealed_head(self):
        first = Word.objects.create(chunk="first")
        second = Word.objects.create(chunk="already queued")
        session = learning.start_or_resume()
        learning.reveal(session.response_token)
        new = Word.objects.create(chunk="new")
        overdue = Word.objects.create(chunk="overdue arrival", due_at=timezone.now()-timedelta(days=1), stage=2)
        Word.objects.create(chunk="too early", due_at=timezone.now()+timedelta(days=1))
        Word.objects.create(chunk="learned", archived_at=timezone.now())
        updated, word = learning.study_state()
        self.assertEqual(updated.queue, [first.pk, second.pk, overdue.pk, new.pk])
        self.assertEqual(updated.response_token, session.response_token)
        self.assertTrue(updated.revealed)
        self.assertEqual(word.pk, first.pk)
        self.assertEqual(learning.study_state()[0].queue, updated.queue)

    def test_answer_discovers_new_cards_and_retries_go_after_them(self):
        first = Word.objects.create(chunk="first", stage=3)
        second = Word.objects.create(chunk="second")
        learning.start_or_resume()
        third = Word.objects.create(chunk="third")
        session = self.respond("dont_know")
        self.assertEqual(session.queue, [second.pk, third.pk, first.pk])
        first.refresh_from_db()
        self.assertEqual(first.stage, 1)

    def test_later_review_gets_fresh_baseline_and_no_old_setback(self):
        word = Word.objects.create(chunk="review again", stage=3)
        session = learning.start_or_resume()
        self.respond("repeat")
        self.respond("know")
        word.refresh_from_db()
        self.assertEqual(word.stage, 2)
        due = word.due_at
        with patch("cards.learning.timezone.now", return_value=due):
            resumed = learning.start_or_resume()
            self.assertEqual(resumed.pk, session.pk)
            self.assertEqual(resumed.initial_stages[str(word.pk)], 2)
            self.assertNotIn(str(word.pk), resumed.setbacks)
            self.respond("know")
        word.refresh_from_db()
        self.assertEqual(word.stage, 3)

    def test_undo_keeps_later_arrivals_at_end(self):
        first = Word.objects.create(chunk="first")
        second = Word.objects.create(chunk="second")
        session = learning.start_or_resume()
        self.respond()
        third = Word.objects.create(chunk="arrived later", stage=2)
        current, _ = learning.study_state()
        self.assertEqual(current.queue, [second.pk, third.pk])
        learning.undo(current.response_token)
        session.refresh_from_db()
        self.assertEqual(session.queue, [first.pk, second.pk, third.pk])
        self.assertEqual(session.initial_stages[str(third.pk)], 2)
        self.assertFalse(session.revealed)

    def test_undo_discovers_new_words_even_without_polling_first(self):
        first = Word.objects.create(chunk="first")
        session = learning.start_or_resume()
        self.respond()
        session.refresh_from_db()
        new = Word.objects.create(chunk="new while waiting")
        learning.undo(session.response_token)
        session.refresh_from_db()
        self.assertEqual(session.queue, [first.pk, new.pk])
        self.assertFalse(session.revealed)

    def test_undo_across_scheduled_reviews_restores_each_prior_state(self):
        word = Word.objects.create(chunk="ongoing undo")
        original = learning.word_progress(word)
        session = learning.start_or_resume()
        self.respond()
        word.refresh_from_db()
        first_state = learning.word_progress(word)
        with patch("cards.learning.timezone.now", return_value=word.due_at):
            learning.start_or_resume()
            self.respond()
            session.refresh_from_db()
            learning.undo(session.response_token)
            session.refresh_from_db()
            word.refresh_from_db()
            self.assertEqual(learning.word_progress(word), first_state)
            learning.undo(session.response_token)
        session.refresh_from_db()
        word.refresh_from_db()
        self.assertEqual(learning.word_progress(word), original)
        self.assertEqual(session.queue, [word.pk])
        self.assertEqual(session.initial_stages[str(word.pk)], 0)
        self.assertFalse(StudyReview.objects.exists())

    def test_legacy_completed_session_reopens_without_losing_undo(self):
        Word.objects.create(chunk="legacy")
        session = learning.start_or_resume()
        self.respond()
        StudySession.objects.filter(pk=session.pk).update(completed_at=timezone.now())
        reopened = learning.start_or_resume()
        self.assertEqual(reopened.pk, session.pk)
        self.assertIsNone(reopened.completed_at)
        self.assertTrue(learning.can_undo(reopened))
        self.assertEqual(StudySession.objects.count(), 1)

    def test_stale_waiting_form_cannot_answer_a_new_arrival(self):
        session = learning.start_or_resume()
        old_token = session.response_token
        word = Word.objects.create(chunk="new arrival")
        learning.reveal(old_token)
        learning.answer(old_token, "know")
        word.refresh_from_db()
        self.assertEqual(word.stage, 0)
        current = learning.active_session()
        self.assertNotEqual(current.response_token, old_token)
        self.assertFalse(current.revealed)

    def test_imported_words_join_at_end_without_restarting(self):
        first = Word.objects.create(chunk="first")
        session = learning.start_or_resume()
        batch = WordImport.objects.create(rows=[{"chunk": "imported", "meaning": "", "example": ""}])
        confirm_import(batch.pk)
        current, _ = learning.study_state()
        self.assertEqual(current.pk, session.pk)
        self.assertEqual(current.queue, [first.pk, Word.objects.get(chunk="imported").pk])

    def test_status_is_private_get_only_and_does_not_reveal_word_text(self):
        Word.objects.create(chunk="private chunk", meaning="private meaning")
        url = reverse("study-status")
        self.assertRedirects(Client().get(url), reverse("login"))
        self.assertEqual(self.client.post(url).status_code, 405)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["remaining"], 1)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotContains(response, "private chunk")
        self.assertNotContains(response, "private meaning")
