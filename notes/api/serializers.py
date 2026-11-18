from django.db.models import Avg, Count, Sum
from rest_framework import serializers

from ..models import Bookmark, Comment, Note, Rating, Subject


class SubjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subject
        fields = ["id", "name", "semester", "branch"]


class NoteSerializer(serializers.ModelSerializer):
    """Expects instances from Note.objects.with_stats() (avg_rating, rating_count, ...)."""

    subject = SubjectSerializer(read_only=True)
    uploader = serializers.CharField(source="uploader.username", read_only=True)
    tags = serializers.ListField(source="tag_list", child=serializers.CharField(), read_only=True)
    avg_rating = serializers.SerializerMethodField()
    rating_count = serializers.IntegerField(read_only=True)
    comment_count = serializers.IntegerField(read_only=True)
    trending_score = serializers.SerializerMethodField()
    file_type = serializers.CharField(source="extension", read_only=True)
    thumbnail_url = serializers.SerializerMethodField()
    web_url = serializers.SerializerMethodField()

    class Meta:
        model = Note
        fields = [
            "id", "title", "subject", "uploader", "description", "tags", "upload_date", "downloads",
            "file_type", "avg_rating", "rating_count", "comment_count", "trending_score",
            "thumbnail_url", "web_url",
        ]
        read_only_fields = fields

    def get_avg_rating(self, obj):
        return None if obj.avg_rating is None else round(obj.avg_rating, 2)

    def get_trending_score(self, obj):
        return round(obj.trending_score, 4)

    def _absolute(self, path):
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request else path

    def get_thumbnail_url(self, obj):
        return self._absolute(obj.thumbnail.url) if obj.thumbnail else None

    def get_web_url(self, obj):
        return self._absolute(obj.get_absolute_url())


class NoteDetailSerializer(NoteSerializer):
    my_rating = serializers.SerializerMethodField()
    bookmarked = serializers.SerializerMethodField()

    class Meta(NoteSerializer.Meta):
        fields = NoteSerializer.Meta.fields + ["my_rating", "bookmarked"]
        read_only_fields = fields

    def _user(self):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        return user if user is not None and user.is_authenticated else None

    def get_my_rating(self, obj):
        user = self._user()
        if not user:
            return None
        return Rating.objects.filter(note=obj, user=user).values_list("stars", flat=True).first()

    def get_bookmarked(self, obj):
        user = self._user()
        return None if not user else Bookmark.objects.filter(note=obj, user=user).exists()


class RatingInputSerializer(serializers.Serializer):
    stars = serializers.IntegerField(min_value=1, max_value=5)


class CommentSerializer(serializers.ModelSerializer):
    user = serializers.CharField(source="user.username", read_only=True)
    text = serializers.CharField(max_length=1000, trim_whitespace=True)  # blank is rejected

    class Meta:
        model = Comment
        fields = ["id", "user", "text", "timestamp"]
        read_only_fields = ["id", "user", "timestamp"]


class ProfileSerializer(serializers.Serializer):
    """Current-user profile built from ORM aggregates."""

    @staticmethod
    def build(user):
        notes = user.notes.aggregate(n=Count("id"), downloads=Sum("downloads"))
        received = Rating.objects.filter(note__uploader=user).aggregate(avg=Avg("stars"), n=Count("id"))
        return {
            "id": user.pk,
            "username": user.username,
            "email": user.email,
            "date_joined": user.date_joined,
            "groups": list(user.groups.values_list("name", flat=True)),
            "can_review_reports": user.has_perm("notes.view_report"),
            "can_remove_notes": user.has_perm("notes.delete_note"),
            "notes_uploaded": notes["n"],
            "downloads_received": notes["downloads"] or 0,
            "ratings_received": received["n"],
            "avg_rating_received": None if received["avg"] is None else round(received["avg"], 2),
            "ratings_given": user.ratings.count(),
            "comments_posted": user.comments.count(),
            "bookmarks": user.bookmarks.count(),
        }
