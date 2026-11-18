import os

from django.contrib.auth.models import Group, User
from django.db import IntegrityError, transaction
from django.urls import reverse

from .factories import MediaTestCase
from .models import Note, Report


class ReportSubmissionTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.note = self.make_note("Reported note", user=self.alice)
        self.url = reverse("report_note", args=[self.note.pk])

    def test_student_can_report_via_ajax(self):
        self.login(self.bob)
        r = self.client.post(self.url, {"reason": "spam", "details": "  looks like an ad "},
                             headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        rep = Report.objects.get()
        self.assertEqual((rep.note, rep.reporter, rep.reason, rep.status), (self.note, self.bob, "spam", "open"))
        self.assertEqual(rep.details, "looks like an ad")
        self.assertEqual(rep.note_title, "Reported note")

    def test_all_reason_choices_accepted_and_others_rejected(self):
        self.login(self.bob)
        for reason, _ in Report.REASON_CHOICES:
            Report.objects.all().delete()
            self.assertEqual(self.client.post(self.url, {"reason": reason}).status_code, 200, reason)
        for bad in ["", "rude", "SPAM", "<script>"]:
            self.assertEqual(self.client.post(self.url, {"reason": bad}).status_code, 400, bad)
        self.assertEqual(self.client.post(self.url, {}).status_code, 400)

    def test_details_length_is_validated_server_side(self):
        self.login(self.bob)
        r = self.client.post(self.url, {"reason": "other", "details": "x" * 501})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Report.objects.exists())
        self.assertEqual(self.client.post(self.url, {"reason": "other", "details": "x" * 500}).status_code, 200)

    def test_anonymous_gets_401_and_owner_gets_403(self):
        self.assertEqual(self.client.post(self.url, {"reason": "spam"}).status_code, 401)
        self.login(self.alice)
        self.assertEqual(self.client.post(self.url, {"reason": "spam"}).status_code, 403)
        self.assertFalse(Report.objects.exists())

    def test_get_not_allowed_and_missing_note_404(self):
        self.login(self.bob)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.client.post(reverse("report_note", args=[9999]), {"reason": "spam"}).status_code, 404)

    def test_one_open_report_per_user_per_note(self):
        self.login(self.bob)
        self.assertEqual(self.client.post(self.url, {"reason": "spam"}).status_code, 200)
        self.assertEqual(self.client.post(self.url, {"reason": "other"}).status_code, 409)
        self.assertEqual(Report.objects.count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():  # the DB enforces it too
            Report.objects.create(note=self.note, reporter=self.bob, reason="other")
        self.login(self.carol)
        self.assertEqual(self.client.post(self.url, {"reason": "spam"}).status_code, 200)

    def test_can_report_again_after_the_first_report_is_handled(self):
        self.login(self.bob)
        self.client.post(self.url, {"reason": "spam"})
        Report.objects.get().close(self.moderator, Report.STATUS_DISMISSED)
        self.assertEqual(self.client.post(self.url, {"reason": "spam"}).status_code, 200)

    def test_report_button_visibility_on_note_page(self):
        detail = reverse("note_detail", args=[self.note.pk])
        self.assertNotContains(self.client.get(detail), 'id="report-btn"')  # anonymous
        self.login(self.alice)
        self.assertNotContains(self.client.get(detail), 'id="report-btn"')  # owner
        self.login(self.bob)
        self.assertContains(self.client.get(detail), 'id="report-btn"')
        self.client.post(self.url, {"reason": "spam"})
        self.assertContains(self.client.get(detail), "Reported</button>")


class GroupTests(MediaTestCase):
    def perms(self, group):
        return set(Group.objects.get(name=group).permissions.values_list("codename", flat=True))

    def test_reviewers_group_exists_and_cannot_delete_notes(self):
        rev = self.perms("Reviewers")
        self.assertTrue({"view_report", "change_report"} <= rev)
        self.assertNotIn("delete_note", rev)
        self.assertNotIn("change_note", rev)

    def test_moderators_can_also_handle_reports_and_delete_notes(self):
        mod = self.perms("Moderators")
        self.assertTrue({"view_report", "change_report", "delete_note"} <= mod)

    def test_reviewer_cannot_delete_a_note_through_the_normal_view(self):
        note = self.make_note("x")
        self.login(self.reviewer)
        self.assertEqual(self.client.post(reverse("note_delete", args=[note.pk])).status_code, 403)
        self.assertTrue(Note.objects.filter(pk=note.pk).exists())


class ModerationQueueTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.note = self.make_note("Bad note", user=self.alice, filename="bad.txt", data=b"spam")
        self.r1 = Report.objects.create(note=self.note, reporter=self.bob, reason="spam", details="buy pills")
        self.r2 = Report.objects.create(note=self.note, reporter=self.carol, reason="duplicate")
        self.queue = reverse("moderation_queue")

    def act(self, report, action):
        return self.client.post(reverse("report_action", args=[report.pk, action]))

    def test_access_is_permission_based_not_username_based(self):
        self.assertEqual(self.client.get(self.queue).status_code, 302)  # anonymous -> login
        self.login(self.bob)
        self.assertEqual(self.client.get(self.queue).status_code, 403)
        impostor = User.objects.create_user("moderator", password="pw12345!")  # name means nothing
        self.login(impostor)
        self.assertEqual(self.client.get(self.queue).status_code, 403)
        for user in (self.moderator, self.reviewer):
            self.login(user)
            self.assertEqual(self.client.get(self.queue).status_code, 200)
        self.login(self.moderator)
        r = self.client.get(self.queue)
        self.assertContains(r, "Bad note")
        self.assertContains(r, "buy pills")
        self.assertContains(r, "Remove note")

    def test_reviewer_sees_queue_without_remove_button(self):
        self.login(self.reviewer)
        r = self.client.get(self.queue)
        self.assertContains(r, "Mark resolved")
        self.assertNotContains(r, "Remove note</button>")

    def test_queue_lists_only_open_reports(self):
        self.r1.close(self.moderator, Report.STATUS_DISMISSED)
        self.login(self.moderator)
        r = self.client.get(self.queue)
        self.assertContains(r, "1 open report")
        self.assertNotContains(r, "buy pills")

    def test_moderator_removes_note_and_all_its_open_reports_resolve(self):
        path = self.note.file.path
        other = self.make_note("Fine note")
        Report.objects.create(note=other, reporter=self.bob, reason="other")
        self.login(self.moderator)
        r = self.act(self.r1, "remove")
        self.assertRedirects(r, self.queue)
        self.assertFalse(Note.objects.filter(pk=self.note.pk).exists())
        self.assertFalse(os.path.exists(path))
        for rep in Report.objects.filter(pk__in=[self.r1.pk, self.r2.pk]):  # reports survive as an audit trail
            self.assertEqual((rep.status, rep.resolved_by, rep.note, rep.note_title),
                             ("resolved", self.moderator, None, "Bad note"))
            self.assertIsNotNone(rep.resolved_at)
        self.assertEqual(Report.objects.filter(status="open").count(), 1)  # the unrelated one
        self.assertContains(self.client.get(self.queue), "Bad note")  # visible under "Recently handled"

    def test_dismiss_and_resolve_keep_the_note(self):
        self.login(self.moderator)
        self.act(self.r1, "dismiss")
        self.act(self.r2, "resolve")
        self.r1.refresh_from_db()
        self.r2.refresh_from_db()
        self.assertEqual((self.r1.status, self.r1.resolved_by), ("dismissed", self.moderator))
        self.assertEqual((self.r2.status, self.r2.resolved_by), ("resolved", self.moderator))
        self.assertTrue(Note.objects.filter(pk=self.note.pk).exists())

    def test_reviewer_can_resolve_and_dismiss_but_not_remove(self):
        self.login(self.reviewer)
        self.assertEqual(self.act(self.r1, "remove").status_code, 403)
        self.assertTrue(Note.objects.filter(pk=self.note.pk).exists())
        self.r1.refresh_from_db()
        self.assertEqual(self.r1.status, "open")
        self.assertEqual(self.act(self.r1, "dismiss").status_code, 302)
        self.assertEqual(self.act(self.r2, "resolve").status_code, 302)
        self.r1.refresh_from_db()
        self.assertEqual((self.r1.status, self.r1.resolved_by), ("dismissed", self.reviewer))

    def test_students_and_anonymous_cannot_act(self):
        self.assertEqual(self.act(self.r1, "dismiss").status_code, 302)  # anonymous -> login
        self.login(self.bob)
        for action in ("dismiss", "resolve", "remove"):
            self.assertEqual(self.act(self.r1, action).status_code, 403)
        self.r1.refresh_from_db()
        self.assertEqual(self.r1.status, "open")

    def test_actions_are_post_only_and_validated(self):
        self.login(self.moderator)
        self.assertEqual(self.client.get(reverse("report_action", args=[self.r1.pk, "dismiss"])).status_code, 405)
        self.assertEqual(self.act(self.r1, "explode").status_code, 404)
        self.assertEqual(self.client.post(reverse("report_action", args=[9999, "dismiss"])).status_code, 404)

    def test_already_handled_report_is_not_overwritten(self):
        self.login(self.moderator)
        self.act(self.r1, "dismiss")
        self.login(self.reviewer)
        self.act(self.r1, "resolve")
        self.r1.refresh_from_db()
        self.assertEqual((self.r1.status, self.r1.resolved_by), ("dismissed", self.moderator))

    def test_nav_shows_reports_link_only_with_permission(self):
        home = reverse("note_list")
        self.login(self.bob)
        self.assertNotContains(self.client.get(home), reverse("moderation_queue"))
        self.login(self.reviewer)
        self.assertContains(self.client.get(home), reverse("moderation_queue"))

    def test_admin_registers_reports(self):
        User.objects.create_superuser("root", password="pw12345!")
        self.client.login(username="root", password="pw12345!")
        self.assertEqual(self.client.get(reverse("admin:notes_report_changelist")).status_code, 200)


class CsvExportTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.note = self.make_note("Bad, \"quoted\" note", user=self.alice)
        Report.objects.create(note=self.note, reporter=self.bob, reason="spam", details="=HYPERLINK(\"http://x\")")
        Report.objects.create(note=self.note, reporter=self.carol, reason="other", details="line1\nline2")
        self.url = reverse("moderation_export")

    def test_permission_required(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.login(self.bob)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_moderators_and_reviewers_get_csv(self):
        import csv
        import io

        for user in (self.moderator, self.reviewer):
            self.login(user)
            r = self.client.get(self.url)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r["Content-Type"], "text/csv")
            self.assertIn("attachment", r["Content-Disposition"])
            rows = list(csv.reader(io.StringIO(r.content.decode())))
            self.assertEqual(rows[0][:3], ["id", "note_id", "note_title"])
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[1][2], 'Bad, "quoted" note')  # quoting round-trips
            self.assertEqual(rows[2][7], "line1\nline2")

    def test_formula_injection_is_neutralised(self):
        import csv
        import io

        self.login(self.moderator)
        rows = list(csv.reader(io.StringIO(self.client.get(self.url).content.decode())))
        self.assertTrue(rows[1][7].startswith("'="))

    def test_link_on_queue_page(self):
        self.login(self.reviewer)
        self.assertContains(self.client.get(reverse("moderation_queue")), self.url)
