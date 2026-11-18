import os

from django.conf import settings
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import (
    Avg, Case, Count, DateTimeField, ExpressionWrapper, F, FloatField, Func, Max, Q, Value, When,
)
from django.db.models.functions import Cast, Coalesce, Greatest, Power
from django.urls import reverse
from django.utils import timezone

TRENDING_RATING_WEIGHT = 0.7
TRENDING_DOWNLOAD_WEIGHT = 0.3
RATING_SCALE = 5.0  # downloads are normalised onto the same 0-5 scale as ratings

ALLOWED_EXTENSIONS = ["pdf", "doc", "docx", "ppt", "pptx", "txt", "md", "png", "jpg", "jpeg"]


class Subject(models.Model):
    BRANCH_CHOICES = [
        ("COMMON", "Common (first year)"),
        ("CSE", "Computer Science"),
        ("IT", "Information Technology"),
        ("ECE", "Electronics & Communication"),
        ("BT", "Biotechnology"),
    ]
    SEMESTER_CHOICES = [(i, f"Semester {i}") for i in range(1, 9)]

    name = models.CharField(max_length=120)
    semester = models.PositiveSmallIntegerField(choices=SEMESTER_CHOICES)
    branch = models.CharField(max_length=10, choices=BRANCH_CHOICES)

    class Meta:
        ordering = ["semester", "name"]
        unique_together = ("name", "semester", "branch")

    def __str__(self):
        return f"{self.name} (Sem {self.semester}, {self.branch})"


class AgeInDays(Func):
    """Age of a timestamp in fractional days, computed by the database.

    Usage: AgeInDays(now, F("upload_date")). Written per backend because there is
    no portable "difference as a number" in Django's ORM.
    """

    output_field = FloatField()
    arity = 2

    def as_sqlite(self, compiler, connection, **extra):
        return super().as_sql(
            compiler, connection, template="(julianday(%(expressions)s))",
            arg_joiner=") - julianday(", **extra,
        )

    def as_postgresql(self, compiler, connection, **extra):
        return super().as_sql(
            compiler, connection, template="((EXTRACT(EPOCH FROM (%(expressions)s)) / 86400.0)::double precision)",
            arg_joiner=" - ", **extra,
        )


def ranking_params():
    """Numbers behind the trending score, for the query and for the dashboard text."""
    global_mean = Rating.objects.aggregate(c=Avg("stars"))["c"] or 0.0
    return {
        "global_mean": float(global_mean),                                  # C
        "min_votes": max(float(settings.BAYESIAN_MIN_VOTES), 1e-9),         # m
        "half_life_days": float(settings.TRENDING_HALF_LIFE_DAYS),          # 0 = no decay
        "rating_weight": TRENDING_RATING_WEIGHT,
        "download_weight": TRENDING_DOWNLOAD_WEIGHT,
    }


class NoteQuerySet(models.QuerySet):
    def with_stats(self):
        """Annotate rating/comment counts and the trending score.

        trending_score = recency * (0.7 * bayes_rating + 0.3 * normalised downloads)

        * bayes_rating = (v/(v+m))*R + (m/(v+m))*C  where R is the note's mean
          rating, v its rating count, C the global mean rating and m a minimum
          vote count (settings.BAYESIAN_MIN_VOTES). Few ratings pull a note
          towards the global mean, so one 5-star vote cannot top the chart. A note
          with no ratings has no evidence at all and contributes 0 for this term.
        * downloads are scaled to 0-5 against the most-downloaded note, so both
          terms live on the same scale.
        * recency = 0.5 ** (age_days / half_life) (settings.TRENDING_HALF_LIFE_DAYS,
          0 disables it), so older notes decay exponentially.

        avg_rating stays the plain mean shown to users. Everything is computed in
        the database; C, m and the max download count are single scalars fetched
        once per query.
        """
        p = ranking_params()
        max_dl = Note.objects.aggregate(m=Max("downloads"))["m"] or 0
        if max_dl:
            norm = ExpressionWrapper(
                F("downloads") * Value(RATING_SCALE) / Value(float(max_dl)),
                output_field=FloatField(),
            )
        else:
            norm = Value(0.0, output_field=FloatField())

        votes = Cast(F("rating_count"), FloatField())
        m = Value(p["min_votes"], output_field=FloatField())
        bayes = ExpressionWrapper(
            (votes / (votes + m)) * Coalesce(F("avg_rating"), Value(0.0))
            + (m / (votes + m)) * Value(p["global_mean"], output_field=FloatField()),
            output_field=FloatField(),
        )
        if p["half_life_days"] > 0:
            age = Greatest(AgeInDays(Value(timezone.now(), output_field=DateTimeField()), F("upload_date")),
                           Value(0.0, output_field=FloatField()))
            recency = Power(Value(0.5, output_field=FloatField()),
                            age / Value(p["half_life_days"], output_field=FloatField()),
                            output_field=FloatField())
        else:
            recency = Value(1.0, output_field=FloatField())

        return self.annotate(
            avg_rating=Avg("ratings__stars"),
            rating_count=Count("ratings", distinct=True),
            comment_count=Count("comments", distinct=True),
        ).annotate(
            bayes_rating=Case(
                When(rating_count=0, then=Value(0.0)),
                default=bayes,
                output_field=FloatField(),
            ),
            recency_factor=recency,
        ).annotate(
            trending_score=ExpressionWrapper(
                (Value(p["rating_weight"]) * F("bayes_rating") + Value(p["download_weight"]) * norm)
                * F("recency_factor"),
                output_field=FloatField(),
            )
        )


class Note(models.Model):
    title = models.CharField(max_length=160)
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="notes")
    uploader = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notes")
    file = models.FileField(
        upload_to="notes/%Y/%m/",
        validators=[FileExtensionValidator(ALLOWED_EXTENSIONS)],
    )
    description = models.TextField(max_length=1000, blank=True)
    tags = models.CharField(max_length=200, blank=True, help_text="Comma-separated, e.g. unit-2, midterm, handwritten")
    upload_date = models.DateTimeField(auto_now_add=True)
    downloads = models.PositiveIntegerField(default=0)

    # Derived at upload time (see notes/textutils.py, similarity.py, previews.py).
    extracted_text = models.TextField(blank=True, default="", editable=False)
    similarity_score = models.FloatField(
        null=True, blank=True, editable=False,
        help_text="Highest TF-IDF cosine similarity (0-1) to another note in the same subject at upload time.",
    )
    duplicate_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="possible_duplicates",
        editable=False, help_text="The closest existing note, set when similarity reached the threshold.",
    )
    thumbnail = models.FileField(upload_to="thumbs/%Y/%m/", blank=True, editable=False)

    objects = NoteQuerySet.as_manager()

    class Meta:
        ordering = ["-upload_date"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("note_detail", args=[self.pk])

    @property
    def tag_list(self):
        return [t.strip() for t in self.tags.split(",") if t.strip()]

    @property
    def filename(self):
        return os.path.basename(self.file.name)

    @property
    def extension(self):
        return os.path.splitext(self.file.name)[1].lstrip(".").upper()

    @property
    def is_pdf(self):
        return self.extension == "PDF"

    @property
    def is_likely_duplicate(self):
        return (
            self.similarity_score is not None
            and self.similarity_score >= settings.DUPLICATE_SIMILARITY_THRESHOLD
        )


class Rating(models.Model):
    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="ratings")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ratings")
    stars = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("note", "user")  # one rating per user per note, enforced by the DB

    def __str__(self):
        return f"{self.user} rated {self.note} {self.stars}"


class Comment(models.Model):
    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comments")
    text = models.TextField(max_length=1000)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["timestamp"]

    def __str__(self):
        return f"{self.user} on {self.note}"


class Bookmark(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bookmarks")
    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="bookmarks")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "note")
        ordering = ["-created"]

    def __str__(self):
        return f"{self.user} saved {self.note}"


class Report(models.Model):
    REASON_CHOICES = [
        ("duplicate", "Duplicate of another note"),
        ("spam", "Spam"),
        ("wrong_subject", "Wrong subject"),
        ("inappropriate", "Inappropriate content"),
        ("other", "Other"),
    ]
    STATUS_OPEN, STATUS_RESOLVED, STATUS_DISMISSED = "open", "resolved", "dismissed"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_RESOLVED, "Resolved"),
        (STATUS_DISMISSED, "Dismissed"),
    ]

    # SET_NULL + a title snapshot so the audit trail survives when a moderator removes the note.
    note = models.ForeignKey(Note, null=True, on_delete=models.SET_NULL, related_name="reports")
    note_title = models.CharField(max_length=160, blank=True, editable=False)
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reports_filed")
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    details = models.TextField(max_length=500, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reports_handled",
    )
    created = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]
        constraints = [
            models.UniqueConstraint(
                fields=["note", "reporter"], condition=Q(status="open"), name="one_open_report_per_user_note",
            ),
        ]

    def __str__(self):
        return f"{self.get_reason_display()} report on {self.note_title or self.note_id} by {self.reporter}"

    def save(self, *args, **kwargs):
        if self.note_id and not self.note_title:
            self.note_title = self.note.title[:160]
        super().save(*args, **kwargs)

    def close(self, user, status):
        """Mark the report resolved or dismissed by `user`."""
        self.status = status
        self.resolved_by = user
        self.resolved_at = timezone.now()
        self.save(update_fields=["status", "resolved_by", "resolved_at"])
