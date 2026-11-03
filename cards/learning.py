"""Small, server-side spaced-repetition engine. No background job is needed."""
from datetime import datetime, time, timedelta
import uuid

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import LearningSettings, StudySession, Word

REVIEW_GAPS = (1, 3, 7, 14, 30, 60, 120, 240, 365)


def lock_preferences():
    """Call inside a transaction, before any study or word-state mutation.

    The initial write takes a lock on both SQLite and PostgreSQL. Locking this
    singleton also prevents simultaneous Start requests from creating two queues.
    """
    LearningSettings.objects.filter(pk=1).update(review_target=F("review_target"))
    return LearningSettings.objects.select_for_update().get(pk=1)


def next_review(stage, now):
    days = REVIEW_GAPS[min(max(stage - 1, 0), len(REVIEW_GAPS) - 1)]
    local_zone = timezone.get_default_timezone()
    due_date = timezone.localtime(now, local_zone).date() + timedelta(days=days)
    return datetime.combine(due_date, time.min, tzinfo=local_zone)


def active_session():
    return StudySession.objects.filter(completed_at__isnull=True).first()


def clean_queue(session):
    """Discard words deleted outside studying, without losing other progress."""
    valid_ids = set(Word.objects.filter(pk__in=session.queue, archived_at__isnull=True).values_list("pk", flat=True))
    cleaned = [pk for pk in session.queue if pk in valid_ids]
    changed = False
    if cleaned != session.queue:
        changed = True
        session.queue = cleaned
        # A stale browser must not answer the replacement card using an old form.
        session.response_token = uuid.uuid4()
        session.revealed = False
    if not session.queue and not session.completed_at:
        session.completed_at = timezone.now()
        changed = True
    if changed:
        session.save(update_fields=["queue", "response_token", "revealed", "completed_at"])
    return session


@transaction.atomic
def start_or_resume():
    lock_preferences()
    session = active_session()
    if session:
        clean_queue(session)
        if not session.completed_at:
            return session
    now = timezone.now()
    due_words = list(Word.objects.filter(archived_at__isnull=True, due_at__lte=now).order_by("due_at", "pk").values_list("pk", "stage"))
    return StudySession.objects.create(
        queue=[pk for pk, stage in due_words],
        initial_stages={str(pk): stage for pk, stage in due_words},
        total_words=len(due_words),
        completed_at=None if due_words else now,
    )


@transaction.atomic
def study_state():
    lock_preferences()
    session = active_session()
    if session:
        clean_queue(session)
        if not session.completed_at:
            return session, Word.objects.get(pk=session.queue[0])
    return None, None


@transaction.atomic
def reveal(response_token):
    lock_preferences()
    session = active_session()
    if not session:
        return
    clean_queue(session)
    if not session.completed_at and session.response_token == response_token:
        session.revealed = True
        session.save(update_fields=["revealed"])


@transaction.atomic
def answer(response_token, choice):
    if choice not in {"know", "dont_know", "repeat"}:
        raise ValueError("Unknown study answer")
    preferences = lock_preferences()
    session = active_session()
    if not session:
        return
    clean_queue(session)
    if session.completed_at or session.response_token != response_token or not session.revealed:
        return

    word = Word.objects.get(pk=session.queue.pop(0))
    key = str(word.pk)
    initial_stage = session.initial_stages[key]
    setback = session.setbacks.get(key, 0)
    now = timezone.now()
    if choice == "know":
        word.stage = max(0, initial_stage - setback) if setback else initial_stage + 1
        if word.stage >= preferences.review_target:
            word.archived_at = now
    else:
        setback = max(setback, 1 if choice == "repeat" else 2)
        session.setbacks[key] = setback
        word.stage = max(0, initial_stage - setback)
        session.queue.insert(min(3, len(session.queue)), word.pk)

    word.due_at = next_review(word.stage, now)
    word.save(update_fields=["stage", "due_at", "archived_at", "updated_at"])
    session.revealed = False
    session.response_token = uuid.uuid4()
    if not session.queue:
        session.completed_at = now
    session.save()


@transaction.atomic
def change_target(target):
    preferences = lock_preferences()
    preferences.review_target = target
    preferences.full_clean()
    preferences.save(update_fields=["review_target"])
