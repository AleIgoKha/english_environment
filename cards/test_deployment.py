"""Deployment readiness, HTTPS proxy handling, and Home Screen assets."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
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
from scripts import apply_railway_config


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
    @override_settings(DEBUG=False, WHITENOISE_USE_FINDERS=True, WHITENOISE_AUTOREFRESH=True)
    def test_manifest_has_installable_content_type(self):
        response = self.client.get("/static/cards/manifest.webmanifest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"].split(";")[0], "application/manifest+json")

    def test_manifest_and_png_sizes(self):
        assets = Path(settings.BASE_DIR) / "cards" / "static" / "cards"
        manifest = json.loads((assets / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/")
        for name, size in [("apple-touch-icon.png", 180), ("icon-192.png", 192), ("icon-512.png", 512)]:
            data = (assets / name).read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", data[16:24]), (size, size))


class RailwayConfigurationTests(SimpleTestCase):
    def setUp(self):
        path = Path(settings.BASE_DIR) / "scripts" / "railway_service.json"
        profile = json.loads(path.read_text(encoding="utf-8"))
        self.expected = {key.split(".", 1)[1]: value for key, value in profile.items()}
        self.arguments = ["apply_railway_config", "--project", "project-test", "--service", "service-test", "--environment", "env-test"]

    def run_config(self, responses, extra=()):
        with patch("sys.argv", [*self.arguments, *extra]), redirect_stdout(io.StringIO()):
            with patch.object(apply_railway_config, "railway_api", side_effect=responses) as api:
                apply_railway_config.main()
                return api.call_args_list

    def test_wrong_project_cannot_mutate_a_service(self):
        with patch("sys.argv", self.arguments), redirect_stdout(io.StringIO()):
            with patch.object(apply_railway_config, "railway_api", return_value={"service": {"projectId": "other-project"}}) as api:
                with self.assertRaises(SystemExit):
                    apply_railway_config.main()
                self.assertEqual(api.call_count, 1)

    def test_saved_profile_mismatch_is_reported_as_failure(self):
        responses = [{"service": {"projectId": "project-test"}}, {"serviceInstanceUpdate": True}, {"serviceInstance": {**self.expected, "healthcheckPath": None}}]
        with self.assertRaises(SystemExit):
            self.run_config(responses)

    def test_existing_matching_trigger_is_not_duplicated(self):
        trigger = {"id": "trigger-test", "branch": "main", "repository": "owner/repo", "environmentId": "env-test"}
        responses = [{"service": {"projectId": "project-test"}}, {"serviceInstanceUpdate": True}, {"serviceInstance": self.expected}, {"service": {"repoTriggers": {"edges": [{"node": trigger}]}}}]
        calls = self.run_config(responses, ["--repository", "owner/repo"])
        self.assertEqual(len(calls), 4)

    def test_missing_trigger_is_created_for_exact_service_and_environment(self):
        trigger = {"id": "trigger-test", "branch": "main", "repository": "owner/repo", "environmentId": "env-test"}
        responses = [{"service": {"projectId": "project-test"}}, {"serviceInstanceUpdate": True}, {"serviceInstance": self.expected}, {"service": {"repoTriggers": {"edges": []}}}, {"deploymentTriggerCreate": trigger}]
        calls = self.run_config(responses, ["--repository", "owner/repo"])
        payload = calls[-1].args[1]["input"]
        self.assertEqual((payload["projectId"], payload["serviceId"], payload["environmentId"], payload["repository"], payload["branch"]), ("project-test", "service-test", "env-test", "owner/repo", "main"))


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
