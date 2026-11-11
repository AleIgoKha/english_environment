import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from authlib.oauth2.rfc6749 import ClientMixin, AuthorizationCodeMixin


class Word(models.Model):
    """A chunk and its learning state, shared by the app's single owner."""

    chunk = models.CharField(max_length=300)
    meaning = models.TextField(blank=True, max_length=2000)
    example = models.TextField(blank=True, max_length=4000)
    show_reverse_immediately = models.BooleanField(default=False)
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


class McpCardBatch(models.Model):
    """Text-free receipt so retrying an approved MCP batch cannot add it twice."""

    id = models.UUIDField(primary_key=True, editable=False)
    payload_hash = models.CharField(max_length=64)
    added_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class McpBearerCredential(models.Model):
    """A generated role token overrides its legacy environment credential."""

    role = models.CharField(max_length=5, primary_key=True, choices=[("read", "Read"), ("write", "Write")])
    token_hash = models.CharField(max_length=64)
    owner_version = models.CharField(max_length=64)
    updated_at = models.DateTimeField(auto_now=True)


class McpOAuthClient(ClientMixin, models.Model):
    client_id = models.CharField(max_length=100, unique=True)
    client_name = models.CharField(max_length=100)
    redirect_uris = models.JSONField(default=list)
    registration_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def get_client_id(self):
        return self.client_id

    def get_default_redirect_uri(self):
        return self.redirect_uris[0]

    def check_redirect_uri(self, uri):
        return uri in self.redirect_uris

    def get_allowed_scope(self, scope):
        allowed = {"cards:read", "cards:add"}
        requested = (scope or "cards:read").split()
        return " ".join(requested) if set(requested) <= allowed else None

    def check_client_secret(self, secret):
        return False

    def check_endpoint_auth_method(self, method, endpoint):
        return method == "none"

    def check_response_type(self, response_type):
        return response_type == "code"

    def check_grant_type(self, grant_type):
        return grant_type in ("authorization_code", "refresh_token")


class McpOAuthCode(AuthorizationCodeMixin, models.Model):
    code_hash = models.CharField(max_length=64, primary_key=True)
    client = models.ForeignKey(McpOAuthClient, on_delete=models.CASCADE)
    redirect_uri = models.URLField(max_length=2048)
    scope = models.CharField(max_length=100)
    resource = models.URLField(max_length=500)
    owner_email = models.EmailField()
    owner_subject = models.CharField(max_length=255)
    owner_version = models.CharField(max_length=64)
    code_challenge = models.CharField(max_length=128)
    code_challenge_method = models.CharField(max_length=10, default="S256")
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    def get_redirect_uri(self):
        return self.redirect_uri

    def get_scope(self):
        return self.scope


class McpOAuthToken(models.Model):
    access_hash = models.CharField(max_length=64, unique=True)
    refresh_hash = models.CharField(max_length=64, unique=True)
    client = models.ForeignKey(McpOAuthClient, on_delete=models.CASCADE)
    family = models.UUIDField(default=uuid.uuid4)
    scope = models.CharField(max_length=100)
    resource = models.URLField(max_length=500)
    owner_email = models.EmailField()
    owner_subject = models.CharField(max_length=255)
    owner_version = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    refresh_expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def check_client(self, client):
        return self.client_id == client.pk

    def get_scope(self):
        return self.scope


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
