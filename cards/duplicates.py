"""Compare card fronts consistently without changing their saved spelling."""
import unicodedata

from .models import Word


def obverse_key(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def existing_obverses(account_id, exclude_pk=None):
    words = Word.all_objects.filter(account_id=account_id)
    if exclude_pk is not None:
        words = words.exclude(pk=exclude_pk)
    return {obverse_key(text) for text in words.values_list("chunk", flat=True)}


def classify_rows(account_id, rows):
    """Include learned/deleted cards and earlier rows in this batch."""
    seen = existing_obverses(account_id)
    classified = []
    for row in rows:
        key = obverse_key(row["chunk"])
        classified.append({**row, "duplicate": key in seen})
        seen.add(key)
    return classified
