from datetime import timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from .factories import MediaTestCase
from .models import Note, Rating, ranking_params


def bayes(R, v, C, m):
    return (v / (v + m)) * R + (m / (v + m)) * C


class BayesianRankingTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User

        self.raters = [User.objects.create_user(f"r{i}", password="x") for i in range(12)]

    def rate(self, note, stars_list):
        for user, stars in zip(self.raters, stars_list):
            Rating.objects.create(note=note, user=user, stars=stars)

    def scores(self):
        return {n.title: n for n in Note.objects.with_stats()}

    @override_settings(TRENDING_HALF_LIFE_DAYS=0, BAYESIAN_MIN_VOTES=5)
    def test_weighted_average_matches_formula(self):
        few = self.make_note("few")
        many = self.make_note("many")
        self.make_note("unrated")
        self.rate(few, [5])
        self.rate(many, [4, 5, 4, 5, 4, 5, 4, 5, 4, 5])  # mean 4.5, 10 votes
        C = (5 + 45) / 11
        self.assertAlmostEqual(ranking_params()["global_mean"], C)
        s = self.scores()
        self.assertAlmostEqual(s["few"].bayes_rating, bayes(5, 1, C, 5))
        self.assertAlmostEqual(s["many"].bayes_rating, bayes(4.5, 10, C, 5))
        self.assertAlmostEqual(s["few"].trending_score, 0.7 * bayes(5, 1, C, 5))  # no downloads, no decay
        self.assertEqual(s["unrated"].bayes_rating, 0.0)
        self.assertEqual(s["unrated"].trending_score, 0.0)
        self.assertAlmostEqual(s["few"].avg_rating, 5.0)  # plain mean is still exposed for display

    @override_settings(TRENDING_HALF_LIFE_DAYS=0)
    def test_one_perfect_vote_does_not_beat_a_well_rated_note(self):
        lucky, solid, filler = self.make_note("lucky"), self.make_note("solid"), self.make_note("filler")
        self.rate(filler, [2] * 12)  # drags the global mean (C) down to a realistic level
        self.rate(lucky, [5])
        self.rate(solid, [5, 4, 5, 4, 5, 4, 5, 4, 5, 4, 5, 4])
        order = [n.title for n in Note.objects.with_stats().order_by("-trending_score")]
        self.assertEqual(order[:2], ["solid", "lucky"])

    @override_settings(TRENDING_HALF_LIFE_DAYS=0)
    def test_min_votes_setting_changes_the_pull(self):
        note, _ = self.make_note("a"), self.make_note("b")
        self.rate(note, [5])
        self.rate(Note.objects.get(title="b"), [1, 1, 1])
        C = (5 + 3) / 4
        with override_settings(BAYESIAN_MIN_VOTES=1):
            self.assertAlmostEqual(self.scores()["a"].bayes_rating, bayes(5, 1, C, 1))
        with override_settings(BAYESIAN_MIN_VOTES=20):
            self.assertAlmostEqual(self.scores()["a"].bayes_rating, bayes(5, 1, C, 20))

    def test_downloads_weight_is_kept(self):
        a = self.make_note("a", downloads=20)
        self.make_note("b", downloads=10)
        self.rate(a, [4])
        with override_settings(TRENDING_HALF_LIFE_DAYS=0):
            s = self.scores()
            self.assertAlmostEqual(s["b"].trending_score, 0.3 * 2.5)
            self.assertAlmostEqual(s["a"].trending_score, 0.7 * bayes(4, 1, 4, 5) + 0.3 * 5)


class RecencyDecayTests(MediaTestCase):
    def make_pair(self, age_days):
        """Two notes with identical ratings; one back-dated by age_days."""
        new, old = self.make_note("new"), self.make_note("old")
        for user in (self.bob, self.carol):
            for note in (new, old):
                Rating.objects.create(note=note, user=user, stars=4)
        Note.objects.filter(pk=old.pk).update(upload_date=timezone.now() - timedelta(days=age_days))
        return {n.title: n for n in Note.objects.with_stats()}

    def test_score_halves_after_one_half_life(self):
        s = self.make_pair(90)
        self.assertAlmostEqual(s["old"].recency_factor, 0.5, places=4)
        self.assertAlmostEqual(s["old"].trending_score / s["new"].trending_score, 0.5, places=3)

    def test_two_half_lives_quarter(self):
        self.assertAlmostEqual(self.make_pair(180)["old"].recency_factor, 0.25, places=4)

    @override_settings(TRENDING_HALF_LIFE_DAYS=30)
    def test_half_life_is_configurable(self):
        self.assertAlmostEqual(self.make_pair(30)["old"].recency_factor, 0.5, places=4)

    @override_settings(TRENDING_HALF_LIFE_DAYS=0)
    def test_decay_can_be_disabled(self):
        s = self.make_pair(400)
        self.assertEqual(s["old"].recency_factor, 1.0)
        self.assertAlmostEqual(s["old"].trending_score, s["new"].trending_score)

    def test_fresh_note_is_not_penalised_and_future_dates_are_clamped(self):
        s = self.make_pair(0)
        self.assertAlmostEqual(s["new"].recency_factor, 1.0, places=4)
        Note.objects.update(upload_date=timezone.now() + timedelta(days=5))
        self.assertTrue(all(n.recency_factor <= 1.0 for n in Note.objects.with_stats()))

    def test_newer_note_outranks_older_with_same_rating(self):
        self.make_pair(120)
        top = Note.objects.with_stats().order_by("-trending_score").first()
        self.assertEqual(top.title, "new")


class DashboardFormulaTextTests(MediaTestCase):
    def test_dashboard_explains_the_new_formula(self):
        self.make_note("a")
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, "Weighted rating")
        self.assertContains(r, "half-life of 90 days")
        self.assertContains(r, "m = 5")
        with override_settings(TRENDING_HALF_LIFE_DAYS=0):
            self.assertContains(self.client.get(reverse("dashboard")), "decay is switched off")
