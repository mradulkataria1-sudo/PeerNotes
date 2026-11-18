"""First-page thumbnails for PDF notes (PyMuPDF, pure Python wheel, no poppler)."""
import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)


def make_pdf_thumbnail(f):
    """Render page 1 of a PDF file object to PNG bytes, or None on any failure."""
    try:
        import pymupdf  # PyMuPDF

        try:
            f.seek(0)
        except (AttributeError, ValueError, OSError):
            pass
        data = f.read()
        try:
            f.seek(0)
        except (AttributeError, ValueError, OSError):
            pass
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass or doc.page_count == 0:
                return None
            page = doc[0]
            width = page.rect.width or 1
            zoom = settings.THUMBNAIL_WIDTH_PX / width
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            return pix.tobytes("png")
    except Exception as exc:  # noqa: BLE001 - thumbnails are decoration, never block an upload
        logger.warning("Thumbnail generation failed: %s", exc)
        return None


def thumbnail_name_for(filename):
    return os.path.splitext(os.path.basename(filename))[0] + ".png"
