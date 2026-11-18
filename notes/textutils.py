"""Best-effort text extraction from uploaded notes.

Every function here is defensive: a corrupt or unsupported file must never stop
an upload, so failures are logged and an empty string is returned.
"""
import io
import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {"txt", "md"}
EXTRACTABLE_EXTENSIONS = {"pdf", "docx"} | TEXT_EXTENSIONS


def _read_bytes(f):
    """Return the bytes of an uploaded/stored file and rewind it."""
    try:
        f.seek(0)
    except (AttributeError, ValueError, OSError):
        pass
    data = f.read()
    try:
        f.seek(0)
    except (AttributeError, ValueError, OSError):
        pass
    return data


def _from_pdf(data):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001 - any failure means "can't read it"
            return ""
    parts = []
    for page in reader.pages[: settings.EXTRACT_MAX_PDF_PAGES]:
        parts.append(page.extract_text() or "")
        if sum(map(len, parts)) >= settings.EXTRACT_MAX_CHARS:
            break
    return "\n".join(parts)


def _from_docx(data):
    from docx import Document

    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def _from_text(data):
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    return ""


def extract_text(f, name=None):
    """Extract plain text from a PDF, DOCX, TXT or MD file object.

    Images and unsupported types (doc, ppt, pptx, png, jpg...) return "".
    """
    name = name or getattr(f, "name", "") or ""
    ext = os.path.splitext(name)[1].lstrip(".").lower()
    if ext not in EXTRACTABLE_EXTENSIONS:
        return ""
    try:
        data = _read_bytes(f)
        if ext == "pdf":
            text = _from_pdf(data)
        elif ext == "docx":
            text = _from_docx(data)
        else:
            text = _from_text(data)
    except Exception as exc:  # noqa: BLE001 - corrupt files must not block uploads
        logger.warning("Text extraction failed for %s: %s", name, exc)
        return ""
    text = text.replace("\x00", "")  # PostgreSQL text columns reject NUL
    return text[: settings.EXTRACT_MAX_CHARS].strip()
