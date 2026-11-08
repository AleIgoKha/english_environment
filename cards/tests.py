"""Google owner access and safe local test sign-in."""
import logging
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from authlib.integrations.base_client.errors import MismatchingStateError
from requests import ConnectionError

from .auth_views import google_client
from .logging import RedactOAuthQuery
from .security import auth_version

GOOGLE_SETTINGS = {
    "GOOGLE_CLIENT_ID": "test-client", "GOOGLE_CLIENT_SECRET": "test-secret",
    "ALLOWED_GOOGLE_EMAIL": "owner@example.test", "LOCAL_TEST_LOGIN": False,
}


@override_settings(**GOOGLE_SETTINGS)
class GoogleLoginTests(TestCase):
    def finish_login(self, info=None):
        if info is None:
            info = {"sub": "owner-subject", "email": "owner@example.test", "email_verified": True}
        with patch("cards.auth_views.google_client") as client:
            client.return_value.authorize_access_token.return_value = {
                "userinfo": info, "access_token": "private-access", "id_token": "private-id",
            }
            return self.client.get(reverse("google-callback"), {"code": "temporary", "state": "state"})

    def test_anonymous_cannot_access_words(self):
        self.assertRedirects(self.client.get(reverse("home")), reverse("login"))
        self.assertRedirects(self.client.post(reverse("word-add"), {"chunk": "secret"}), reverse("login"))

    def test_login_is_google_only_and_uncached(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "Sign in with Google")
        self.assertNotContains(response, 'name="pin"')
        self.assertNotContains(response, "Continue as test user")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.client.post(reverse("login"), {"pin": "1234"}).status_code, 405)

    def test_verified_owner_can_resume_thirty_day_session(self):
        self.assertRedirects(self.finish_login(), reverse("home"))
        session = self.client.session
        self.assertEqual(session.get_expiry_age(), 30 * 24 * 60 * 60)
        self.assertEqual(session["google_subject"], "owner-subject")
        self.assertNotIn("private-access", str(dict(session)))
        self.assertNotIn("private-id", str(dict(session)))
        reopened = Client()
        reopened.cookies = self.client.cookies.copy()
        self.assertEqual(reopened.get(reverse("home")).status_code, 200)
        self.assertRedirects(reopened.get(reverse("login")), reverse("home"))

    def test_other_or_unverified_accounts_are_rejected(self):
        for info in [
            {"sub": "other", "email": "other@example.test", "email_verified": True},
            {"sub": "owner", "email": "owner@example.test", "email_verified": False},
            {"sub": "owner", "email": "owner@example.test", "email_verified": "true"},
            {"email": "owner@example.test", "email_verified": True},
            {},
        ]:
            with self.subTest(info=info):
                self.assertEqual(self.finish_login(info).status_code, 403)
                self.assertNotIn("auth_kind", self.client.session)

    def test_google_failures_are_generic_and_do_not_login(self):
        for error in [MismatchingStateError(), ConnectionError("private secret")]:
            with patch("cards.auth_views.google_client") as client:
                client.return_value.authorize_access_token.side_effect = error
                response = self.client.get(reverse("google-callback"))
            self.assertEqual(response.status_code, 403)
            self.assertNotContains(response, "private secret", status_code=403)
            self.assertNotIn("auth_kind", self.client.session)

    def test_changed_owner_or_credentials_revoke_sessions(self):
        for changed in [{"ALLOWED_GOOGLE_EMAIL": "different@example.test"}, {"GOOGLE_CLIENT_SECRET": "changed"}]:
            self.finish_login()
            with override_settings(**changed):
                self.assertRedirects(self.client.get(reverse("home")), reverse("login"))
                self.assertNotIn("auth_kind", self.client.session)

    def test_old_pin_session_does_not_authenticate(self):
        session = self.client.session
        session["pin_version"] = "old-pin-session"
        session.save()
        self.assertRedirects(self.client.get(reverse("home")), reverse("login"))

    def test_logout_requires_post_and_clears_session(self):
        self.finish_login()
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertRedirects(self.client.post(reverse("logout")), reverse("login"))
        self.assertRedirects(self.client.get(reverse("home")), reverse("login"))

    def test_login_actions_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        for name in ("google-login", "local-test-login", "logout"):
            self.assertEqual(client.post(reverse(name)).status_code, 403)
        self.assertEqual(client.get(reverse("google-login")).status_code, 405)

    def test_unconfigured_google_fails_closed(self):
        with override_settings(GOOGLE_CLIENT_SECRET=""):
            self.assertEqual(self.client.post(reverse("google-login")).status_code, 503)
            self.assertRedirects(self.client.get(reverse("google-callback")), reverse("login"))
            self.assertRedirects(self.client.get(reverse("home")), reverse("login"))

    def test_redirect_uses_state_nonce_pkce_and_exact_callback(self):
        metadata = {
            "issuer": "https://accounts.google.com",
            "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_endpoint": "https://oauth2.googleapis.com/token",
            "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
            "code_challenge_methods_supported": ["S256"],
        }
        provider = google_client()
        with patch.object(provider, "load_server_metadata", return_value=metadata):
            with patch("cards.auth_views.google_client", return_value=provider):
                response = self.client.post(reverse("google-login"))
        query = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(query["redirect_uri"], ["http://testserver/auth/google/callback/"])
        self.assertEqual(query["scope"], ["openid email"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertTrue(query["state"][0])
        self.assertTrue(query["nonce"][0])
        self.assertNotIn("test-secret", response["Location"])

    def test_log_filter_redacts_callback_codes(self):
        record = logging.LogRecord("django.server", logging.INFO, "", 0, '"GET %s HTTP/1.1" 302',
                                   ("/auth/google/callback/?code=private-code&state=private-state",), None)
        RedactOAuthQuery().filter(record)
        self.assertNotIn("private-code", record.getMessage())
        self.assertNotIn("private-state", record.getMessage())


    def test_real_oidc_validation_and_callback_replay(self):
        import time
        from joserfc import jwt
        from joserfc.jwk import RSAKey

        key = RSAKey.generate_key(2048, parameters={"kid": "test-key"})
        wrong_key = RSAKey.generate_key(2048, parameters={"kid": "test-key"})
        metadata = {
            "issuer": "https://accounts.google.com",
            "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_endpoint": "https://oauth2.googleapis.com/token",
            "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
            "id_token_signing_alg_values_supported": ["RS256"],
        }
        scenarios = [({}, False, False, 302), ({"aud": "wrong-client"}, False, False, 403),
                     ({"iss": "https://evil.example.test"}, False, False, 403),
                     ({"exp": int(time.time()) - 600}, False, False, 403),
                     ({"nonce": "wrong-nonce"}, False, False, 403),
                     ({}, True, False, 403), ({}, False, True, 403)]
        for changes, bad_signature, bad_state, status in scenarios:
            with self.subTest(changes=changes, bad_signature=bad_signature, bad_state=bad_state):
                self.client = Client()
                provider = google_client()
                with patch.object(provider, "load_server_metadata", return_value=metadata), patch.object(
                    provider, "fetch_jwk_set", return_value={"keys": [key.as_dict()]}
                ), patch("cards.auth_views.google_client", return_value=provider):
                    response = self.client.post(reverse("google-login"))
                    query = parse_qs(urlparse(response["Location"]).query)
                    now = int(time.time())
                    claims = {"iss": metadata["issuer"], "aud": "test-client", "sub": "owner-subject",
                              "email": "owner@example.test", "email_verified": True,
                              "iat": now, "exp": now + 300, "nonce": query["nonce"][0], **changes}
                    signed = jwt.encode({"alg": "RS256", "kid": "test-key"}, claims,
                                        wrong_key if bad_signature else key)
                    with patch.object(provider, "fetch_access_token", return_value={"id_token": signed, "access_token": "test-access"}) as exchange:
                        params = {"code": "one-use-code", "state": "invalid" if bad_state else query["state"][0]}
                        result = self.client.get(reverse("google-callback"), params)
                        self.assertEqual(result.status_code, status)
                        if bad_state:
                            exchange.assert_not_called()
                        else:
                            self.assertTrue(exchange.call_args.kwargs["code_verifier"])
                        if status == 302:
                            self.assertEqual(self.client.get(reverse("home")).status_code, 200)
                            exchange.reset_mock()
                            self.assertEqual(self.client.get(reverse("google-callback"), params).status_code, 403)
                            exchange.assert_not_called()
                        else:
                            self.assertNotIn("auth_kind", self.client.session)


@override_settings(DEBUG=True, LOCAL_TEST_LOGIN=True, RAILWAY_ENVIRONMENT_ID="")
class LocalTestLoginTests(TestCase):
    def test_button_and_persistent_local_session(self):
        self.assertContains(self.client.get(reverse("login")), "Continue as test user")
        self.assertRedirects(self.client.post(reverse("local-test-login")), reverse("home"))
        self.assertEqual(self.client.session["auth_kind"], "local-test")
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)
        reopened = Client()
        reopened.cookies = self.client.cookies.copy()
        self.assertEqual(reopened.get(reverse("home")).status_code, 200)

    def test_production_and_disabled_modes_cannot_use_test_login(self):
        for options in [{"DEBUG": False}, {"LOCAL_TEST_LOGIN": False}, {"RAILWAY_ENVIRONMENT_ID": "production"}]:
            with override_settings(**options):
                self.assertEqual(self.client.post(reverse("local-test-login")).status_code, 404)
                self.assertNotContains(self.client.get(reverse("login")), "Continue as test user")

    def test_remote_database_or_request_is_blocked(self):
        with patch("cards.security.settings") as config:
            config.DEBUG = True
            config.LOCAL_TEST_LOGIN = True
            config.RAILWAY_ENVIRONMENT_ID = ""
            config.DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "HOST": "remote.example.test"}}
            self.assertEqual(self.client.post(reverse("local-test-login")).status_code, 404)
        self.assertEqual(self.client.post(reverse("local-test-login"), REMOTE_ADDR="192.168.1.10").status_code, 404)

    def test_disabling_test_login_revokes_test_session(self):
        self.client.post(reverse("local-test-login"))
        with override_settings(LOCAL_TEST_LOGIN=False):
            self.assertRedirects(self.client.get(reverse("home")), reverse("login"))
            self.assertNotIn("auth_kind", self.client.session)

    @override_settings(LOCAL_TEST_NETWORK="192.168.0.0/24")
    def test_configured_wifi_clients_can_use_test_deck(self):
        self.assertContains(self.client.get(reverse("login"), REMOTE_ADDR="192.168.0.20"), "Continue as test user")
        self.assertRedirects(self.client.post(reverse("local-test-login"), REMOTE_ADDR="192.168.0.20"), reverse("home"))
        self.assertEqual(self.client.get(reverse("home"), REMOTE_ADDR="192.168.0.20").status_code, 200)
        self.assertEqual(self.client.get(reverse("home"), REMOTE_ADDR="192.168.1.20").status_code, 302)

    def test_wifi_test_login_requires_private_opt_in(self):
        for network in ("", "0.0.0.0/0", "8.8.8.0/24", "invalid", "192.168.0.1/24"):
            with self.subTest(network=network), override_settings(LOCAL_TEST_NETWORK=network):
                self.assertEqual(self.client.post(reverse("local-test-login"), REMOTE_ADDR="192.168.0.20").status_code, 404)
        with override_settings(LOCAL_TEST_NETWORK="192.168.0.0/24", DEBUG=False):
            self.assertEqual(self.client.post(reverse("local-test-login"), REMOTE_ADDR="192.168.0.20").status_code, 404)

    def test_post_and_csrf_are_required(self):
        self.assertEqual(self.client.get(reverse("local-test-login")).status_code, 405)
        self.assertEqual(Client(enforce_csrf_checks=True).post(reverse("local-test-login")).status_code, 403)
