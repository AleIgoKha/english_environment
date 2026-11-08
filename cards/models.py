import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class Word(models.Model):
    """A chunk and its learning state, shared by the app's single owner."""

    chunk = models.CharField(max_length=300)
    meaning = models.TextField(blank=True, max_length=2000)
    example = models.TextField(blank=True, max_length=4000)
    stage = models.PositiveSmallIntegerField(default=0)
    due_at = models.DateTimeField(default=timezone.now)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return self.chunk


class LearningSettings(models.Model):
    """One owner's preferences; also serializes study-session changes."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    review_target = models.PositiveSmallIntegerField(
        default=5, validators=[MinValueValidator(1), MaxValueValidator(10)],
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(review_target__gte=1, review_target__lte=10),
                name="review_target_between_1_and_10",
            ),
        ]


class WordImport(models.Model):
    """Temporary parsed CSV preview, stored privately in the database."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rows = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    imported_at = models.DateTimeField(null=True, blank=True)
    added_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)


class StudySession(models.Model):
    """The queue and per-word baselines survive browser closure and restarts."""

    queue = models.JSONField(default=list)
    initial_stages = models.JSONField(default=dict)
    setbacks = models.JSONField(default=dict)
    total_words = models.PositiveIntegerField(default=0)
    response_token = models.UUIDField(default=uuid.uuid4)
    revealed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                models.Value(1), condition=models.Q(completed_at__isnull=True),
                name="one_open_study_session",
            ),
        ]


class StudyReview(models.Model):
    """A reversible answer; snapshots contain learning state, never card text."""

    session = models.ForeignKey(StudySession, on_delete=models.CASCADE, related_name="reviews")
    word = models.ForeignKey(Word, on_delete=models.CASCADE)
    choice = models.CharField(max_length=10)
    word_before = models.JSONField()
    word_after = models.JSONField()
    session_before = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
