"""Disposable PostgreSQL tests, using the local test cluster only."""
import os

import dj_database_url

from .test_settings import *  # noqa: F403

test_url = os.environ.get("TEST_DATABASE_URL", "")
if not test_url:
    raise RuntimeError("Use scripts/test_postgres.ps1 to provide an isolated test cluster.")
DATABASES = {"default": dj_database_url.parse(test_url)}
