from io import StringIO

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings

from .factories import DEADLOCK, TREES, TREES_NEAR_COPY, MediaTestCase, make_pdf
from .models import Note, Report


class BackfillTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.first = self.make_note("First", data=TREES.encode(), filename="a.txt")
        self.copy = self.make_note("Copy", data=TREES_NEAR_COPY.encode(), filename="b.txt")
        self.other = self.make_note("Other", data=DEADLOCK.encode(), filename="c.txt")
        self.pdf = self.make_note("Pdf", data=make_pdf(DEADLOCK + " pdf only words"), filename="d.pdf",
                                  subject=self.other_subject)
        Note.objects.update(extracted_text="")  # simulate rows created before this feature existed

    def run_cmd(self, *args):
        out = StringIO()
        call_command("backfill_notes", *args, stdout=out)
        return out.getvalue()

    def test_fills_text_thumbnails_and_flags_only_the_later_copy(self):
        self.assertIn("Processed 4", self.run_cmd())
        for n in (self.first, self.copy, self.other, self.pdf):
            n.refresh_from_db()
        self.assertIn("inorder", self.first.extracted_text)
        self.assertIsNone(self.first.duplicate_of)  # the original is not flagged as a copy of its copy
        self.assertEqual(self.copy.duplicate_of, self.first)
        self.assertTrue(self.copy.is_likely_duplicate)
        self.assertFalse(self.other.is_likely_duplicate)
        self.assertTrue(self.pdf.thumbnail)

    def test_is_idempotent_and_all_flag_reprocesses(self):
        self.run_cmd()
        self.assertIn("Processed 0", self.run_cmd())
        self.assertIn("Processed 4", self.run_cmd("--all"))

    def test_missing_files_are_skipped_not_fatal(self):
        import os

        os.remove(self.other.file.path)
        self.assertIn("skipped 1", self.run_cmd())
        self.other.refresh_from_db()
        self.assertEqual(self.other.extracted_text, "")


class SeedDemoTests(MediaTestCase):
    def run_seed(self):
        call_command("seed_demo", stdout=StringIO())

    def test_seed_creates_demo_data_for_every_new_feature(self):
        self.run_seed()
        near = Note.objects.get(title="Trees and graphs quick revision")
        self.assertTrue(near.is_likely_duplicate)
        self.assertEqual(near.duplicate_of.title, "Trees and Graphs cheat sheet")
        self.assertGreaterEqual(Report.objects.filter(status="open").count(), 3)
        self.assertTrue(Report.objects.filter(status="resolved").exists())
        self.assertTrue(Report.objects.filter(status="dismissed").exists())
        self.assertTrue(User.objects.get(username="reviewer").groups.filter(name="Reviewers").exists())
        self.assertTrue(User.objects.get(username="moderator").groups.filter(name="Moderators").exists())
        pdf = Note.objects.get(title__endswith="(PDF)")
        self.assertTrue(pdf.thumbnail)
        self.assertTrue(all(n.extracted_text for n in Note.objects.all()))
        for report in Report.objects.all():
            self.assertNotEqual(report.reporter, report.note.uploader)

    def test_seed_is_idempotent(self):
        self.run_seed()
        counts = (Note.objects.count(), Report.objects.count(), User.objects.count())
        self.run_seed()
        self.assertEqual((Note.objects.count(), Report.objects.count(), User.objects.count()), counts)

    def test_seed_flags_the_near_duplicate_even_with_a_stricter_threshold_setting_untouched(self):
        with override_settings(DUPLICATE_SIMILARITY_THRESHOLD=0.85):
            self.run_seed()
        self.assertGreaterEqual(Note.objects.get(title="Trees and graphs quick revision").similarity_score, 0.85)


class MigrationOnExistingDataTests(TransactionTestCase):
    """migrate must work on a database that already holds data from the 0001 schema."""

    migrate_from = [("notes", "0001_initial")]

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())  # leave the DB fully migrated for other tests
        super().tearDown()

    def test_upgrade_keeps_rows_and_applies_defaults(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old = executor.loader.project_state(self.migrate_from).apps
        user = old.get_model("auth", "User").objects.create(username="legacy")
        subject = old.get_model("notes", "Subject").objects.create(name="DSA", semester=3, branch="CSE")
        note = old.get_model("notes", "Note").objects.create(
            title="Legacy", subject=subject, uploader=user, file="notes/2026/11/legacy.pdf", downloads=7)
        old.get_model("notes", "Rating").objects.create(note=note, user=user, stars=4)

        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        new = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps
        legacy = new.get_model("notes", "Note").objects.get(title="Legacy")
        self.assertEqual((legacy.downloads, legacy.file.name), (7, "notes/2026/11/legacy.pdf"))
        self.assertEqual(legacy.extracted_text, "")
        self.assertIsNone(legacy.similarity_score)
        self.assertIsNone(legacy.duplicate_of_id)
        self.assertFalse(legacy.thumbnail)
        self.assertEqual(new.get_model("notes", "Rating").objects.count(), 1)
        self.assertEqual(new.get_model("notes", "Report").objects.count(), 0)

    def test_groups_are_refreshed_after_migrate(self):
        call_command("migrate", verbosity=0)
        self.assertTrue(Group.objects.filter(name="Reviewers").exists())
        self.assertTrue(Group.objects.get(name="Moderators").permissions.filter(codename="view_report").exists())
