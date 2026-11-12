"""Small, server-side spaced-repetition engine. No background job is needed."""
from datetime import datetime, time, timedelta, timezone as datetime_timezone
import uuid

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import LearningSettings, StudyReview, StudySession, Word

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


def clear_undo_history():
    """Call under the preferences lock when starting over or changing the deck."""
    StudyReview.objects.all().delete()


def can_undo(session):
    return bool(session and session.reviews.filter(
        Q(word__deleted_at__isnull=True) | Q(choice="delete", word__deleted_at__isnull=False)).exists())


def word_progress(word, include_deleted=False):
    def stored_time(value):
        return value.astimezone(datetime_timezone.utc).isoformat() if value else None

    progress = {"stage": word.stage, "due_at": stored_time(word.due_at), "archived_at": stored_time(word.archived_at)}
    if include_deleted:
        progress["deleted_at"] = stored_time(word.deleted_at)
    return progress


@transaction.atomic
def delete_words(word_ids, from_study=False):
    """Only deletion from Learning is reversible through the study Undo stack."""
    lock_preferences()
    words = list(Word.objects.filter(pk__in=word_ids))
    session = active_session()
    reversible = bool(from_study and len(words) == 1 and session and words[0].pk in session.queue)
    if reversible:
        word = words[0]
        before = word_progress(word)
        snapshot = {"queue": list(session.queue), "initial_stages": dict(session.initial_stages),
                    "setbacks": dict(session.setbacks)}
    ids = [word.pk for word in words]
    count, _ = Word.objects.filter(pk__in=ids).delete()
    if reversible:
        word.refresh_from_db()
        StudyReview.objects.create(session=session, word=word, choice="delete", word_before=before,
            word_after=word_progress(word, include_deleted=True), session_before=snapshot)
    else:
        # Forget actions for these cards only, preserving undo for other cards.
        StudyReview.objects.filter(word_id__in=ids).delete()
    if session:
        clean_queue(session)
    return count


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
    if changed:
        session.save(update_fields=["queue", "response_token", "revealed", "completed_at"])
    return session


def append_due_words(session):
    """Append eligible cards without disturbing the current card or pending retries."""
    clean_queue(session)
    due = Word.objects.filter(archived_at__isnull=True, due_at__lte=timezone.now()).exclude(pk__in=session.queue)
    incoming = list(due.order_by("due_at", "pk").values_list("pk", "stage"))
    if not incoming:
        return session
    was_empty = not session.queue
    for word_id, stage in incoming:
        key = str(word_id)
        session.queue.append(word_id)
        # A new scheduled review starts with a fresh baseline and no prior setback.
        session.initial_stages[key] = stage
        session.setbacks.pop(key, None)
    session.total_words = len(session.initial_stages)
    if was_empty:
        session.response_token = uuid.uuid4()
        session.revealed = False
    session.save(update_fields=["queue", "initial_stages", "setbacks", "total_words", "response_token", "revealed"])
    return session


def ongoing_session():
    """Call under the preferences lock; resume legacy completed sessions too."""
    session = active_session()
    if not session:
        session = StudySession.objects.order_by("-pk").first()
        if session:
            session.completed_at = None
            session.save(update_fields=["completed_at"])
        else:
            session = StudySession.objects.create()
    return append_due_words(session)


@transaction.atomic
def start_or_resume():
    lock_preferences()
    return ongoing_session()


@transaction.atomic
def study_state():
    lock_preferences()
    session = ongoing_session()
    word = Word.objects.get(pk=session.queue[0]) if session.queue else None
    if word and word.show_reverse_immediately and not session.revealed:
        session.revealed = True
        session.save(update_fields=["revealed"])
    return session, word


@transaction.atomic
def reveal(response_token):
    lock_preferences()
    session = active_session()
    if not session:
        return
    append_due_words(session)
    if session.queue and session.response_token == response_token:
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
    append_due_words(session)
    if not session.queue or session.response_token != response_token or not session.revealed:
        return

    word = Word.objects.get(pk=session.queue[0])
    before = word_progress(word)
    session_before = {
        "queue": list(session.queue),
        "initial_stages": dict(session.initial_stages),
        "setbacks": dict(session.setbacks),
    }
    session.queue.pop(0)
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
        session.queue.append(word.pk)

    word.due_at = next_review(word.stage, now)
    word.save(update_fields=["stage", "due_at", "archived_at", "updated_at"])
    session.revealed = False
    session.response_token = uuid.uuid4()
    session.completed_at = None
    session.save()
    StudyReview.objects.create(
        session=session, word=word, choice=choice, word_before=before,
        word_after=word_progress(word), session_before=session_before,
    )


@transaction.atomic
def undo(response_token):
    lock_preferences()
    # Only the newest session can be reopened; older browser tabs cannot undo it.
    session = StudySession.objects.order_by("-pk").first()
    if not session or session.response_token != response_token:
        return
    skipped = False
    while True:
        review = session.reviews.order_by("-pk").first()
        if not review:
            if skipped:
                clean_queue(session)
                session.response_token = uuid.uuid4()
                session.save(update_fields=["response_token"])
            return
        is_deletion = review.choice == "delete"
        word = (Word.all_objects if is_deletion else Word.objects).filter(pk=review.word_id).first()
        if word is not None and (not is_deletion or (
                word.deleted_at and word_progress(word, include_deleted=True) == review.word_after)):
            break
        # A deleted answer card, or a deletion already changed elsewhere, must
        # not hide valid earlier actions or bring the card back unexpectedly.
        review.delete()
        skipped = True
    if word_progress(word, include_deleted=is_deletion) != review.word_after:
        # Do not overwrite a word that has since been changed outside studying.
        clear_undo_history()
        session.response_token = uuid.uuid4()
        session.save(update_fields=["response_token"])
        return
    word.stage = review.word_before["stage"]
    word.due_at = parse_datetime(review.word_before["due_at"])
    archived = review.word_before["archived_at"]
    word.archived_at = parse_datetime(archived) if archived else None
    if is_deletion:
        word.deleted_at = None
    word.save(update_fields=["stage", "due_at", "archived_at", "deleted_at", "updated_at"])
    snapshot = review.session_before
    # Keep cards that arrived after this answer at the end of the restored queue.
    extras = [pk for pk in session.queue if pk not in snapshot["queue"]]
    candidates = snapshot["queue"] + extras
    valid_ids = set(Word.objects.filter(pk__in=candidates, archived_at__isnull=True).values_list("pk", flat=True))
    baselines = dict(snapshot["initial_stages"])
    setbacks = dict(snapshot["setbacks"])
    for pk in extras:
        key = str(pk)
        baselines[key] = session.initial_stages[key]
        if key in session.setbacks:
            setbacks[key] = session.setbacks[key]
        else:
            setbacks.pop(key, None)
    session.queue = [pk for pk in candidates if pk in valid_ids]
    session.initial_stages = baselines
    session.setbacks = setbacks
    session.completed_at = None
    session.revealed = False
    session.response_token = uuid.uuid4()
    session.save(update_fields=["queue", "initial_stages", "setbacks", "completed_at", "revealed", "response_token"])
    append_due_words(session)
    review.delete()


def reset_word(word_id):
    reset_words([word_id])


@transaction.atomic
def reset_words(word_ids):
    """Restart the selected words and invalidate study forms with old stages."""
    lock_preferences()
    clear_undo_history()
    now = timezone.now()
    Word.objects.filter(pk__in=word_ids).update(stage=0, archived_at=None, due_at=now, updated_at=now)
    session = active_session()
    affected = set(word_ids).intersection(session.queue) if session else set()
    if affected:
        for word_id in affected:
            key = str(word_id)
            session.initial_stages[key] = 0
            session.setbacks.pop(key, None)
        session.response_token = uuid.uuid4()
        session.revealed = False
        session.save(update_fields=["initial_stages", "setbacks", "response_token", "revealed"])


@transaction.atomic
def change_target(target):
    preferences = lock_preferences()
    preferences.review_target = target
    preferences.full_clean()
    preferences.save(update_fields=["review_target"])
