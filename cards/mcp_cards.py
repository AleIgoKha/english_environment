"""Add an explicitly approved batch with atomic duplicate checks and receipts."""
import hashlib
import json
import uuid

from django.db import transaction
from django.views.decorators.debug import sensitive_variables

from . import learning
from .duplicates import classify_rows
from .forms import WordForm
from .models import McpCardBatch, Word

MAX_CARDS = 100

ADD_TOOL = {
    "name": "add_cards",
    "title": "Add approved cards to Cardy",
    "description": (
        "Add 1–100 cards only after the user approves these exact Obverse, Reverse and Extra values "
        "in the current chat. Never call from a scheduled/background run. Keep batch_id unchanged "
        "when retrying the same approved batch. Existing Obverses (including Learned) are skipped, "
        "never overwritten. Requests with more than 100 cards are rejected before any cards are added. "
        "The chat may request a lower count. The 64 KiB JSON request limit also applies. "
        "Requires cards:add OAuth permission or the separate add-cards credential."
    ),
    "inputSchema": {
        "type": "object", "additionalProperties": False,
        "required": ["batch_id", "cards", "approved"],
        "properties": {
            "batch_id": {"type": "string", "format": "uuid"},
            "approved": {"type": "boolean", "const": True},
            "cards": {"type": "array", "minItems": 1, "maxItems": MAX_CARDS, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["obverse", "reverse", "extra"],
                "properties": {
                    "obverse": {"type": "string", "minLength": 1, "maxLength": 300},
                    "reverse": {"type": "string", "maxLength": 2000},
                    "extra": {"type": "string", "maxLength": 4000},
                },
            }},
        },
    },
    "annotations": {"readOnlyHint": False, "destructiveHint": False,
                    "idempotentHint": True, "openWorldHint": False},
}


@sensitive_variables()
def add_cards(arguments):
    if (not isinstance(arguments, dict) or set(arguments) != {"batch_id", "cards", "approved"}
            or arguments["approved"] is not True):
        raise ValueError("Explicit approval of the exact cards is required; provide batch_id, cards and approved=true.")
    try:
        batch_id = uuid.UUID(arguments["batch_id"])
    except (ValueError, TypeError, AttributeError):
        raise ValueError("batch_id must be a UUID; reuse it only for the same approved batch.") from None
    cards = arguments["cards"]
    if not isinstance(cards, list) or not 1 <= len(cards) <= MAX_CARDS:
        raise ValueError("Provide 1–100 approved cards. Requests with more than 100 cards are rejected.")
    rows = []
    for card in cards:
        if (not isinstance(card, dict) or set(card) != {"obverse", "reverse", "extra"}
                or any(not isinstance(value, str) for value in card.values())):
            raise ValueError("Each card needs only obverse, reverse and extra strings.")
        data = dict(zip(("chunk", "meaning", "example"), (card["obverse"], card["reverse"], card["extra"])))
        form = WordForm(data, check_duplicates=False)
        if not form.is_valid():
            raise ValueError("Invalid card: Obverse must be nonblank and at most 300 characters; Reverse 2,000; Extra 4,000.")
        rows.append(form.cleaned_data)
    payload_hash = hashlib.sha256(json.dumps(cards, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    with transaction.atomic():
        learning.lock_preferences()
        receipt = McpCardBatch.objects.filter(pk=batch_id).first()
        replayed = receipt is not None
        if receipt and receipt.payload_hash != payload_hash:
            raise ValueError("This batch_id belongs to different cards. A changed batch needs new approval and a new ID.")
        if not receipt:
            new_rows = [row for row in classify_rows(rows) if not row["duplicate"]]
            Word.objects.bulk_create([Word(chunk=row["chunk"], meaning=row["meaning"], example=row["example"])
                                      for row in new_rows])
            receipt = McpCardBatch.objects.create(id=batch_id, payload_hash=payload_hash,
                                                  added_count=len(new_rows), skipped_count=len(rows) - len(new_rows))
    return {"batch_id": str(receipt.pk), "added_count": receipt.added_count,
            "skipped_count": receipt.skipped_count, "replayed": replayed}
