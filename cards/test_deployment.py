"""Deployment readiness, HTTPS proxy handling, and Home Screen assets."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
import json
import struct

from django.conf import settings
from django.db import DatabaseError, connection, connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from . import learning
from .models import LearningSettings, StudySession, Word
from .tests import TEST_HASH


@override_settings(APP_PIN_HASH=TEST_HASH)
class HealthTests(TestCase):
    def test_health_is_public_and_reports_only_status(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertIn("no-store", response["Cache-Control"])

    def test_unconfigured_pin_fails_readiness(self):
        with override_settings(APP_PIN_HASH=""):
            response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})

    def test_missing_seed_data_fails_readiness(self):
        LearningSettings.objects.filter(pk=1).delete()
        self.assertEqual(self.client.get(reverse("health")).status_code, 503)

    def test_database_error_is_not_exposed(self):
        with patch("cards.views.Word.objects.exists", side_effect=DatabaseError("private connection details")):
            response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotContains(response, "private connection", status_code=503)

    @override_settings(SECURE_SSL_REDIRECT=True, SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
    def test_railway_https_does_not_loop_and_internal_health_can_use_http(self):
        self.assertEqual(self.client.get(reverse("login"), HTTP_X_FORWARDED_PROTO="https").status_code, 200)
        redirect = self.client.get(reverse("login"))
        self.assertEqual(redirect.status_code, 301)
        self.assertTrue(redirect["Location"].startswith("https://"))
        self.assertEqual(self.client.get(reverse("health")).status_code, 200)


class HomeScreenAssetTests(SimpleTestCase):
    def test_manifest_and_png_sizes(self):
        assets = Path(settings.BASE_DIR) / "cards" / "static" / "cards"
        manifest = json.loads((assets / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/")
        for name, size in [("apple-touch-icon.png", 180), ("icon-192.png", 192), ("icon-512.png", 512)]:
            data = (assets / name).read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", data[16:24]), (size, size))


@skipUnless(connection.vendor == "postgresql", "Real concurrent request checks require PostgreSQL")
class PostgresConcurrencyTests(TransactionTestCase):
    def setUp(self):
        LearningSettings.objects.get_or_create(pk=1)
        self.word = Word.objects.create(chunk="concurrent test")

    def run_together(self, operation):
        barrier = Barrier(2)

        def worker():
            try:
                barrier.wait(timeout=10)
                return operation()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            requests = [pool.submit(worker), pool.submit(worker)]
            return [request.result(timeout=20) for request in requests]

    def test_concurrent_start_creates_only_one_session(self):
        sessions = self.run_together(lambda: learning.start_or_resume().pk)
        self.assertEqual(sessions[0], sessions[1])
        self.assertEqual(StudySession.objects.count(), 1)

    def test_concurrent_duplicate_answer_advances_only_once(self):
        session = learning.start_or_resume()
        learning.reveal(session.response_token)
        self.run_together(lambda: learning.answer(session.response_token, "know"))
        self.word.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(self.word.stage, 1)
        self.assertEqual(session.queue, [])
        self.assertIsNotNone(session.completed_at)
