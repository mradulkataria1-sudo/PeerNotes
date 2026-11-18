import csv
import logging
import os
import uuid
from collections import defaultdict

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, F, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from .filters import apply_note_filters
from .filters import int_or_none as _int_or_none
from .forms import CommentForm, NoteForm, ReportForm, SignUpForm
from .models import Bookmark, Comment, Note, Rating, Report, Subject, ranking_params
from .services import analyse_upload, apply_derived
from .similarity import likely_duplicates

logger = logging.getLogger(__name__)

PAGE_SIZE = 9
PENDING_KEY = "pending_note_upload"  # session key for an upload awaiting duplicate confirmation


def _is_ajax(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest" or "partial" in request.GET


def _bookmarked_ids(user, notes):
    if not user.is_authenticated:
        return set()
    return set(Bookmark.objects.filter(user=user, note__in=notes).values_list("note_id", flat=True))


# --------------------------------------------------------------- browse / search

def note_list(request):
    """Searchable, filterable note feed. Returns a partial for AJAX requests."""
    qs = Note.objects.select_related("subject", "uploader").with_stats()
    qs, filters = apply_note_filters(qs, request.GET)

    page_obj = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    params.pop("partial", None)

    ctx = {
        "page_obj": page_obj,
        "bookmarked": _bookmarked_ids(request.user, page_obj.object_list),
        "querystring": params.urlencode(),
        "total": page_obj.paginator.count,
    }
    if _is_ajax(request):
        return render(request, "notes/_note_grid.html", ctx)

    ctx.update(
        subjects=Subject.objects.all(),
        semesters=Subject.SEMESTER_CHOICES,
        branches=Subject.BRANCH_CHOICES,
        f=filters,
    )
    return render(request, "notes/note_list.html", ctx)


def note_detail(request, pk):
    note = get_object_or_404(Note.objects.select_related("subject", "uploader").with_stats(), pk=pk)
    my_rating = bookmarked = None
    already_reported = False
    is_owner = request.user == note.uploader
    if request.user.is_authenticated:
        my_rating = Rating.objects.filter(note=note, user=request.user).values_list("stars", flat=True).first()
        bookmarked = Bookmark.objects.filter(note=note, user=request.user).exists()
        already_reported = Report.objects.filter(
            note=note, reporter=request.user, status=Report.STATUS_OPEN).exists()
    return render(request, "notes/note_detail.html", {
        "can_report": request.user.is_authenticated and not is_owner,
        "already_reported": already_reported,
        "report_reasons": Report.REASON_CHOICES,
        "report_max": settings.REPORT_DETAILS_MAX_CHARS,
        "duplicate_of": note.duplicate_of if request.user.has_perm("notes.view_report") else None,
        "note": note,
        "comments": note.comments.select_related("user"),
        "comment_form": CommentForm(),
        "my_rating": my_rating or 0,
        "bookmarked": bookmarked,
        "stars": range(1, 6),
        "is_owner": is_owner,
    })


# --------------------------------------------------------------- CRUD

def _discard_pending(request):
    pending = request.session.pop(PENDING_KEY, None)
    if pending:
        try:
            default_storage.delete(pending["path"])
        except Exception:  # noqa: BLE001 - a leftover temp file is harmless
            logger.warning("Could not delete pending upload %s", pending.get("path"), exc_info=True)


def _stash_pending(request, form, f):
    """Park a flagged upload on disk + in the session until the user confirms or discards it."""
    _discard_pending(request)
    f.seek(0)
    path = default_storage.save(f"pending/{uuid.uuid4().hex}_{os.path.basename(f.name)}", f)
    cd = form.cleaned_data
    request.session[PENDING_KEY] = {
        "path": path, "name": f.name,
        "data": {"title": cd["title"], "subject": cd["subject"].pk,
                 "description": cd["description"], "tags": cd["tags"]},
    }


def _save_new_note(request, form, f, text, ranked, flagged):
    note = form.save(commit=False)
    note.uploader = request.user
    apply_derived(note, f, text, ranked)
    note.save()
    if flagged:
        messages.warning(request, "Note uploaded. It was flagged as a possible duplicate for moderators to review.")
    else:
        messages.success(request, "Note uploaded.")
    return redirect(note)


def _duplicate_warning(request, form, matches, heading="Upload a note"):
    return render(request, "notes/note_form.html", {
        "form": form, "heading": heading, "similar": matches,
        "threshold_pct": round(settings.DUPLICATE_SIMILARITY_THRESHOLD * 100),
        "pending": request.session.get(PENDING_KEY),
    })


@login_required
def note_create(request):
    """Upload a note. Near-duplicates in the same subject must be confirmed first."""
    if request.method == "POST" and request.POST.get("confirm_duplicate"):
        pending = request.session.get(PENDING_KEY)
        if not pending:
            messages.error(request, "That upload expired. Please choose the file again.")
            return redirect("note_create")
        if request.POST.get("action") == "cancel":
            _discard_pending(request)
            messages.info(request, "Upload discarded.")
            return redirect("note_list")
        try:
            with default_storage.open(pending["path"], "rb") as fh:
                upload = SimpleUploadedFile(pending["name"], fh.read())
        except (FileNotFoundError, OSError):
            _discard_pending(request)
            messages.error(request, "That upload expired. Please choose the file again.")
            return redirect("note_create")
        form = NoteForm(pending["data"], {"file": upload})
        if form.is_valid():
            f = form.cleaned_data["file"]
            text, ranked = analyse_upload(f, form.cleaned_data["subject"])
            flagged = bool(likely_duplicates(ranked))
            response = _save_new_note(request, form, f, text, ranked, flagged)
            _discard_pending(request)
            return response
        _discard_pending(request)
        return render(request, "notes/note_form.html", {"form": form, "heading": "Upload a note"})

    form = NoteForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        f = form.cleaned_data["file"]
        text, ranked = analyse_upload(f, form.cleaned_data["subject"])
        matches = likely_duplicates(ranked)
        if matches:
            _stash_pending(request, form, f)
            return _duplicate_warning(request, form, matches)
        _discard_pending(request)  # a fresh clean upload supersedes any abandoned warning
        return _save_new_note(request, form, f, text, ranked, flagged=False)
    return render(request, "notes/note_form.html", {"form": form, "heading": "Upload a note"})


@login_required
def note_update(request, pk):
    note = get_object_or_404(Note, pk=pk)
    if note.uploader != request.user and not request.user.has_perm("notes.change_note"):
        return HttpResponseForbidden("You can only edit your own notes.")
    form = NoteForm(request.POST or None, request.FILES or None, instance=note)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if "file" in form.changed_data:
            # New file: refresh text, duplicate flag and thumbnail (flag only, never blocks an edit).
            f = form.cleaned_data["file"]
            text, ranked = analyse_upload(f, obj.subject, exclude_pk=obj.pk)
            apply_derived(obj, f, text, ranked)
        obj.save()
        messages.success(request, "Note updated.")
        return redirect(note)
    return render(request, "notes/note_form.html", {"form": form, "heading": "Edit note", "note": note})


@login_required
def note_delete(request, pk):
    note = get_object_or_404(Note, pk=pk)
    if note.uploader != request.user and not request.user.has_perm("notes.delete_note"):
        return HttpResponseForbidden("Only the uploader or a moderator can delete this note.")
    if request.method == "POST":
        note.delete()
        messages.success(request, "Note removed.")
        return redirect("note_list")
    return render(request, "notes/note_confirm_delete.html", {"note": note})


@login_required
def note_download(request, pk):
    note = get_object_or_404(Note, pk=pk)
    Note.objects.filter(pk=pk).update(downloads=F("downloads") + 1)
    return FileResponse(note.file.open("rb"), as_attachment=True, filename=note.filename)


@login_required
@xframe_options_sameorigin  # lets the note page embed this response in an <iframe>
def note_preview(request, pk):
    """Serve a PDF inline for the in-page preview. Does not count as a download."""
    note = get_object_or_404(Note, pk=pk)
    if not note.is_pdf:
        raise Http404("Only PDF notes can be previewed.")
    try:
        fh = note.file.open("rb")
    except (FileNotFoundError, ValueError):
        raise Http404("The file is missing.")
    return FileResponse(fh, as_attachment=False, content_type="application/pdf", filename=note.filename)


# --------------------------------------------------------------- AJAX actions

@require_POST
def rate_note(request, pk):
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "Log in to rate notes."}, status=401)
    note = get_object_or_404(Note, pk=pk)
    if note.uploader == request.user:
        return JsonResponse({"ok": False, "error": "You can't rate your own note."}, status=403)
    stars = _int_or_none(request.POST.get("stars"))
    if stars is None or not 1 <= stars <= 5:
        return JsonResponse({"ok": False, "error": "Choose 1 to 5 stars."}, status=400)

    Rating.objects.update_or_create(note=note, user=request.user, defaults={"stars": stars})
    agg = note.ratings.aggregate(avg=Avg("stars"), n=Count("id"))
    return JsonResponse({"ok": True, "stars": stars, "avg": round(agg["avg"], 1), "count": agg["n"]})


@require_POST
def add_comment(request, pk):
    note = get_object_or_404(Note, pk=pk)
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "Log in to comment."}, status=401)
    form = CommentForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"ok": False, "error": "Write something before posting."}, status=400)
    comment = form.save(commit=False)
    comment.note, comment.user = note, request.user
    comment.save()
    html = render_to_string("notes/_comment.html", {"c": comment}, request=request)
    return JsonResponse({"ok": True, "html": html, "count": note.comments.count()})


@login_required
@require_POST
def delete_comment(request, pk):
    comment = get_object_or_404(Comment, pk=pk)
    if comment.user != request.user and not request.user.has_perm("notes.delete_comment"):
        return HttpResponseForbidden()
    note = comment.note
    comment.delete()
    messages.success(request, "Comment removed.")
    return redirect(note)


@require_POST
def toggle_bookmark(request, pk):
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "Log in to save notes."}, status=401)
    note = get_object_or_404(Note, pk=pk)
    obj, created = Bookmark.objects.get_or_create(user=request.user, note=note)
    if not created:
        obj.delete()
    return JsonResponse({"ok": True, "bookmarked": created})


@login_required
def bookmarks(request):
    notes = (
        Note.objects.filter(bookmarks__user=request.user)
        .select_related("subject", "uploader").with_stats().order_by("-bookmarks__created")
    )
    page_obj = Paginator(notes, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(request, "notes/bookmarks.html", {
        "page_obj": page_obj, "bookmarked": {n.pk for n in page_obj}, "querystring": "",
    })


# --------------------------------------------------------------- reports & moderation

@require_POST
def report_note(request, pk):
    """AJAX: a student reports a note for moderator review."""
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "Log in to report notes."}, status=401)
    note = get_object_or_404(Note, pk=pk)
    if note.uploader == request.user:
        return JsonResponse({"ok": False, "error": "You can't report your own note."}, status=403)
    form = ReportForm(request.POST)
    if not form.is_valid():
        errors = " ".join(e for errs in form.errors.values() for e in errs)
        return JsonResponse({"ok": False, "error": errors or "Invalid report."}, status=400)
    duplicate_msg = "You already have an open report on this note. A moderator will review it."
    if Report.objects.filter(note=note, reporter=request.user, status=Report.STATUS_OPEN).exists():
        return JsonResponse({"ok": False, "error": duplicate_msg}, status=409)
    try:
        with transaction.atomic():
            report = form.save(commit=False)
            report.note, report.reporter = note, request.user
            report.save()
    except IntegrityError:  # two simultaneous submits: the DB constraint is the final guard
        return JsonResponse({"ok": False, "error": duplicate_msg}, status=409)
    return JsonResponse({"ok": True, "message": "Thanks. A moderator will review this note."})


@login_required
@permission_required("notes.view_report", raise_exception=True)
def moderation_queue(request):
    """Open reports with remove / dismiss / resolve actions, gated by Django permissions."""
    open_reports = (
        Report.objects.filter(status=Report.STATUS_OPEN)
        .select_related("note", "note__subject", "note__uploader", "reporter")
        .order_by("created")
    )
    handled = (
        Report.objects.exclude(status=Report.STATUS_OPEN)
        .select_related("note", "reporter", "resolved_by").order_by("-resolved_at")[:10]
    )
    return render(request, "notes/moderation.html", {
        "reports": open_reports,
        "handled": handled,
        "can_resolve": request.user.has_perm("notes.change_report"),
        "can_remove": request.user.has_perm("notes.delete_note"),
    })


def _csv_safe(value):
    """Neutralise spreadsheet formulas: a cell starting with = + - @ would execute in Excel."""
    value = "" if value is None else str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


@login_required
@permission_required("notes.view_report", raise_exception=True)
def moderation_export(request):
    """CSV download of every report (open and handled) for moderators and reviewers."""
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="peernotes-reports.csv"'
    writer = csv.writer(response)
    writer.writerow(["id", "note_id", "note_title", "subject", "uploader", "reporter", "reason", "details",
                     "status", "resolved_by", "created", "resolved_at"])
    rows = Report.objects.select_related("note__subject", "note__uploader", "reporter", "resolved_by").order_by("created")
    for r in rows:
        writer.writerow([_csv_safe(v) for v in [
            r.pk, r.note_id, r.note_title,
            r.note.subject.name if r.note else "", r.note.uploader.username if r.note else "",
            r.reporter.username, r.get_reason_display(), r.details, r.get_status_display(),
            r.resolved_by.username if r.resolved_by else "",
            r.created.isoformat(), r.resolved_at.isoformat() if r.resolved_at else "",
        ]])
    return response


@login_required
@require_POST
def report_action(request, pk, action):
    """Apply remove / dismiss / resolve to one report."""
    if action not in ("remove", "dismiss", "resolve"):
        raise Http404
    if not request.user.has_perm("notes.change_report"):
        return HttpResponseForbidden("You can't resolve reports.")
    if action == "remove" and not request.user.has_perm("notes.delete_note"):
        return HttpResponseForbidden("Only moderators can remove notes.")

    report = get_object_or_404(Report, pk=pk)
    if report.status != Report.STATUS_OPEN:
        messages.info(request, "That report was already handled.")
        return redirect("moderation_queue")

    if action == "remove":
        note = report.note
        with transaction.atomic():
            if note is not None:
                # Every open report on this note is settled by its removal.
                Report.objects.filter(note=note, status=Report.STATUS_OPEN).update(
                    status=Report.STATUS_RESOLVED, resolved_by=request.user, resolved_at=timezone.now())
                note.delete()  # instance delete so the post_delete signal removes the files
            else:
                report.close(request.user, Report.STATUS_RESOLVED)
        messages.success(request, "Note removed and its reports resolved.")
    elif action == "dismiss":
        report.close(request.user, Report.STATUS_DISMISSED)
        messages.success(request, "Report dismissed.")
    else:
        report.close(request.user, Report.STATUS_RESOLVED)
        messages.success(request, "Report marked as resolved.")
    return redirect("moderation_queue")


# --------------------------------------------------------------- dashboard

def dashboard(request):
    """Trending notes, top contributors and per-subject analytics (ORM aggregation)."""
    trending = Note.objects.select_related("subject", "uploader").with_stats().order_by("-trending_score")[:10]

    contributors = list(
        User.objects.annotate(
            note_count=Count("notes", distinct=True),
            total_downloads=Coalesce(Sum("notes__downloads"), 0),
        ).filter(note_count__gt=0).order_by("-note_count", "-total_downloads")[:10]
    )
    received = {
        r["note__uploader"]: r["avg"]
        for r in Rating.objects.values("note__uploader").annotate(avg=Avg("stars"))
    }
    for u in contributors:
        u.avg_received = received.get(u.pk)

    downloads = {r["subject"]: r["d"] for r in Note.objects.values("subject").annotate(d=Sum("downloads"))}
    subjects = list(
        Subject.objects.annotate(
            note_count=Count("notes", distinct=True),
            rating_count=Count("notes__ratings", distinct=True),
            avg_rating=Avg("notes__ratings__stars"),
        ).filter(note_count__gt=0).order_by(F("avg_rating").desc(nulls_last=True))
    )
    monthly = defaultdict(list)
    rows = (
        Rating.objects.annotate(month=TruncMonth("created"))
        .values("note__subject", "month").annotate(avg=Avg("stars")).order_by("month")
    )
    for r in rows:
        monthly[r["note__subject"]].append({"month": r["month"], "avg": r["avg"], "pct": round(r["avg"] / 5 * 100)})
    for s in subjects:
        s.downloads = downloads.get(s.pk, 0)
        s.trend = monthly.get(s.pk, [])[-6:]

    return render(request, "notes/dashboard.html", {
        "trending": trending, "contributors": contributors, "subjects": subjects,
        "ranking": ranking_params(),
    })


# --------------------------------------------------------------- auth

def signup(request):
    if request.user.is_authenticated:
        return redirect("note_list")
    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, f"Welcome, {user.username}.")
        return redirect("note_list")
    return render(request, "registration/signup.html", {"form": form})
