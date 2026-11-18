"""Upload processing shared by the web views, seed_demo and the backfill command."""
import os

from django.conf import settings
from django.core.files.base import ContentFile

from .previews import make_pdf_thumbnail, thumbnail_name_for
from .similarity import rank_similar_notes
from .textutils import extract_text


def analyse_upload(f, subject, exclude_pk=None, before=None):
    """Extract text from a file and rank same-subject notes by similarity to it."""
    text = extract_text(f, f.name)
    return text, rank_similar_notes(text, subject, exclude_pk=exclude_pk, before=before)


def apply_derived(note, f, text, ranked):
    """Set extracted text, duplicate flag and PDF thumbnail on `note` (does not save it).

    Thumbnail failures are swallowed by make_pdf_thumbnail, so they never block a save.
    """
    note.extracted_text = text
    top = ranked[0] if ranked else None
    note.similarity_score = top.score if top else None
    note.duplicate_of = top.note if top and top.score >= settings.DUPLICATE_SIMILARITY_THRESHOLD else None
    ext = os.path.splitext(f.name)[1].lstrip(".").lower()
    if ext == "pdf":
        png = make_pdf_thumbnail(f)
        if png:
            if note.thumbnail:
                note.thumbnail.delete(save=False)
            note.thumbnail.save(thumbnail_name_for(f.name), ContentFile(png), save=False)
    elif note.thumbnail:  # file replaced by a non-PDF: drop the stale thumbnail
        note.thumbnail.delete(save=False)
        note.thumbnail = ""
