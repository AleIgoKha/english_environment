"""Refresh local styles and scripts after edits; production uses hashed assets."""
from django.conf import settings


def local_asset_version(request):
    if not settings.DEBUG:
        return {}
    directory = settings.BASE_DIR / "cards" / "static" / "cards"
    version = max((directory / name).stat().st_mtime_ns for name in ("app.css", "cards.js", "study.js", "settings.js"))
    return {"local_asset_version": str(version)}
