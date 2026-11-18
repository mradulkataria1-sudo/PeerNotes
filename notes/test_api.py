from unittest import mock

from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.throttling import SimpleRateThrottle

from .factories import PASSWORD, MediaTestCase
from .models import Bookmark, Comment, Note, Rating, Report


class ApiTestCase(MediaTestCase):
    def auth(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.defaults["HTTP_AUTHORIZATION"] = f"Token {token.key}"

    def anon(self):
        self.client.defaults.pop("HTTP_AUTHORIZATION", None)

    def setUp(self):
        super().setUp()
        self.note = self.make_note("Trees", user=self.alice, tags="unit-3, trees", downloads=10)
        self.other = self.make_note("Hashing", user=self.alice, subject=self.other_subject, tags="midterm")
        self.json = {"content_type": "application/json"}


class TokenTests(ApiTestCase):
    def test_obtain_token_with_valid_credentials(self):
        r = self.client.post(reverse("api_token"), {"username": "bob", "password": PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["token"], Token.objects.get(user=self.bob).key)

    def test_wrong_credentials_rejected(self):
        r = self.client.post(reverse("api_token"), {"username": "bob", "password": "nope"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Token.objects.exists())

    def test_invalid_token_is_401(self):
        self.client.defaults["HTTP_AUTHORIZATION"] = "Token deadbeef"
        self.assertEqual(self.client.get(reverse("api_me")).status_code, 401)

    def test_token_endpoint_is_throttled_more_tightly(self):
        with mock.patch.dict(SimpleRateThrottle.THROTTLE_RATES, {"token_auth": "2/min"}):
            codes = [self.client.post(reverse("api_token"), {"username": "bob", "password": "x"}).status_code
                     for _ in range(3)]
        self.assertEqual(codes, [400, 400, 429])


class NoteReadTests(ApiTestCase):
    def test_list_is_public_paginated_and_has_stats(self):
        Rating.objects.create(note=self.note, user=self.bob, stars=4)
        Comment.objects.create(note=self.note, user=self.bob, text="hi")
        r = self.client.get(reverse("api_note_list"))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["count"], 2)
        self.assertEqual({"count", "next", "previous", "results"}, set(body))
        first = body["results"][0]  # trending: rated + downloaded note first
        self.assertEqual(first["title"], "Trees")
        self.assertEqual(first["avg_rating"], 4.0)
        self.assertEqual(first["rating_count"], 1)
        self.assertEqual(first["comment_count"], 1)
        self.assertIn("trending_score", first)
        self.assertEqual(first["subject"]["name"], "DSA")
        self.assertEqual(first["tags"], ["unit-3", "trees"])
        self.assertEqual(first["uploader"], "alice")
        self.assertTrue(first["web_url"].endswith(self.note.get_absolute_url()))
        self.assertIsNone(body["results"][1]["avg_rating"])

    def test_trending_score_matches_the_orm_annotation(self):
        Rating.objects.create(note=self.note, user=self.bob, stars=5)
        expected = Note.objects.with_stats().get(pk=self.note.pk).trending_score
        got = self.client.get(reverse("api_note_detail", args=[self.note.pk])).json()["trending_score"]
        self.assertAlmostEqual(got, expected, places=4)

    def test_filters_match_the_web_feed(self):
        url = reverse("api_note_list")
        titles = lambda **p: [n["title"] for n in self.client.get(url, p).json()["results"]]  # noqa: E731
        self.assertEqual(titles(q="hash"), ["Hashing"])
        self.assertEqual(titles(subject=self.subject.pk), ["Trees"])
        self.assertEqual(titles(semester=4), ["Hashing"])
        self.assertEqual(titles(branch="CSE", tag="MIDTERM"), ["Hashing"])
        self.assertEqual(titles(sort="newest"), ["Hashing", "Trees"])
        self.assertEqual(titles(sort="downloads"), ["Trees", "Hashing"])
        self.assertEqual(titles(subject="abc", sort="bogus"), titles())  # junk is ignored like the web view
        web = self.client.get(reverse("note_list"), {"q": "hash", "partial": 1})
        self.assertContains(web, "Hashing")
        self.assertNotContains(web, "Trees")

    def test_page_size_is_capped(self):
        for i in range(12):
            self.make_note(f"n{i}")
        url = reverse("api_note_list")
        self.assertEqual(len(self.client.get(url).json()["results"]), 9)
        self.assertEqual(len(self.client.get(url, {"page_size": 5}).json()["results"]), 5)
        self.assertEqual(len(self.client.get(url, {"page_size": 500}).json()["results"]), 14)  # 50 cap > 14 notes
        self.assertEqual(self.client.get(url, {"page": 99}).status_code, 404)

    def test_detail_includes_my_rating_and_bookmark_only_for_authenticated_users(self):
        url = reverse("api_note_detail", args=[self.note.pk])
        anon = self.client.get(url).json()
        self.assertIsNone(anon["my_rating"])
        self.assertIsNone(anon["bookmarked"])
        Rating.objects.create(note=self.note, user=self.bob, stars=3)
        Bookmark.objects.create(note=self.note, user=self.bob)
        self.auth(self.bob)
        mine = self.client.get(url).json()
        self.assertEqual((mine["my_rating"], mine["bookmarked"]), (3, True))
        self.assertEqual(self.client.get(reverse("api_note_detail", args=[9999])).status_code, 404)

    def test_trending_endpoint_orders_and_limits(self):
        Rating.objects.create(note=self.other, user=self.bob, stars=5)
        for i in range(4):
            self.make_note(f"extra{i}")
        r = self.client.get(reverse("api_trending"), {"limit": 2}).json()
        self.assertEqual(len(r), 2)
        self.assertEqual(len(self.client.get(reverse("api_trending")).json()), 6)  # default 10 > 6 notes
        scores = [n["trending_score"] for n in self.client.get(reverse("api_trending")).json()]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(len(self.client.get(reverse("api_trending"), {"limit": "zzz"}).json()), 6)
        self.assertEqual(len(self.client.get(reverse("api_trending"), {"limit": -5}).json()), 1)


class RateApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("api_rate_note", args=[self.note.pk])

    def test_requires_authentication(self):
        self.assertEqual(self.client.post(self.url, {"stars": 4}).status_code, 401)

    def test_cannot_rate_own_note(self):
        self.auth(self.alice)
        self.assertEqual(self.client.post(self.url, {"stars": 5}).status_code, 403)
        self.assertFalse(Rating.objects.exists())

    def test_invalid_stars_rejected(self):
        self.auth(self.bob)
        for bad in [0, 6, -1, "abc", "", 2.5, None]:
            r = self.client.post(self.url, {"stars": bad}, **self.json)
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(self.client.post(self.url, {}, **self.json).status_code, 400)
        self.assertFalse(Rating.objects.exists())

    def test_one_rating_per_user_updates_instead_of_duplicating(self):
        self.auth(self.bob)
        r = self.client.post(self.url, {"stars": 3}, **self.json)
        self.assertEqual(r.status_code, 201)
        r = self.client.post(self.url, {"stars": 5}, **self.json)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Rating.objects.filter(note=self.note).count(), 1)
        body = r.json()
        self.assertEqual((body["stars"], body["avg_rating"], body["rating_count"]), (5, 5.0, 1))
        self.assertIn("trending_score", body)
        self.auth(self.carol)
        self.assertEqual(self.client.post(self.url, {"stars": 3}, **self.json).json()["avg_rating"], 4.0)

    def test_form_encoded_payload_also_works_and_missing_note_404(self):
        self.auth(self.bob)
        self.assertEqual(self.client.post(self.url, {"stars": "4"}).status_code, 201)
        self.assertEqual(self.client.post(reverse("api_rate_note", args=[9999]), {"stars": 4}).status_code, 404)


class CommentApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("api_comments", args=[self.note.pk])

    def test_post_creates_comment_for_token_user(self):
        self.auth(self.bob)
        r = self.client.post(self.url, {"text": "Nice notes"}, **self.json)
        self.assertEqual(r.status_code, 201)
        self.assertEqual((r.json()["user"], r.json()["text"]), ("bob", "Nice notes"))
        self.assertEqual(Comment.objects.get().user, self.bob)

    def test_post_validation_and_auth(self):
        self.assertEqual(self.client.post(self.url, {"text": "x"}, **self.json).status_code, 401)
        self.auth(self.bob)
        for bad in ["", "   ", "x" * 1001]:
            self.assertEqual(self.client.post(self.url, {"text": bad}, **self.json).status_code, 400, bad[:5])
        self.assertEqual(self.client.post(self.url, {}, **self.json).status_code, 400)
        self.assertEqual(self.client.post(self.url, {"text": "x" * 1000}, **self.json).status_code, 201)

    def test_user_cannot_spoof_author(self):
        self.auth(self.bob)
        self.client.post(self.url, {"text": "hi", "user": "alice"}, **self.json)
        self.assertEqual(Comment.objects.get().user, self.bob)

    def test_list_is_public_and_paginated(self):
        for i in range(11):
            Comment.objects.create(note=self.note, user=self.bob, text=f"c{i}")
        r = self.client.get(self.url).json()
        self.assertEqual((r["count"], len(r["results"])), (11, 9))
        self.assertEqual(self.client.get(reverse("api_comments", args=[9999])).status_code, 404)


class BookmarkAndProfileTests(ApiTestCase):
    def test_bookmark_toggles_and_requires_auth(self):
        url = reverse("api_bookmark", args=[self.note.pk])
        self.assertEqual(self.client.post(url).status_code, 401)
        self.auth(self.bob)
        self.assertTrue(self.client.post(url).json()["bookmarked"])
        self.assertEqual(Bookmark.objects.count(), 1)
        self.assertFalse(self.client.post(url).json()["bookmarked"])
        self.assertEqual(Bookmark.objects.count(), 0)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(reverse("api_bookmark", args=[9999])).status_code, 404)

    def test_me_requires_auth_and_reports_aggregates(self):
        self.assertEqual(self.client.get(reverse("api_me")).status_code, 401)
        Rating.objects.create(note=self.note, user=self.bob, stars=4)
        Rating.objects.create(note=self.other, user=self.bob, stars=2)
        Bookmark.objects.create(note=self.note, user=self.bob)
        self.auth(self.alice)
        me = self.client.get(reverse("api_me")).json()
        self.assertEqual(me["username"], "alice")
        self.assertEqual((me["notes_uploaded"], me["downloads_received"]), (2, 10))
        self.assertEqual((me["ratings_received"], me["avg_rating_received"]), (2, 3.0))
        self.assertFalse(me["can_remove_notes"])
        self.auth(self.bob)
        me = self.client.get(reverse("api_me")).json()
        self.assertEqual((me["ratings_given"], me["bookmarks"], me["notes_uploaded"]), (2, 1, 0))
        self.assertIsNone(me["avg_rating_received"])
        self.assertNotIn("password", me)

    def test_me_exposes_roles(self):
        self.auth(self.moderator)
        me = self.client.get(reverse("api_me")).json()
        self.assertEqual(me["groups"], ["Moderators"])
        self.assertTrue(me["can_remove_notes"] and me["can_review_reports"])
        self.auth(self.reviewer)
        me = self.client.get(reverse("api_me")).json()
        self.assertTrue(me["can_review_reports"])
        self.assertFalse(me["can_remove_notes"])


class ThrottleAndAuthSchemeTests(ApiTestCase):
    def test_anonymous_requests_are_throttled(self):
        with mock.patch.dict(SimpleRateThrottle.THROTTLE_RATES, {"anon": "3/min"}):
            codes = [self.client.get(reverse("api_note_list")).status_code for _ in range(4)]
        self.assertEqual(codes, [200, 200, 200, 429])

    def test_authenticated_users_have_their_own_limit(self):
        self.auth(self.bob)
        with mock.patch.dict(SimpleRateThrottle.THROTTLE_RATES, {"user": "2/min", "anon": "1/min"}):
            codes = [self.client.get(reverse("api_me")).status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])

    def test_session_login_alone_does_not_authenticate_the_api(self):
        self.login(self.bob)  # web session, no token
        self.assertEqual(self.client.post(reverse("api_bookmark", args=[self.note.pk])).status_code, 401)

    def test_api_does_not_leak_private_fields(self):
        body = self.client.get(reverse("api_note_detail", args=[self.note.pk])).json()
        for field in ("extracted_text", "similarity_score", "duplicate_of", "file"):
            self.assertNotIn(field, body)

    def test_reports_are_not_exposed_over_the_api(self):
        Report.objects.create(note=self.note, reporter=self.bob, reason="spam")
        self.auth(self.moderator)
        self.assertEqual(self.client.get("/api/reports/").status_code, 404)
