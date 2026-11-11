"""Hash-only bearer credentials shared by all app workers; no secret retrieval."""
import hashlib
import secrets

from django.conf import settings
from django.db import transaction
from django.utils.crypto import constant_time_compare
from django.views.decorators.debug import sensitive_variables

from . import learning
from .models import McpBearerCredential
from .security import auth_version


def credential_hashes():
    hashes = {"read": settings.CARDY_MCP_TOKEN_SHA256, "write": settings.CARDY_MCP_WRITE_TOKEN_SHA256}
    version = auth_version()
    for credential in McpBearerCredential.objects.all():
        # A generated record replaces the legacy hash even after an owner change.
        hashes[credential.role] = (credential.token_hash
            if constant_time_compare(credential.owner_version, version) else "")
    return hashes


@sensitive_variables()
def rotate_token(role):
    if role not in ("read", "write"):
        raise ValueError("Unknown credential role.")
    token = secrets.token_urlsafe(48)
    with transaction.atomic():
        learning.lock_preferences()
        McpBearerCredential.objects.update_or_create(role=role, defaults={
            "token_hash": hashlib.sha256(token.encode()).hexdigest(), "owner_version": auth_version(),
        })
    return "Bearer " + token
