from django.conf import settings
from django.contrib import admin

from .models import Bookmark, Comment, Note, Rating, Report, Subject


class LikelyDuplicateFilter(admin.SimpleListFilter):
    """Filter notes by the similarity score stored at upload time (see DUPLICATE_SIMILARITY_THRESHOLD)."""

    title = "duplicate check"
    parameter_name = "dup"

    def lookups(self, request, model_admin):
        return [("likely", "Likely duplicates"), ("clean", "Not flagged")]

    def queryset(self, request, queryset):
        threshold = settings.DUPLICATE_SIMILARITY_THRESHOLD
        if self.value() == "likely":
            return queryset.filter(similarity_score__gte=threshold)
        if self.value() == "clean":
            return queryset.exclude(similarity_score__gte=threshold)
        return queryset


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("name", "semester", "branch")
    list_filter = ("semester", "branch")
    search_fields = ("name",)


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("title", "subject", "uploader", "downloads", "upload_date", "similarity", "duplicate_of")
    list_filter = (LikelyDuplicateFilter, "subject__branch", "subject__semester", "subject")
    readonly_fields = ("similarity_score", "duplicate_of", "thumbnail")
    exclude = ("extracted_text",)
    list_select_related = ("subject", "uploader", "duplicate_of")
    search_fields = ("title", "tags", "description", "uploader__username")
    date_hierarchy = "upload_date"
    actions = ["remove_selected"]

    @admin.display(description="Similarity", ordering="similarity_score")
    def similarity(self, obj):
        return "-" if obj.similarity_score is None else f"{obj.similarity_score:.0%}"

    @admin.action(description="Remove selected notes (spam / duplicates)")
    def remove_selected(self, request, queryset):
        # Iterate so post_delete signals also clean up the files on disk.
        n = queryset.count()
        for note in queryset:
            note.delete()
        self.message_user(request, f"Removed {n} note(s).")

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)  # bulk delete bypasses file cleanup
        return actions


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("user", "note", "timestamp")
    search_fields = ("text", "user__username")


@admin.register(Rating)
class RatingAdmin(admin.ModelAdmin):
    list_display = ("note", "user", "stars", "created")
    list_filter = ("stars",)


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("note_title", "reason", "status", "reporter", "resolved_by", "created")
    list_filter = ("status", "reason")
    search_fields = ("note_title", "details", "reporter__username")
    readonly_fields = ("note", "note_title", "reporter", "created", "resolved_at")


admin.site.register(Bookmark)
