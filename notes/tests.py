import shutil
import tempfile

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Bookmark, Note, Rating, Subject

TMP = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TMP)
class PeerNotesTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        self.subject = Subject.objects.create(name="DSA", semester=3, branch="CSE")
        self.alice = User.objects.create_user("alice", password="pw12345!")
        self.bob = User.objects.create_user("bob", password="pw12345!")
        self.note = self.make_note("Trees", self.alice, downloads=10)

    def make_note(self, title, user, downloads=0, tags=""):
        return Note.objects.create(
            title=title, subject=self.subject, uploader=user, downloads=downloads, tags=tags,
            file=SimpleUploadedFile(f"{title}.txt", b"hello"),
        )

    def test_rating_unique_per_user(self):
        Rating.objects.create(note=self.note, user=self.bob, stars=4)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Rating.objects.create(note=self.note, user=self.bob, stars=5)

    def test_ajax_rating_updates_not_duplicates(self):
        self.client.login(username="bob", password="pw12345!")
        url = reverse("rate_note", args=[self.note.pk])
        self.client.post(url, {"stars": 3})
        r = self.client.post(url, {"stars": 5})
        self.assertEqual(r.json()["avg"], 5.0)
        self.assertEqual(Rating.objects.filter(note=self.note).count(), 1)

    def test_cannot_rate_own_note_or_invalid(self):
        self.client.login(username="alice", password="pw12345!")
        url = reverse("rate_note", args=[self.note.pk])
        self.assertEqual(self.client.post(url, {"stars": 5}).status_code, 403)
        self.client.login(username="bob", password="pw12345!")
        self.assertEqual(self.client.post(url, {"stars": 9}).status_code, 400)

    def test_trending_formula(self):
        other = self.make_note("Graphs", self.alice, downloads=20)
        Rating.objects.create(note=self.note, user=self.bob, stars=5)
        notes = {n.title: n for n in Note.objects.with_stats()}
        # Trees: 0.7*5 + 0.3*(10/20*5) = 4.25 ; Graphs: 0 + 0.3*5 = 1.5
        self.assertAlmostEqual(notes["Trees"].trending_score, 4.25)
        self.assertAlmostEqual(notes["Graphs"].trending_score, 1.5)
        self.assertEqual(Note.objects.with_stats().order_by("-trending_score").first(), self.note)

    def test_search_and_partial(self):
        self.make_note("Graphs", self.alice, tags="unit-4")
        r = self.client.get(reverse("note_list"), {"q": "graph", "partial": 1})
        self.assertContains(r, "Graphs")
        self.assertNotContains(r, "Trees")

    def test_download_counts_and_requires_login(self):
        url = reverse("note_download", args=[self.note.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.login(username="bob", password="pw12345!")
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        r.close()
        self.note.refresh_from_db()
        self.assertEqual(self.note.downloads, 11)

    def test_comment_and_bookmark_ajax(self):
        self.client.login(username="bob", password="pw12345!")
        r = self.client.post(reverse("add_comment", args=[self.note.pk]), {"text": "Nice"})
        self.assertTrue(r.json()["ok"])
        r = self.client.post(reverse("toggle_bookmark", args=[self.note.pk]))
        self.assertTrue(r.json()["bookmarked"])
        self.client.post(reverse("toggle_bookmark", args=[self.note.pk]))
        self.assertFalse(Bookmark.objects.exists())

    def test_moderator_group_and_permissions(self):
        self.assertTrue(Group.objects.filter(name="Moderators").exists())
        url = reverse("note_delete", args=[self.note.pk])
        self.client.login(username="bob", password="pw12345!")
        self.assertEqual(self.client.post(url).status_code, 403)
        bob = User.objects.get(username="bob")
        bob.groups.add(Group.objects.get(name="Moderators"))
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertFalse(Note.objects.filter(pk=self.note.pk).exists())

    def test_dashboard_and_pages_render(self):
        Rating.objects.create(note=self.note, user=self.bob, stars=4)
        for name in ["dashboard", "note_list", "signup", "login"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        self.assertEqual(self.client.get(reverse("note_detail", args=[self.note.pk])).status_code, 200)
        self.client.login(username="bob", password="pw12345!")
        self.assertEqual(self.client.get(reverse("note_create")).status_code, 200)
        self.assertEqual(self.client.get(reverse("bookmarks")).status_code, 200)
