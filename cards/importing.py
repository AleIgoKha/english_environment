"""Validate CSV text without writing uploaded files or private exports to disk."""
import csv
from datetime import timedelta
import io

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from . import learning
from .forms import WordForm
from .duplicates import classify_rows
from .models import Word, WordImport

MAX_BYTES = 2 * 1024 * 1024
MAX_WORDS = 1000
PREVIEW_LIFETIME = timedelta(hours=24)
COLUMNS = {
    "obverse": "chunk", "chunk": "chunk", "front": "chunk",
    "reverse": "meaning", "meaning": "meaning", "back": "meaning",
    "extra": "example", "example": "example", "hint": "example",
}


@sensitive_variables()
def parse_csv(upload):
    raw = upload.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValidationError("Choose a CSV smaller than 2 MB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValidationError("Save the CSV with UTF-8 encoding and try again.") from None
    try:
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(text, newline=""), dialect=dialect, strict=True)
        headers = next(reader, [])
        fields = [COLUMNS.get(header.strip().lower()) for header in headers]
        if "chunk" not in fields:
            raise ValidationError("The CSV needs an Obverse, Chunk or front column.")
        for field in ("chunk", "meaning", "example"):
            if fields.count(field) > 1:
                raise ValidationError("Use only one column for each of Obverse, Reverse and Extra, including older header names.")
        rows = []
        for values in reader:
            if not any(value.strip() for value in values):
                continue
            if len(rows) >= MAX_WORDS:
                raise ValidationError("Import no more than 1,000 words at a time.")
            if len(values) > len(fields):
                raise ValidationError(f"Row {reader.line_num} has too many columns. Put text containing commas inside quotes.")
            values += [""] * (len(fields) - len(values))
            data = {field: value for field, value in zip(fields, values) if field}
            form = WordForm(data, check_duplicates=False)
            if not form.is_valid():
                details = " ".join(f"{form.fields[field].label}: {' '.join(errors)}" for field, errors in form.errors.items())
                raise ValidationError(f"Row {reader.line_num}: {details}")
            rows.append(dict(form.cleaned_data))
    except csv.Error:
        raise ValidationError("The CSV could not be read. Check its separators and quoted text.") from None
    if not rows:
        raise ValidationError("The CSV has no words to import.")
    return rows



@transaction.atomic
def confirm_import(import_id):
    # Serialize imports and study changes. A repeated confirmation cannot add twice.
    learning.lock_preferences()
    batch = WordImport.objects.select_for_update().get(pk=import_id)
    if batch.imported_at:
        return batch
    if batch.created_at < timezone.now() - PREVIEW_LIFETIME:
        raise ValidationError("This preview expired. Upload the CSV again.")
    # Check again under the shared lock: the deck may have changed since preview.
    rows = [row for row in classify_rows(batch.rows) if not row["duplicate"]]
    new_words = [Word(chunk=row["chunk"], meaning=row["meaning"], example=row["example"]) for row in rows]
    Word.objects.bulk_create(new_words)
    batch.added_count = len(new_words)
    batch.skipped_count = len(batch.rows) - len(new_words)
    batch.imported_at = timezone.now()
    batch.rows = []
    batch.save(update_fields=["added_count", "skipped_count", "imported_at", "rows"])
    return batch
