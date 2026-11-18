import os
from unittest import mock

from django.urls import reverse

from .factories import DEADLOCK, HASHING, TREES, MediaTestCase, make_pdf, upload
from .models import Note


class ThumbnailTests(MediaTestCase):
    def post_upload(self, title, data, filename):
        return self.client.post(reverse("note_create"), {
            "title": title, "subject": self.subject.pk, "description": "", "tags": "", "file": upload(filename, data),
        })

    def setUp(self):
        super().setUp()
        self.login(self.alice)

    def test_pdf_upload_generates_a_png_thumbnail(self):
        self.assertEqual(self.post_upload("Pdf note", make_pdf(TREES), "n.pdf").status_code, 302)
        note = Note.objects.get(title="Pdf note")
        self.assertTrue(note.thumbnail)
        self.assertTrue(os.path.exists(note.thumbnail.path))
        self.assertEqual(note.thumbnail.read()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertTrue(note.is_pdf)

    def test_cards_show_thumbnail_or_placeholder(self):
        self.post_upload("Pdf note", make_pdf(TREES), "n.pdf")
        self.post_upload("Text note", DEADLOCK.encode(), "n.txt")
        html = self.client.get(reverse("note_list"), {"partial": 1}).content.decode()
        pdf = Note.objects.get(title="Pdf note")
        self.assertIn(pdf.thumbnail.url, html)
        self.assertEqual(html.count("thumb-ph"), 1)  # the txt note gets the placeholder icon
        self.assertIn("<b>TXT</b>", html)

    def test_thumbnail_failure_does_not_block_the_upload(self):
        data = make_pdf(TREES)  # build the PDF before pymupdf.open is mocked
        with mock.patch("pymupdf.open", side_effect=RuntimeError("boom")):
            r = self.post_upload("Pdf note", data, "n.pdf")
        self.assertEqual(r.status_code, 302)
        note = Note.objects.get(title="Pdf note")
        self.assertFalse(note.thumbnail)
        self.assertTrue(note.file)
        self.assertIn("inorder", note.extracted_text)  # text extraction still worked

    def test_corrupt_pdf_still_uploads(self):
        self.assertEqual(self.post_upload("Broken", b"%PDF-1.4 not really", "b.pdf").status_code, 302)
        self.assertFalse(Note.objects.get(title="Broken").thumbnail)
        self.assertContains(self.client.get(reverse("note_list")), "thumb-ph")

    def test_non_pdf_has_no_thumbnail(self):
        self.post_upload("Image", b"\x89PNG\r\n\x1a\n....", "i.png")
        self.assertFalse(Note.objects.get(title="Image").thumbnail)

    def test_thumbnail_removed_when_note_deleted_and_replaced_when_file_changes(self):
        self.post_upload("Pdf note", make_pdf(TREES), "n.pdf")
        note = Note.objects.get(title="Pdf note")
        old_thumb = note.thumbnail.path
        r = self.client.post(reverse("note_update", args=[note.pk]), {
            "title": "Pdf note", "subject": self.subject.pk, "description": "", "tags": "",
            "file": upload("plain.txt", DEADLOCK.encode()),
        })
        self.assertEqual(r.status_code, 302)
        note.refresh_from_db()
        self.assertFalse(note.thumbnail)  # replaced by a non-PDF: stale thumbnail dropped, row still saves
        self.assertFalse(os.path.exists(old_thumb))

        self.post_upload("Another", make_pdf(HASHING), "a.pdf")
        other = Note.objects.get(title="Another")
        path = other.thumbnail.path
        other.delete()
        self.assertFalse(os.path.exists(path))

    def test_replacing_a_pdf_with_another_pdf_regenerates_the_thumbnail(self):
        self.post_upload("Pdf note", make_pdf(TREES), "n.pdf")
        note = Note.objects.get(title="Pdf note")
        old = note.thumbnail.path
        self.client.post(reverse("note_update", args=[note.pk]), {
            "title": "Pdf note", "subject": self.subject.pk, "description": "", "tags": "",
            "file": upload("n2.pdf", make_pdf("Totally different")),
        })
        note.refresh_from_db()
        self.assertTrue(note.thumbnail)
        self.assertNotEqual(note.thumbnail.path, old)
        self.assertFalse(os.path.exists(old))


class PreviewTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.pdf = self.make_note("P", filename="p.pdf", data=make_pdf(TREES), downloads=3)
        self.txt = self.make_note("T", filename="t.txt", data=b"text")

    def test_preview_requires_login(self):
        r = self.client.get(reverse("note_preview", args=[self.pdf.pk]))
        self.assertEqual(r.status_code, 302)

    def test_preview_serves_inline_pdf_embeddable_in_same_origin_iframe(self):
        self.login(self.bob)
        r = self.client.get(reverse("note_preview", args=[self.pdf.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r["Content-Disposition"].startswith("inline"))
        self.assertEqual(r["X-Frame-Options"], "SAMEORIGIN")
        self.assertTrue(b"".join(r.streaming_content).startswith(b"%PDF"))
        self.pdf.refresh_from_db()
        self.assertEqual(self.pdf.downloads, 3)  # previewing is not a download

    def test_non_pdf_and_missing_files_return_404(self):
        self.login(self.bob)
        self.assertEqual(self.client.get(reverse("note_preview", args=[self.txt.pk])).status_code, 404)
        os.remove(self.pdf.file.path)
        self.assertEqual(self.client.get(reverse("note_preview", args=[self.pdf.pk])).status_code, 404)

    def test_other_pages_still_refuse_framing(self):
        self.login(self.bob)
        self.assertEqual(self.client.get(reverse("note_detail", args=[self.pdf.pk]))["X-Frame-Options"], "DENY")

    def test_detail_page_embeds_preview_only_for_pdfs(self):
        self.login(self.bob)
        html = self.client.get(reverse("note_detail", args=[self.pdf.pk])).content.decode()
        self.assertIn("<iframe", html)
        self.assertIn(reverse("note_preview", args=[self.pdf.pk]), html)
        self.assertNotIn("<iframe", self.client.get(reverse("note_detail", args=[self.txt.pk])).content.decode())

    def test_anonymous_sees_login_prompt_instead_of_iframe(self):
        r = self.client.get(reverse("note_detail", args=[self.pdf.pk]))
        self.assertNotContains(r, "<iframe")
        self.assertContains(r, "to preview this PDF")
