"""CSV preview, validation, privacy, duplicate handling and atomic confirmation."""
from datetime import timedelta
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import importing
from .models import Word, WordImport
from .security import auth_version
from .tests import GOOGLE_SETTINGS


@override_settings(**GOOGLE_SETTINGS)
class CsvImportTests(TestCase):
    def setUp(self):
        session = self.client.session
        session.update({"auth_kind": "google", "google_email": "owner@example.test", "google_subject": "owner", "auth_version": auth_version()})
        session.save()

    def upload(self, content, name="words.csv"):
        if isinstance(content, str):
            content = content.encode("utf-8")
        return self.client.post(reverse("word-import"), {"file": SimpleUploadedFile(name, content, content_type="text/csv")})

    def test_duocards_preview_is_readonly_then_confirmation_imports_mapping(self):
        response = self.upload('front,back,hint,publishedAt\nhello,a greeting,Hello there!,2026-01-01\nchunk only,,,\n')
        self.assertEqual(response.status_code, 302)
        url = response["Location"]
        self.assertEqual(Word.objects.count(), 0)
        preview = self.client.get(url)
        self.assertEqual(preview.context["add_count"], 2)
        self.assertContains(preview, "a greeting")
        self.assertNotContains(preview, "2026-01-01")
        self.assertEqual(Word.objects.count(), 0)
        self.assertRedirects(self.client.post(url), url)
        word = Word.objects.get(chunk="hello")
        self.assertEqual((word.meaning, word.example, word.stage), ("a greeting", "Hello there!", 0))
        self.assertIsNone(word.archived_at)
        self.assertLessEqual(word.due_at, timezone.now())
        self.assertEqual(Word.objects.get(chunk="chunk only").meaning, "")
        self.assertContains(self.client.get(url), "Added 2 words")
        batch = WordImport.objects.get()
        self.assertEqual(batch.rows, [])
        self.assertIsNotNone(batch.imported_at)
        self.client.post(url)
        self.assertEqual(Word.objects.count(), 2)

    def test_chunk_headers_utf8_quotes_newlines_and_optional_fields(self):
        response = self.upload('\ufeffChunk,Meaning,Example\n"take your time","не спеши, пожалуйста","First line\nSecond line"\nonly chunk\n')
        self.client.post(response["Location"])
        word = Word.objects.get(chunk="take your time")
        self.assertEqual(word.meaning, "не спеши, пожалуйста")
        self.assertEqual(word.example, "First line\nSecond line")
        self.assertEqual(Word.objects.get(chunk="only chunk").example, "")
        response = self.upload('Chunk\nfresh word\n')
        self.client.post(response["Location"])
        self.assertTrue(Word.objects.filter(chunk="fresh word").exists())

    def test_semicolon_csv_and_blank_lines(self):
        response = self.upload('Chunk;Meaning;Example\n\nhello;greeting;example\n')
        self.client.post(response["Location"])
        self.assertEqual(Word.objects.get().meaning, "greeting")

    def test_new_headers_preview_and_import_with_existing_limits(self):
        reverse_text = "r" * 2000
        extra_text = "e" * 4000
        response = self.upload(f'Obverse,Reverse,Extra\nwith a focus on clarity,{reverse_text},{extra_text}\n')
        preview = self.client.get(response["Location"])
        for label in ("Obverse", "Reverse", "Extra"):
            self.assertContains(preview, f'<th scope="col">{label}</th>')
        self.client.post(response["Location"])
        word = Word.objects.get()
        self.assertEqual((word.chunk, word.meaning, word.example),
                         ("with a focus on clarity", reverse_text, extra_text))
        self.assertEqual(self.upload(f'Obverse,Reverse\nhello,{reverse_text}r\n').status_code, 400)
        self.assertEqual(self.upload('Obverse,Chunk\nhello,hello\n').status_code, 400)
        self.assertEqual(Word.objects.count(), 1)

    def test_duplicates_preserve_existing_content_and_progress(self):
        existing = Word.objects.create(chunk="Take your time", meaning="original", stage=5, archived_at=timezone.now())
        response = self.upload('front,back\n TAKE   YOUR TIME ,replacement\nnew chunk,first\nNEW CHUNK,second\n')
        url = response["Location"]
        self.assertEqual(self.client.get(url).context["add_count"], 3)
        self.assertNotContains(self.client.get(url), '<th scope="col">Action</th>')
        self.client.post(url)
        self.assertEqual(Word.objects.count(), 4)
        existing.refresh_from_db()
        self.assertEqual((existing.meaning, existing.stage), ("original", 5))
        self.assertIsNotNone(existing.archived_at)
        self.assertEqual(Word.objects.get(chunk="new chunk").meaning, "first")

    def test_exact_duplicates_are_imported_without_changing_existing_word(self):
        response = self.upload('Chunk\nhello\nhello\n')
        existing = Word.objects.create(chunk="hello", stage=2)
        self.client.post(response["Location"])
        self.assertEqual(Word.objects.filter(chunk="hello").count(), 3)
        existing.refresh_from_db()
        self.assertEqual(existing.stage, 2)
        self.assertContains(self.client.get(response["Location"]), "Added 2 words")
        self.client.post(response["Location"])
        self.assertEqual(Word.objects.count(), 3)

    def test_invalid_csv_has_no_preview_or_partial_import(self):
        for content in ('', 'Meaning\nmissing chunk header\n', 'Chunk,Meaning\nvalid,ok\n,missing chunk\n',
                        'Chunk,Meaning\nhello,one,extra\n', 'Chunk,front\na,b\n',
                        'Chunk\n"unterminated', 'Chunk\n' + 'x' * 301):
            with self.subTest(content=content[:30]):
                response = self.upload(content)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.json()["errors"])
                self.assertFalse(WordImport.objects.exists())
                self.assertFalse(Word.objects.exists())
        response = self.upload(b'Chunk\n\xff')
        self.assertContains(response, "UTF-8 encoding", status_code=400)
        self.assertFalse(WordImport.objects.exists())

    def test_upload_and_row_limits(self):
        self.assertContains(self.upload('Chunk\nhello', name="words.txt"), "Choose a .csv", status_code=400)
        self.assertContains(self.upload(b'x' * (importing.MAX_BYTES + 1)), "smaller than 2 MB", status_code=400)
        with patch("cards.importing.MAX_WORDS", 2):
            self.assertContains(self.upload('Chunk\none\ntwo\nthree\n'), "no more than 1,000", status_code=400)
        self.assertFalse(WordImport.objects.exists())

    def test_preview_is_private_to_its_authenticated_browser_session(self):
        response = self.upload('Chunk\nprivate chunk\n')
        url = response["Location"]
        other = Client()
        session = other.session
        session.update(dict(self.client.session))
        session.pop("word_import_id")
        session.save()
        self.assertEqual(other.get(url).status_code, 404)
        self.assertEqual(other.post(url).status_code, 404)
        self.assertRedirects(Client().get(url), reverse("login"))
        self.assertRedirects(Client().post(reverse("word-import")), reverse("login"))
        self.assertEqual(Client(enforce_csrf_checks=True).post(url).status_code, 403)
        self.assertEqual(Client(enforce_csrf_checks=True).post(reverse("word-import")).status_code, 403)
        self.assertIn("no-store", self.client.get(url)["Cache-Control"])

    def test_expired_preview_cannot_import(self):
        response = self.upload('Chunk\nhello\n')
        WordImport.objects.update(created_at=timezone.now()-timedelta(days=2))
        self.assertContains(self.client.get(response["Location"]), "preview expired", status_code=410)
        self.assertEqual(self.client.post(response["Location"]).status_code, 410)
        self.assertFalse(Word.objects.exists())

    def test_import_failure_rolls_back_all_changes(self):
        self.upload('Chunk\none\ntwo\n')
        batch = WordImport.objects.get()
        def fail_after_first_word(words):
            words[0].save()
            raise RuntimeError("Simulated database failure")
        with patch("cards.importing.Word.objects.bulk_create", side_effect=fail_after_first_word):
            with self.assertRaises(RuntimeError):
                importing.confirm_import(batch.pk)
        self.assertFalse(Word.objects.exists())
        batch.refresh_from_db()
        self.assertIsNone(batch.imported_at)
        self.assertEqual(len(batch.rows), 2)

    def test_csv_content_is_escaped_in_preview(self):
        response = self.upload('Chunk,Meaning\n<script>alert(1)</script>,<img src=x>\n')
        preview = self.client.get(response["Location"])
        self.assertNotContains(preview, "<script>alert(1)</script>")
        self.assertContains(preview, "&lt;script&gt;")
        self.assertNotContains(preview, "<img src=x>")


    def test_direct_picker_upload_returns_preview_url_without_importing(self):
        self.assertRedirects(self.client.get(reverse("word-import")), reverse("home") + "?cards=open")
        response = self.client.post(reverse("word-import"), {
            "file": SimpleUploadedFile("words.csv", b"Chunk\nhello\n"),
        }, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertContains(self.client.get(response.json()["preview_url"]), "hello")
        self.assertFalse(Word.objects.exists())
        response = self.client.post(reverse("word-import"), {
            "file": SimpleUploadedFile("invalid.csv", b"Meaning\nmissing chunk header\n"),
        }, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.json()["errors"])
