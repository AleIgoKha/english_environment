"""Test PIN and session behavior with a disposable example PIN, not a secret."""
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.hashers import PBKDF2PasswordHasher
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import LoginGuard

TEST_PIN = "1234"
TEST_HASH = PBKDF2PasswordHasher().encode(TEST_PIN, "disposable-test-salt")


@override_settings(APP_PIN_HASH=TEST_HASH)
class PinLoginTests(TestCase):
    def login(self, client=None, pin=TEST_PIN):
        return (client or self.client).post(reverse("login"), {"pin": pin})

    def test_home_requires_login(self):
        self.assertRedirects(self.client.get(reverse("home")), reverse("login"))

    def test_login_page_has_numeric_password_input_and_no_cache(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, 'type="password"')
        self.assertContains(response, 'inputmode="numeric"')
        self.assertIn("no-store", response["Cache-Control"])

    def test_valid_pin_remembers_login_for_thirty_days(self):
        self.assertRedirects(self.login(), reverse("home"))
        session = self.client.session
        self.assertIn("pin_version", session)
        self.assertEqual(session.get_expiry_age(), 30 * 24 * 60 * 60)
        self.assertEqual(settings.SESSION_ENGINE, "django.contrib.sessions.backends.db")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Make room")
        self.assertIn("no-store", response["Cache-Control"])

    def test_cookie_can_resume_with_a_new_client(self):
        self.login()
        reopened = Client()
        reopened.cookies = self.client.cookies.copy()
        self.assertEqual(reopened.get(reverse("home")).status_code, 200)

    def test_failed_pin_is_counted_and_never_echoed(self):
        response = self.login(pin="9876")
        self.assertEqual(response.status_code, 400)
        self.assertNotContains(response, "9876", status_code=400)
        self.assertEqual(LoginGuard.objects.get(pk=1).failed_attempts, 1)
        self.assertNotIn("pin_version", self.client.session)

    def test_server_rejects_non_four_digit_pins(self):
        for pin in ["123", "12345", "abcd", "１２３４"]:
            with self.subTest(pin=pin):
                self.assertEqual(self.login(pin=pin).status_code, 400)
        self.assertEqual(LoginGuard.objects.get(pk=1).failed_attempts, 4)

    def test_fifth_failure_locks_out_even_the_correct_pin_and_other_clients(self):
        for attempt in range(4):
            self.assertEqual(self.login(pin="0000").status_code, 400)
        response = self.login(pin="0000")
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response["Retry-After"]), 0)
        blocked_until = LoginGuard.objects.get(pk=1).blocked_until
        self.assertEqual(self.login(client=Client()).status_code, 429)
        guard = LoginGuard.objects.get(pk=1)
        self.assertEqual(guard.blocked_until, blocked_until)
        self.assertEqual(guard.failed_attempts, 5)

    def test_lockout_expires_after_fifteen_minutes(self):
        now = timezone.now()
        with patch("cards.security.timezone.now", return_value=now):
            for attempt in range(5):
                self.login(pin="0000")
        self.assertEqual(LoginGuard.objects.get(pk=1).blocked_until, now + timedelta(minutes=15))
        with patch("cards.security.timezone.now", return_value=now + timedelta(minutes=15, seconds=1)):
            self.assertRedirects(self.login(), reverse("home"))
        guard = LoginGuard.objects.get(pk=1)
        self.assertEqual(guard.failed_attempts, 0)
        self.assertIsNone(guard.blocked_until)

    def test_success_clears_consecutive_failure_count(self):
        self.login(pin="0000")
        self.login()
        self.assertEqual(LoginGuard.objects.get(pk=1).failed_attempts, 0)

    def test_expired_lockout_starts_a_fresh_failure_count(self):
        LoginGuard.objects.filter(pk=1).update(
            failed_attempts=5, blocked_until=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(self.login(pin="0000").status_code, 400)
        guard = LoginGuard.objects.get(pk=1)
        self.assertEqual(guard.failed_attempts, 1)
        self.assertIsNone(guard.blocked_until)

    def test_logout_requires_post_and_revokes_session(self):
        self.login()
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertRedirects(self.client.post(reverse("logout")), reverse("login"))
        self.assertRedirects(self.client.get(reverse("home")), reverse("login"))

    def test_pin_rotation_revokes_remembered_login(self):
        self.login()
        replacement = PBKDF2PasswordHasher().encode("5678", "another-test-salt")
        with override_settings(APP_PIN_HASH=replacement):
            self.assertRedirects(self.client.get(reverse("home")), reverse("login"))

    def test_missing_or_invalid_pin_configuration_fails_closed(self):
        for encoded in ["", "invalid", "pbkdf2_sha256$broken"]:
            with self.subTest(encoded=encoded), override_settings(APP_PIN_HASH=encoded):
                self.assertEqual(self.login().status_code, 503)
                self.assertRedirects(
                    self.client.get(reverse("home")), reverse("login"),
                    fetch_redirect_response=False,
                )
        self.assertEqual(LoginGuard.objects.get(pk=1).failed_attempts, 0)

    def test_login_and_logout_enforce_csrf(self):
        browser = Client(enforce_csrf_checks=True)
        self.assertEqual(self.login(client=browser).status_code, 403)
        browser.get(reverse("login"))
        token = browser.cookies["csrftoken"].value
        response = browser.post(reverse("login"), {"pin": TEST_PIN, "csrfmiddlewaretoken": token})
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(browser.post(reverse("logout")).status_code, 403)
        token = browser.cookies["csrftoken"].value
        self.assertRedirects(
            browser.post(reverse("logout"), {"csrfmiddlewaretoken": token}), reverse("login")
        )

    def test_successful_login_rotates_session_id(self):
        session = self.client.session
        session["old"] = "discard-me"
        session.save()
        old_key = session.session_key
        self.login()
        self.assertNotEqual(self.client.session.session_key, old_key)
        self.assertNotIn("old", self.client.session)

    def test_authenticated_login_page_redirects_home(self):
        self.login()
        self.assertRedirects(self.client.get(reverse("login")), reverse("home"))
