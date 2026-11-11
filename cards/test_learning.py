"""Exercise scheduling and durable session rules using disposable words only."""
from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch
import uuid

from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import learning
from .models import LearningSettings, StudySession, Word
from .security import auth_version
from .tests import GOOGLE_SETTINGS


@override_settings(**GOOGLE_SETTINGS)
class LearningTests(TestCase):
    def setUp(self):
        session = self.client.session
        session.update({"auth_kind": "google", "google_email": "owner@example.test", "google_subject": "test-owner", "auth_version": auth_version()})
        session.save()

    def respond(self, choice="know"):
        session = learning.active_session()
        learning.reveal(session.response_token)
        learning.answer(session.response_token, choice)
        session.refresh_from_db()
        return session

    def test_start_includes_only_new_and_due_words_in_due_order(self):
        newer = Word.objects.create(chunk="new")
        older = Word.objects.create(chunk="overdue", due_at=timezone.now() - timedelta(days=2), stage=3)
        Word.objects.create(chunk="future", due_at=timezone.now() + timedelta(days=1))
        Word.objects.create(chunk="learned", archived_at=timezone.now())
        session = learning.start_or_resume()
        self.assertEqual(session.queue, [older.pk, newer.pk])
        self.assertEqual(session.initial_stages, {str(older.pk): 3, str(newer.pk): 0})
        self.assertEqual(session.total_words, 2)

    def test_empty_deck_waits_in_an_open_session(self):
        response = self.client.post(reverse("study-start"), follow=True)
        self.assertContains(response, "Well done!")
        self.assertEqual(learning.active_session().queue, [])
        self.assertIsNone(StudySession.objects.get().completed_at)

    def test_start_resumes_and_appends_new_words_to_the_same_session(self):
        word = Word.objects.create(chunk="first")
        first = learning.start_or_resume()
        added = Word.objects.create(chunk="added later")
        again = learning.start_or_resume()
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(again.queue, [word.pk, added.pk])
        self.assertEqual(StudySession.objects.count(), 1)
        self.respond()
        next_session = learning.start_or_resume()
        self.assertEqual(next_session.pk, first.pk)
        self.assertEqual(len(next_session.queue), 1)

    def test_reveal_is_required_and_is_saved_on_server(self):
        word = Word.objects.create(chunk="visible chunk", meaning="hidden meaning", example="hidden example")
        session = learning.start_or_resume()
        response = self.client.get(reverse("study"))
        self.assertContains(response, word.chunk)
        self.assertNotContains(response, "hidden meaning")
        self.assertNotContains(response, "hidden example")
        learning.answer(session.response_token, "know")
        word.refresh_from_db()
        self.assertEqual(word.stage, 0)
        self.client.post(reverse("study-reveal"), {"response_token": session.response_token})
        reopened = Client()
        reopened.cookies = self.client.cookies.copy()
        response = reopened.get(reverse("study"))
        self.assertContains(response, "hidden meaning")
        self.assertContains(response, "hidden example")
        self.assertContains(response, "Don't know")
        self.assertIn("no-store", response["Cache-Control"])

    def test_chunk_only_can_be_revealed_and_reviewed(self):
        word = Word.objects.create(chunk="hello")
        session = learning.start_or_resume()
        learning.reveal(session.response_token)
        self.assertContains(self.client.get(reverse("study")), "No reverse or extra added.")
        self.respond()
        word.refresh_from_db()
        self.assertEqual(word.stage, 1)

    def test_show_reverse_immediately_can_be_answered_without_reveal(self):
        word = Word.objects.create(chunk="visible", meaning="answer text", example="extra text",
                                   show_reverse_immediately=True)
        next_word = Word.objects.create(chunk="next", meaning="keep hidden")
        response = self.client.post(reverse("study-start"), follow=True)
        self.assertContains(response, "answer text")
        self.assertContains(response, "extra text")
        self.assertNotContains(response, "Reveal answer")
        self.assertContains(response, "Don't know")
        session = learning.active_session()
        self.client.post(reverse("study-answer"), {"response_token": session.response_token, "answer": "know"})
        word.refresh_from_db()
        self.assertEqual(word.stage, 1)
        response = self.client.get(reverse("study"))
        self.assertContains(response, next_word.chunk)
        self.assertNotContains(response, "keep hidden")
        self.assertContains(response, "Reveal answer")
        self.assertFalse(next_word.show_reverse_immediately)

    def test_automatic_reverse_is_applied_after_repeat_and_undo(self):
        word = Word.objects.create(chunk="visible", meaning="answer text", show_reverse_immediately=True)
        session, _ = learning.study_state()
        learning.answer(session.response_token, "repeat")
        session, _ = learning.study_state()
        self.assertTrue(session.revealed)
        learning.answer(session.response_token, "know")
        session.refresh_from_db()
        learning.undo(session.response_token)
        session, current_word = learning.study_state()
        self.assertEqual(current_word.pk, word.pk)
        self.assertTrue(session.revealed)

    def test_know_advances_once_and_is_not_due_again_the_same_day(self):
        word = Word.objects.create(chunk="remember")
        learning.start_or_resume()
        session = self.respond()
        word.refresh_from_db()
        self.assertEqual(word.stage, 1)
        self.assertIsNone(session.completed_at)
        self.assertIsNone(word.archived_at)
        self.assertEqual(timezone.localtime(word.due_at).date(), timezone.localdate() + timedelta(days=1))
        self.assertEqual(learning.start_or_resume().queue, [])

    def test_five_spaced_successes_use_expected_gaps_then_archive(self):
        current = datetime(2026, 11, 2, 12, tzinfo=datetime_timezone.utc)
        word = Word.objects.create(chunk="progress", due_at=current)
        for stage in range(1, 6):
            with self.subTest(stage=stage), patch("cards.learning.timezone.now", return_value=current):
                learning.start_or_resume()
                self.respond()
                word.refresh_from_db()
                self.assertEqual(word.stage, stage)
                if stage < 5:
                    gap = [1, 3, 7, 14][stage - 1]
                    self.assertEqual(timezone.localtime(word.due_at).date(), timezone.localtime(current).date() + timedelta(days=gap))
                    self.assertIsNone(word.archived_at)
                    current = word.due_at
                else:
                    self.assertEqual(word.archived_at, current)
        self.assertEqual(self.client.get(reverse("home"), {"progress": "practiced"}).context["word_count"], 0)
        self.assertContains(self.client.get(reverse("home"), {"progress": "learned"}), "progress")

    def test_extended_schedule_and_stage_zero(self):
        current = datetime(2026, 11, 2, 12, tzinfo=datetime_timezone.utc)
        for stage, gap in enumerate([1, 1, 3, 7, 14, 30, 60, 120, 240, 365]):
            with self.subTest(stage=stage):
                due = learning.next_review(stage, current)
                self.assertEqual(due.date(), timezone.localtime(current).date() + timedelta(days=gap))
                self.assertEqual((due.hour, due.minute), (0, 0))

    def test_calendar_days_use_chisinau_across_utc_midnight_and_dst(self):
        for current, expected in [
            (datetime(2026, 11, 2, 23, 30, tzinfo=datetime_timezone.utc), (2026, 11, 4, 2)),
            (datetime(2026, 3, 28, 12, tzinfo=datetime_timezone.utc), (2026, 3, 29, 2)),
            (datetime(2026, 10, 24, 12, tzinfo=datetime_timezone.utc), (2026, 10, 25, 3)),
        ]:
            with self.subTest(current=current):
                due = learning.next_review(1, current)
                self.assertEqual((due.year, due.month, due.day), expected[:3])
                self.assertEqual(due.utcoffset(), timedelta(hours=expected[3]))

    def test_repeated_repeat_never_sets_back_more_than_one_stage(self):
        word = Word.objects.create(chunk="repeat", stage=4)
        learning.start_or_resume()
        for attempt in range(3):
            session = self.respond("repeat")
            word.refresh_from_db()
            self.assertEqual(word.stage, 3)
            self.assertEqual(session.setbacks[str(word.pk)], 1)
        self.respond("know")
        word.refresh_from_db()
        self.assertEqual(word.stage, 3)
        self.assertEqual(learning.active_session().queue, [])

    def test_repeated_dont_know_never_sets_back_more_than_two_stages(self):
        word = Word.objects.create(chunk="difficult", stage=4)
        learning.start_or_resume()
        for attempt in range(3):
            self.respond("dont_know")
            word.refresh_from_db()
            self.assertEqual(word.stage, 2)
        self.respond("know")
        word.refresh_from_db()
        self.assertEqual(word.stage, 2)

    def test_mixed_setbacks_use_maximum_not_sum_in_either_order(self):
        for sequence in [("repeat", "dont_know", "repeat"), ("dont_know", "repeat", "dont_know")]:
            with self.subTest(sequence=sequence):
                word = Word.objects.create(chunk="mixed", stage=4)
                learning.start_or_resume()
                for choice in sequence:
                    self.respond(choice)
                self.respond()
                word.refresh_from_db()
                self.assertEqual(word.stage, 2)

    def test_setbacks_stop_at_first_stage_and_retry_success_does_not_advance(self):
        for initial in [0, 1]:
            word = Word.objects.create(chunk="new", stage=initial)
            learning.start_or_resume()
            self.respond("dont_know")
            self.respond("know")
            word.refresh_from_db()
            self.assertEqual(word.stage, 0)
            self.assertEqual(timezone.localtime(word.due_at).date(), timezone.localdate() + timedelta(days=1))

    def test_retry_returns_after_all_other_pending_cards(self):
        words = [Word.objects.create(chunk=f"word {index}") for index in range(5)]
        learning.start_or_resume()
        session = self.respond("repeat")
        self.assertEqual(session.queue, [words[1].pk, words[2].pk, words[3].pk, words[4].pk, words[0].pk])
        for index in [1, 2, 3, 4]:
            self.assertEqual(learning.active_session().queue[0], words[index].pk)
            self.respond()
        self.assertEqual(learning.active_session().queue[0], words[0].pk)

    def test_retry_goes_to_end_of_short_queue(self):
        first = Word.objects.create(chunk="first")
        second = Word.objects.create(chunk="second")
        learning.start_or_resume()
        self.assertEqual(self.respond("dont_know").queue, [second.pk, first.pk])

    def test_double_submission_cannot_apply_to_the_next_card(self):
        first = Word.objects.create(chunk="first")
        second = Word.objects.create(chunk="second")
        session = learning.start_or_resume()
        old_token = session.response_token
        learning.reveal(old_token)
        learning.answer(old_token, "know")
        learning.answer(old_token, "know")
        learning.reveal(old_token)
        first.refresh_from_db()
        second.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual((first.stage, second.stage), (1, 0))
        self.assertEqual(session.queue, [second.pk])
        self.assertFalse(session.revealed)

    def test_duplicate_retry_token_cannot_answer_the_same_card_again(self):
        word = Word.objects.create(chunk="only word", stage=4)
        session = learning.start_or_resume()
        learning.reveal(session.response_token)
        old_token = session.response_token
        learning.answer(old_token, "repeat")
        learning.answer(old_token, "dont_know")
        word.refresh_from_db()
        self.assertEqual(word.stage, 3)
        self.assertFalse(learning.active_session().revealed)

    def test_resume_from_another_browser_preserves_setback_limit(self):
        word = Word.objects.create(chunk="remember", stage=4)
        initial = learning.start_or_resume()
        self.respond("repeat")
        reopened = Client()
        reopened.cookies = self.client.cookies.copy()
        self.assertRedirects(reopened.post(reverse("study-start")), reverse("study"))
        session = learning.active_session()
        self.assertEqual(session.pk, initial.pk)
        self.respond("repeat")
        self.respond("know")
        word.refresh_from_db()
        self.assertEqual(word.stage, 3)

    def test_failed_session_save_rolls_back_word_progress_too(self):
        word = Word.objects.create(chunk="atomic")
        session = learning.start_or_resume()
        learning.reveal(session.response_token)
        with patch.object(StudySession, "save", side_effect=RuntimeError("simulated database failure")):
            with self.assertRaises(RuntimeError):
                learning.answer(session.response_token, "know")
        word.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(word.stage, 0)
        self.assertEqual(session.queue, [word.pk])
        self.assertTrue(session.revealed)

    def test_deleted_current_word_is_skipped_and_stale_form_cannot_answer_replacement(self):
        first = Word.objects.create(chunk="delete me")
        second = Word.objects.create(chunk="keep me")
        session = learning.start_or_resume()
        learning.reveal(session.response_token)
        old_token = session.response_token
        self.client.post(reverse("word-delete", args=[first.pk]))
        response = self.client.get(reverse("study"))
        self.assertContains(response, "keep me")
        learning.answer(old_token, "know")
        second.refresh_from_db()
        self.assertEqual(second.stage, 0)
        self.assertFalse(learning.active_session().revealed)

    def test_deleting_last_pending_word_leaves_session_waiting(self):
        word = Word.objects.create(chunk="delete")
        learning.start_or_resume()
        self.client.post(reverse("word-delete", args=[word.pk]))
        self.assertContains(self.client.get(reverse("study")), "Well done!")
        self.assertEqual(learning.active_session().queue, [])

    def test_only_one_open_session_allowed_by_database(self):
        Word.objects.create(chunk="one")
        learning.start_or_resume()
        with self.assertRaises(IntegrityError), transaction.atomic():
            StudySession.objects.create()

    def test_default_and_settings_validation(self):
        self.assertEqual(LearningSettings.objects.get(pk=1).review_target, 5)
        for value in ["0", "11", "", "1.5", "abc"]:
            with self.subTest(value=value):
                response = self.client.post(reverse("learning-settings"), {"review_target": value})
                self.assertEqual(response.status_code, 200)
                self.assertIn("review_target", response.context["form"].errors)
                self.assertEqual(LearningSettings.objects.get(pk=1).review_target, 5)
        for target in [1, 10]:
            self.assertRedirects(self.client.post(reverse("learning-settings"), {"review_target": target}), reverse("learning-settings-saved"))
            self.assertEqual(LearningSettings.objects.get(pk=1).review_target, target)

    def test_settings_changes_do_not_rewrite_words_or_dates(self):
        due_at = timezone.now() + timedelta(days=7)
        active = Word.objects.create(chunk="active", stage=4, due_at=due_at)
        archived = Word.objects.create(chunk="learned", stage=5, archived_at=timezone.now())
        self.client.post(reverse("learning-settings"), {"review_target": "1"})
        active.refresh_from_db()
        archived.refresh_from_db()
        self.assertIsNone(active.archived_at)
        self.assertEqual(active.due_at, due_at)
        self.assertEqual(active.stage, 4)
        self.assertIsNotNone(archived.archived_at)

    def test_invalid_settings_on_saved_page_do_not_claim_success(self):
        response = self.client.post(reverse("learning-settings-saved"), {"review_target": "0"})
        self.assertIn("review_target", response.context["form"].errors)
        self.assertNotContains(response, "Settings saved.")

    def test_changed_target_applies_to_next_answer_in_existing_session(self):
        word = Word.objects.create(chunk="learn me")
        learning.start_or_resume()
        self.client.post(reverse("learning-settings"), {"review_target": "1"})
        self.respond()
        word.refresh_from_db()
        self.assertIsNotNone(word.archived_at)

    def test_target_ten_does_not_archive_early_and_restored_words_start_fresh(self):
        learning.change_target(10)
        word = Word.objects.create(chunk="keep learning", stage=8)
        learning.start_or_resume()
        self.respond()
        word.refresh_from_db()
        self.assertEqual(word.stage, 9)
        self.assertIsNone(word.archived_at)
        with patch("cards.learning.timezone.now", return_value=word.due_at):
            learning.start_or_resume()
            self.respond()
        word.refresh_from_db()
        self.assertIsNotNone(word.archived_at)
        self.client.post(reverse("word-restore", args=[word.pk]))
        fresh = learning.start_or_resume()
        self.assertEqual(fresh.initial_stages[str(word.pk)], 0)

    def test_study_and_settings_routes_require_login(self):
        anonymous = Client()
        for name in ["study", "study-start", "study-reveal", "study-answer", "learning-settings", "learning-settings-saved"]:
            for method in [anonymous.get, anonymous.post]:
                with self.subTest(name=name, method=method.__name__):
                    self.assertRedirects(method(reverse(name)), reverse("login"))

    def test_study_mutations_are_post_only_and_enforce_csrf(self):
        browser = Client(enforce_csrf_checks=True)
        browser.cookies = self.client.cookies.copy()
        for name in ["study-start", "study-reveal", "study-answer"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 405)
            self.assertEqual(browser.post(reverse(name)).status_code, 403)
        self.assertEqual(browser.post(reverse("learning-settings"), {"review_target": 1}).status_code, 403)
        self.assertEqual(StudySession.objects.count(), 0)

    def test_invalid_actions_do_not_mutate_progress(self):
        word = Word.objects.create(chunk="safe")
        session = learning.start_or_resume()
        for name, payload in [
            ("study-reveal", {}),
            ("study-answer", {"response_token": "broken", "answer": "know"}),
            ("study-answer", {"response_token": session.response_token, "answer": "invented"}),
        ]:
            self.assertEqual(self.client.post(reverse(name), payload).status_code, 400)
        learning.reveal(uuid.uuid4())
        word.refresh_from_db()
        self.assertEqual(word.stage, 0)
        self.assertFalse(learning.active_session().revealed)

    def test_full_http_flow_and_summary(self):
        Word.objects.create(chunk="practice", meaning="a useful habit")
        self.assertRedirects(self.client.post(reverse("study-start")), reverse("study"))
        session = learning.active_session()
        self.assertRedirects(self.client.post(reverse("study-reveal"), {"response_token": session.response_token}), reverse("study"))
        self.assertRedirects(self.client.post(reverse("study-answer"), {"response_token": session.response_token, "answer": "know"}), reverse("study"))
        self.assertContains(self.client.get(reverse("study")), "Well done!")
        self.assertNotContains(self.client.get(reverse("study")), "Your next review is")


    def test_reset_during_session_invalidates_old_answer_and_restarts_baseline(self):
        word = Word.objects.create(chunk="reset me", stage=3)
        session = learning.start_or_resume()
        learning.reveal(session.response_token)
        old_token = session.response_token
        learning.answer(old_token, "repeat")
        session.refresh_from_db()
        learning.reveal(session.response_token)
        stale_token = session.response_token
        learning.reset_word(word.pk)
        learning.answer(stale_token, "know")
        word.refresh_from_db()
        self.assertEqual(word.stage, 0)
        session.refresh_from_db()
        self.assertEqual(session.initial_stages[str(word.pk)], 0)
        self.assertNotIn(str(word.pk), session.setbacks)
        self.assertFalse(session.revealed)
        learning.reveal(session.response_token)
        learning.answer(session.response_token, "know")
        word.refresh_from_db()
        self.assertEqual(word.stage, 1)

    def test_bulk_delete_prunes_queue_and_stale_answer_cannot_apply(self):
        first = Word.objects.create(chunk="first")
        second = Word.objects.create(chunk="second")
        session = learning.start_or_resume()
        learning.reveal(session.response_token)
        old_token = session.response_token
        self.client.post(reverse("word-bulk"), {"word_ids": [first.pk], "action": "delete", "confirmed": "yes"})
        session.refresh_from_db()
        self.assertEqual(session.queue, [second.pk])
        self.assertNotEqual(session.response_token, old_token)
        learning.answer(old_token, "know")
        second.refresh_from_db()
        self.assertEqual(second.stage, 0)
