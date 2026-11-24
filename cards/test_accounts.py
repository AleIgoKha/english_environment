"""Google account boundaries, including saved sessions and owner MCP access."""
import json
from unittest.mock import patch
from urllib.parse import urlencode
import uuid

from authlib.oauth2.rfc7636 import create_s256_code_challenge
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Account, LearningSettings, LOCAL_TEST_ACCOUNT_ID, OWNER_ACCOUNT_ID, StudyReview, StudySession, Word
from .test_mcp import TEST_TOKEN, TOKEN_HASH
from .test_mcp_write import WRITE_TOKEN, WRITE_HASH
from .tests import GOOGLE_SETTINGS


def google_sign_in(client, subject, email, verified=True):
    with patch("cards.auth_views.google_client") as google:
        google.return_value.authorize_access_token.return_value = {
            "userinfo": {"sub": subject, "email": email, "email_verified": verified},
        }
        return client.get(reverse("google-callback"))


@override_settings(**GOOGLE_SETTINGS)
class AccountEnrollmentTests(TestCase):
    def test_open_switch_requires_owner_binding_in_owner_only_mode(self):
        with override_settings(CARDY_PUBLIC_SIGN_IN=True):
            self.assertEqual(google_sign_in(self.client, "visitor", "visitor@example.test").status_code, 403)
            self.assertEqual(google_sign_in(self.client, "original", "owner@example.test").status_code, 403)
            self.assertEqual(self.client.get(reverse("health")).status_code, 503)
        self.assertRedirects(google_sign_in(self.client, "original", "owner@example.test"), reverse("home"))
        self.assertEqual(Account.objects.get(pk=OWNER_ACCOUNT_ID).google_subject, "original")
        self.assertEqual(google_sign_in(Client(), "imposter", "owner@example.test").status_code, 403)
        with override_settings(CARDY_PUBLIC_SIGN_IN=True):
            client = Client()
            self.assertEqual(google_sign_in(client, "unverified", "visitor@example.test", False).status_code, 403)
            self.assertRedirects(google_sign_in(client, "visitor", "owner@example.test"), reverse("home"))
            account_id = client.session["account_id"]
            self.assertNotEqual(account_id, str(OWNER_ACCOUNT_ID))
            self.assertRedirects(google_sign_in(client, "visitor", "updated@example.test"), reverse("home"))
            self.assertEqual(client.session["account_id"], account_id)
        self.assertRedirects(client.get(reverse("home")), reverse("login"))
        self.assertTrue(Account.objects.filter(pk=account_id).exists())
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)

    @override_settings(DEBUG=True, LOCAL_TEST_LOGIN=True)
    def test_local_test_login_uses_a_separate_account(self):
        Word.objects.create(account_id=OWNER_ACCOUNT_ID, chunk="Google owner's card")
        Word.objects.create(account_id=LOCAL_TEST_ACCOUNT_ID, chunk="local test card")
        self.assertRedirects(self.client.post(reverse("local-test-login")), reverse("home"))
        home = self.client.get(reverse("home"))
        self.assertContains(home, "local test card")
        self.assertNotContains(home, "Google owner's card")


@override_settings(**GOOGLE_SETTINGS, CARDY_MCP_TOKEN_SHA256=TOKEN_HASH, CARDY_MCP_WRITE_TOKEN_SHA256=WRITE_HASH,
                   CARDY_OAUTH_ENABLED=True)
class AccountIsolationTests(TestCase):
    def setUp(self):
        self.assertRedirects(google_sign_in(self.client, "owner", "owner@example.test"), reverse("home"))
        self.other = Client()
        self.enterContext(override_settings(CARDY_PUBLIC_SIGN_IN=True))
        self.assertRedirects(google_sign_in(self.other, "visitor", "visitor@example.test"), reverse("home"))
        self.other_id = self.other.session["account_id"]

    def test_lists_counts_direct_links_and_bulk_actions_stay_private(self):
        self.assertRedirects(self.client.post(reverse("word-add"), {"chunk": "same phrase"}), reverse("home"))
        private = Word.objects.get(account_id=OWNER_ACCOUNT_ID)
        learned = Word.objects.create(account_id=OWNER_ACCOUNT_ID, chunk="learned private", archived_at=timezone.now())
        deleted = Word.all_objects.create(account_id=OWNER_ACCOUNT_ID, chunk="deleted private", deleted_at=timezone.now())
        self.assertEqual(self.other.get(reverse("home")).context["total_count"], 0)
        for progress in ("all", "learned", "deleted", "to-learn"):
            listing = self.other.get(reverse("home"), {"progress": progress})
            self.assertEqual(listing.context["word_count"], 0)
        self.assertRedirects(self.other.post(reverse("word-add"), {"chunk": "same phrase"}), reverse("home"))
        own = Word.objects.get(account_id=self.other_id)
        for name, word, data in (
            ("word-edit", private, {"chunk": "changed"}),
            ("word-delete", private, {}),
            ("word-delete", deleted, {"permanent": "yes"}),
            ("word-restore", learned, {}),
            ("word-undelete", deleted, {}),
            ("word-reset", private, {}),
        ):
            with self.subTest(name=name, word=word.pk):
                self.assertEqual(self.other.post(reverse(name, args=[word.pk]), data).status_code, 404)
        self.assertEqual(self.other.get(reverse("word-edit", args=[private.pk])).status_code, 404)
        self.assertEqual(self.other.get(reverse("word-delete", args=[deleted.pk])).status_code, 404)
        response = self.other.post(reverse("word-bulk"), {
            "action": "delete", "word_ids": [own.pk, private.pk, deleted.pk],
            "confirmed": "yes", "deleted_ids": json.dumps([deleted.pk]),
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Word.objects.count(), 3)
        self.assertTrue(Word.all_objects.filter(pk=deleted.pk, deleted_at__isnull=False).exists())

    def test_import_ownership_also_applies_to_confirmation_and_duplicates(self):
        Word.objects.create(account_id=OWNER_ACCOUNT_ID, chunk="owner phrase")

        def upload():
            return SimpleUploadedFile("cards.csv", b"Obverse\nowner phrase\n")

        owner_url = self.client.post(reverse("word-import"), {"file": upload()})["Location"]
        self.assertEqual(self.client.get(owner_url).context["skipped_count"], 1)
        self.assertEqual(self.other.get(owner_url).status_code, 404)
        self.assertEqual(self.other.post(owner_url).status_code, 404)
        other_url = self.other.post(reverse("word-import"), {"file": upload()})["Location"]
        self.assertEqual(self.other.get(other_url).context["skipped_count"], 0)
        self.assertRedirects(self.other.post(other_url), other_url)
        self.assertTrue(Word.objects.filter(account_id=self.other_id, chunk="owner phrase").exists())

    def test_study_sessions_tokens_settings_and_undo_are_account_specific(self):
        owner_word = Word.objects.create(account_id=OWNER_ACCOUNT_ID, chunk="owner study", show_reverse_immediately=True)
        other_word = Word.objects.create(account_id=self.other_id, chunk="visitor study", show_reverse_immediately=True)
        self.client.post(reverse("learning-settings"), {"review_target": 3})
        self.other.post(reverse("learning-settings"), {"review_target": 8})
        self.assertEqual(LearningSettings.objects.get(account_id=OWNER_ACCOUNT_ID).review_target, 3)
        self.assertEqual(LearningSettings.objects.get(account_id=self.other_id).review_target, 8)
        for client, word in ((self.client, owner_word), (self.other, other_word)):
            self.assertContains(client.get(reverse("study")), word.chunk)
            self.assertEqual(StudySession.objects.get(account_id=word.account_id).queue, [word.pk])
        owner_token = self.client.get(reverse("study-status")).json()["response_token"]
        self.other.post(reverse("study-answer"), {"response_token": owner_token, "answer": "know"})
        self.assertFalse(StudyReview.objects.exists())
        self.client.post(reverse("study-answer"), {"response_token": owner_token, "answer": "know"})
        self.assertEqual(StudyReview.objects.get().word_id, owner_word.pk)
        undo_token = self.client.get(reverse("study-status")).json()["response_token"]
        self.other.post(reverse("study-undo"), {"response_token": undo_token})
        self.other.post(reverse("word-reset", args=[other_word.pk]))
        self.assertTrue(StudyReview.objects.filter(word=owner_word).exists())
        self.client.post(reverse("study-undo"), {"response_token": undo_token})
        owner_word.refresh_from_db()
        self.assertEqual(owner_word.stage, 0)

    def test_public_users_cannot_issue_mcp_tokens_and_bearers_use_owner_deck(self):
        Word.objects.create(account_id=self.other_id, chunk="visitor phrase")
        self.assertNotContains(self.other.get(reverse("learning-settings")), "Connect Codex")
        self.assertEqual(self.other.post(reverse("mcp-token-generate", args=["read"]), secure=True).status_code, 404)
        registered = self.client.post(reverse("oauth-register"), {
            "client_name": "Test client", "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
            "token_endpoint_auth_method": "none",
        }, content_type="application/json", secure=True)
        self.assertEqual(registered.status_code, 201)
        consent_url = reverse("oauth-authorize") + "?" + urlencode({
            "client_id": registered.json()["client_id"],
            "redirect_uri": "https://chatgpt.com/connector_platform_oauth_redirect",
            "response_type": "code", "scope": "cards:read", "resource": "https://testserver/mcp/",
            "code_challenge": create_s256_code_challenge("v" * 64), "code_challenge_method": "S256",
        })
        self.assertEqual(self.other.get(consent_url, secure=True).status_code, 403)
        self.assertEqual(self.other.post(consent_url, {"decision": "allow"}, secure=True).status_code, 403)

        def rpc(name, arguments, token):
            return self.client.post(reverse("mcp"), {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}",
                HTTP_ACCEPT="application/json, text/event-stream", secure=True).json()["result"]["structuredContent"]

        lookup = rpc("check_existing_cards", {"phrases": ["visitor phrase"]}, TEST_TOKEN)
        self.assertFalse(lookup["matches"][0]["exists"])
        result = rpc("add_cards", {
            "batch_id": str(uuid.uuid4()), "approved": True,
            "cards": [{"obverse": "visitor phrase", "reverse": "owner copy", "extra": ""}],
        }, WRITE_TOKEN)
        self.assertEqual(result["added_count"], 1)
        self.assertEqual(Word.objects.get(account_id=OWNER_ACCOUNT_ID).meaning, "owner copy")
        self.assertEqual(Word.objects.get(account_id=self.other_id).meaning, "")

    @override_settings(CARDY_MAINTENANCE_MODE=True)
    def test_maintenance_closes_browser_and_mcp_routes_but_allows_health(self):
        self.assertEqual(self.client.get(reverse("health")).status_code, 200)
        for route in ("word-add", "mcp", "oauth-token"):
            self.assertEqual(self.client.post(reverse(route)).status_code, 503)
        self.assertFalse(Word.objects.exists())
