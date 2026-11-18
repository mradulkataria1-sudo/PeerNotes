from django.core.management.base import BaseCommand

from notes.models import Note
from notes.services import analyse_upload, apply_derived


class Command(BaseCommand):
    help = (
        "Extract text, PDF thumbnails and duplicate scores for notes uploaded before those features "
        "existed. Safe to re-run: by default only notes with no extracted text or no thumbnail are touched."
    )

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Reprocess every note, not just the incomplete ones.")

    def handle(self, *args, **opts):
        done = skipped = 0
        for note in Note.objects.select_related("subject").order_by("upload_date", "pk"):
            needs_thumb = note.is_pdf and not note.thumbnail
            if not (opts["all"] or not note.extracted_text or needs_thumb):
                continue
            try:
                with note.file.open("rb") as fh:
                    fh.name = note.file.name  # so the extension is known
                    text, ranked = analyse_upload(fh, note.subject, exclude_pk=note.pk, before=note.upload_date)
                    apply_derived(note, fh, text, ranked)
            except (FileNotFoundError, ValueError, OSError) as exc:
                skipped += 1
                self.stderr.write(f"Skipped note {note.pk} ({note.title}): {exc}")
                continue
            note.save(update_fields=["extracted_text", "similarity_score", "duplicate_of", "thumbnail"])
            done += 1
        if opts["verbosity"] > 0:
            self.stdout.write(self.style.SUCCESS(f"Processed {done} note(s); skipped {skipped}."))
