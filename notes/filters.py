"""Note feed filtering shared by the web view and the REST API."""
from django.db.models import F, Q

SORTS = {
    "trending": "-trending_score",
    "newest": "-upload_date",
    "rated": "-avg_rating",
    "downloads": "-downloads",
}


def int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def apply_note_filters(qs, params):
    """Filter and order a with_stats() queryset from a QueryDict-like `params`.

    Returns (queryset, cleaned_filters). Unknown/invalid values are ignored.
    """
    q = params.get("q", "").strip()
    subject = int_or_none(params.get("subject"))
    semester = int_or_none(params.get("semester"))
    branch = params.get("branch", "").strip()
    tag = params.get("tag", "").strip().lower()
    sort = params.get("sort", "trending")
    if sort not in SORTS:
        sort = "trending"

    if q:
        qs = qs.filter(
            Q(title__icontains=q) | Q(description__icontains=q)
            | Q(tags__icontains=q) | Q(subject__name__icontains=q)
        )
    if subject:
        qs = qs.filter(subject_id=subject)
    if semester:
        qs = qs.filter(subject__semester=semester)
    if branch:
        qs = qs.filter(subject__branch=branch)
    if tag:
        qs = qs.filter(tags__icontains=tag)

    order = F("avg_rating").desc(nulls_last=True) if sort == "rated" else SORTS[sort]
    qs = qs.order_by(order, "-upload_date")
    return qs, {"q": q, "subject": subject, "semester": semester, "branch": branch, "tag": tag, "sort": sort}
