from django.core.files.storage import default_storage
from django.test import override_settings
from django.urls import reverse

from .factories import DEADLOCK, TREES, TREES_NEAR_COPY, MediaTestCase, make_docx, make_pdf, upload
from .models import Note
from .similarity import find_similar_notes, similarity_scores
from .textutils import extract_text


class ExtractionTests(MediaTestCase):
    def test_extracts_txt_md_docx_pdf(self):
        self.assertIn("inorder", extract_text(upload("a.txt", TREES)))
        self.assertIn("inorder", extract_text(upload("a.md", "# Trees\n" + TREES)))
        self.assertIn("inorder", extract_text(upload("a.docx", make_docx(TREES))))
        self.assertIn("inorder", extract_text(upload("a.pdf", make_pdf(TREES))))

    def test_images_and_unsupported_types_fall_back_to_empty(self):
        for name in ["scan.png", "photo.jpg", "slides.pptx", "legacy.doc"]:
            self.assertEqual(extract_text(upload(name, b"\x89PNG anything")), "")

    def test_corrupt_files_never_raise(self):
        self.assertEqual(extract_text(upload("bad.pdf", b"not a pdf at all")), "")
        self.assertEqual(extract_text(upload("bad.docx", b"not a zip")), "")

    def test_nul_bytes_removed_and_length_capped(self):
        with override_settings(EXTRACT_MAX_CHARS=20):
            self.assertEqual(len(extract_text(upload("a.txt", "x" * 100))), 20)
        self.assertNotIn("\x00", extract_text(upload("a.txt", b"ab\x00cd")))


class SimilarityTests(MediaTestCase):
    def test_identical_high_unrelated_low(self):
        scores = similarity_scores(TREES, [TREES, DEADLOCK])
        self.assertGreater(scores[0], 0.99)
        self.assertLess(scores[1], 0.2)

    def test_empty_inputs(self):
        self.assertEqual(similarity_scores("", [TREES]), [])
        self.assertEqual(similarity_scores(TREES, []), [])
        self.assertEqual(similarity_scores("the and of", ["the or an"]), [])  # only stop words

    def test_find_similar_scoped_to_subject_and_threshold(self):
        same = self.make_note("Same subject", text=TREES)
        self.make_note("Other subject", subject=self.other_subject, text=TREES)
        hits = find_similar_notes(TREES_NEAR_COPY, self.subject)
        self.assertEqual([h.note for h in hits], [same])
        self.assertGreaterEqual(hits[0].score, 0.85)
        self.assertEqual(find_similar_notes(TREES_NEAR_COPY, self.subject, threshold=1.01), [])

    def test_short_texts_are_never_compared(self):
        self.make_note("Tiny", text="hello world")
        self.assertEqual(find_similar_notes("hello world", self.subject), [])


class UploadFlowTests(MediaTestCase):
    def post_upload(self, title, data, filename="notes.txt", subject=None, **extra):
        return self.client.post(reverse("note_create"), {
            "title": title, "subject": (subject or self.subject).pk, "description": "", "tags": "",
            "file": upload(filename, data), **extra,
        })

    def setUp(self):
        super().setUp()
        self.original = self.make_note("Trees original", text=TREES)
        self.login(self.bob)

    def test_unique_upload_saves_text_and_score_without_warning(self):
        r = self.post_upload("Deadlocks", DEADLOCK)
        self.assertEqual(r.status_code, 302)
        note = Note.objects.get(title="Deadlocks")
        self.assertIn("banker", note.extracted_text)
        self.assertIsNone(note.duplicate_of)
        self.assertLess(note.similarity_score, 0.5)

    def test_near_duplicate_is_blocked_until_confirmed(self):
        r = self.post_upload("Trees copy", TREES_NEAR_COPY)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Note.objects.filter(title="Trees copy").exists())
        self.assertContains(r, "Possible duplicate")
        self.assertContains(r, self.original.get_absolute_url())
        self.assertContains(r, "Trees original")
        self.assertRegex(r.content.decode(), r"\d{2,3}% similar")
        pending_path = self.client.session["pending_note_upload"]["path"]
        self.assertTrue(default_storage.exists(pending_path))

        r = self.client.post(reverse("note_create"), {"confirm_duplicate": "1"})
        self.assertEqual(r.status_code, 302)
        note = Note.objects.get(title="Trees copy")
        self.assertEqual(note.duplicate_of, self.original)
        self.assertGreaterEqual(note.similarity_score, 0.85)
        self.assertTrue(note.is_likely_duplicate)
        self.assertEqual(note.file.read(), TREES_NEAR_COPY.encode())
        self.assertNotIn("pending_note_upload", self.client.session)
        self.assertFalse(default_storage.exists(pending_path))  # temp file cleaned up

    def test_discarding_a_flagged_upload(self):
        self.post_upload("Trees copy", TREES_NEAR_COPY)
        pending_path = self.client.session["pending_note_upload"]["path"]
        r = self.client.post(reverse("note_create"), {"confirm_duplicate": "1", "action": "cancel"})
        self.assertRedirects(r, reverse("note_list"))
        self.assertFalse(Note.objects.filter(title="Trees copy").exists())
        self.assertFalse(default_storage.exists(pending_path))

    def test_confirm_without_pending_upload_is_rejected(self):
        r = self.client.post(reverse("note_create"), {"confirm_duplicate": "1"})
        self.assertRedirects(r, reverse("note_create"))
        self.assertFalse(Note.objects.filter(title="Trees copy").exists())

    def test_confirm_is_revalidated_server_side(self):
        """A tampered session cannot smuggle in a file type the form would refuse."""
        self.post_upload("Trees copy", TREES_NEAR_COPY)
        session = self.client.session
        session["pending_note_upload"]["name"] = "evil.exe"
        session.save()
        r = self.client.post(reverse("note_create"), {"confirm_duplicate": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Note.objects.filter(title="Trees copy").exists())

    def test_same_text_in_another_subject_is_fine(self):
        r = self.post_upload("Trees in OS", TREES_NEAR_COPY, subject=self.other_subject)
        self.assertEqual(r.status_code, 302)

    def test_threshold_is_configurable(self):
        with override_settings(DUPLICATE_SIMILARITY_THRESHOLD=1.01):
            self.assertEqual(self.post_upload("Trees copy", TREES_NEAR_COPY).status_code, 302)
        note = Note.objects.get(title="Trees copy")
        self.assertIsNone(note.duplicate_of)           # below the (raised) threshold: not flagged
        self.assertGreater(note.similarity_score, 0.9)  # but the score is still recorded

    def test_image_and_unreadable_uploads_are_not_blocked(self):
        self.assertEqual(self.post_upload("Scan", b"\x89PNG\r\n\x1a\nxxxx", "scan.png").status_code, 302)
        self.assertEqual(self.post_upload("Broken", b"garbage", "broken.pdf").status_code, 302)
        self.assertEqual(Note.objects.get(title="Scan").extracted_text, "")
        self.assertIsNone(Note.objects.get(title="Scan").similarity_score)

    def test_docx_and_pdf_duplicates_are_detected(self):
        self.assertEqual(self.post_upload("Copy docx", make_docx(TREES), "c.docx").status_code, 200)
        self.assertEqual(self.post_upload("Copy pdf", make_pdf(TREES), "c.pdf").status_code, 200)

    def test_editing_with_a_new_file_refreshes_text_and_flag_without_blocking(self):
        note = self.make_note("Mine", user=self.bob, text=DEADLOCK)
        r = self.client.post(reverse("note_update", args=[note.pk]), {
            "title": "Mine", "subject": self.subject.pk, "description": "", "tags": "",
            "file": upload("new.txt", TREES_NEAR_COPY),
        })
        self.assertEqual(r.status_code, 302)
        note.refresh_from_db()
        self.assertIn("inorder", note.extracted_text)
        self.assertEqual(note.duplicate_of, self.original)

    def test_editing_without_a_new_file_keeps_derived_data(self):
        note = self.make_note("Mine", user=self.bob, text=DEADLOCK, similarity_score=0.1)
        self.client.post(reverse("note_update", args=[note.pk]), {
            "title": "Renamed", "subject": self.subject.pk, "description": "", "tags": "",
        })
        note.refresh_from_db()
        self.assertEqual((note.title, note.extracted_text, note.similarity_score), ("Renamed", DEADLOCK, 0.1))


class AdminFilterTests(MediaTestCase):
    def test_moderators_can_filter_likely_duplicates(self):
        from django.contrib.auth.models import User

        User.objects.create_superuser("root", password="pw12345!")
        self.make_note("Clean", similarity_score=0.2)
        self.make_note("Dupe", similarity_score=0.93)
        self.client.login(username="root", password="pw12345!")
        url = reverse("admin:notes_note_changelist")
        r = self.client.get(url, {"dup": "likely"})
        self.assertContains(r, "Dupe")
        self.assertNotContains(r, "Clean</a>")
        r = self.client.get(url, {"dup": "clean"})
        self.assertContains(r, "Clean")
        self.assertNotContains(r, "Dupe</a>")
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_derived_fields_are_not_editable_in_admin(self):
        from django.contrib.auth.models import User

        User.objects.create_superuser("root", password="pw12345!")
        note = self.make_note("Dupe", similarity_score=0.93)
        self.client.login(username="root", password="pw12345!")
        self.assertEqual(self.client.get(reverse("admin:notes_note_change", args=[note.pk])).status_code, 200)
