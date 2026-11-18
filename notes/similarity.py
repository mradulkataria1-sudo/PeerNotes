"""Duplicate detection: TF-IDF cosine similarity between notes of one subject."""
import logging
from dataclasses import dataclass

from django.conf import settings

from .models import Note

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SimilarNote:
    note: Note
    score: float  # 0-1

    @property
    def percent(self):
        return round(self.score * 100)


def similarity_scores(text, corpus):
    """Cosine similarity of `text` against each string in `corpus` (TF-IDF vectors).

    The vocabulary is fitted on corpus + text so rare shared terms weigh more than
    common ones. Returns [] when there is nothing meaningful to compare.
    """
    if not corpus or not text:
        return []
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    try:
        matrix = TfidfVectorizer(stop_words="english", sublinear_tf=True).fit_transform(corpus + [text])
    except ValueError:  # empty vocabulary (e.g. only stop words)
        return []
    return [float(s) for s in cosine_similarity(matrix[-1], matrix[:-1]).ravel()]


def rank_similar_notes(text, subject, exclude_pk=None, before=None):
    """Every comparable note in `subject`, most similar first, as SimilarNote items.

    Notes without enough extracted text (images, legacy rows, tiny files) are
    skipped, as is `text` itself when it is too short to compare. `before` limits the
    comparison to notes uploaded earlier (used when back-filling old data so that
    only the later copy of a pair is flagged).
    """
    min_chars = settings.DUPLICATE_MIN_CHARS
    if len((text or "").strip()) < min_chars:
        return []
    qs = Note.objects.filter(subject=subject).exclude(pk=exclude_pk).exclude(extracted_text="")
    if before is not None:
        qs = qs.filter(upload_date__lt=before)
    candidates = [n for n in qs.select_related("uploader") if len(n.extracted_text.strip()) >= min_chars]
    scores = similarity_scores(text, [n.extracted_text for n in candidates])
    ranked = [SimilarNote(n, s) for n, s in zip(candidates, scores)]
    ranked.sort(key=lambda h: h.score, reverse=True)
    return ranked


def likely_duplicates(ranked, threshold=None, limit=None):
    """The entries of a rank_similar_notes() result at or above the threshold."""
    threshold = settings.DUPLICATE_SIMILARITY_THRESHOLD if threshold is None else threshold
    limit = settings.DUPLICATE_MAX_RESULTS if limit is None else limit
    return [h for h in ranked if h.score >= threshold][:limit]


def find_similar_notes(text, subject, exclude_pk=None, threshold=None, limit=None):
    """Convenience wrapper: ranked notes that reach the duplicate threshold."""
    return likely_duplicates(rank_similar_notes(text, subject, exclude_pk), threshold, limit)
