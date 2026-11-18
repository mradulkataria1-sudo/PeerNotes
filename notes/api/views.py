"""REST API. Function-based views that enforce the same rules as the web views."""
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle

from ..filters import apply_note_filters, int_or_none
from ..models import Bookmark, Comment, Note, Rating
from .pagination import StandardPagination
from .serializers import (
    CommentSerializer, NoteDetailSerializer, NoteSerializer, ProfileSerializer, RatingInputSerializer,
)

TRENDING_DEFAULT_LIMIT = 10
TRENDING_MAX_LIMIT = 50


def _notes():
    return Note.objects.select_related("subject", "uploader").with_stats()


def _serialize_note(request, pk, detail=False):
    note = get_object_or_404(_notes(), pk=pk)
    cls = NoteDetailSerializer if detail else NoteSerializer
    return note, cls(note, context={"request": request})


class TokenView(ObtainAuthToken):
    """POST username + password, get {"token": ...}. Throttled harder than the rest of the API."""

    throttle_classes = [AnonRateThrottle, ScopedRateThrottle]
    throttle_scope = "token_auth"


@api_view(["GET"])
def note_list(request):
    """Same q / subject / semester / branch / tag / sort filters as the web feed."""
    qs, _ = apply_note_filters(_notes(), request.query_params)
    paginator = StandardPagination()
    page = paginator.paginate_queryset(qs, request)
    return paginator.get_paginated_response(NoteSerializer(page, many=True, context={"request": request}).data)


@api_view(["GET"])
def note_detail(request, pk):
    _, ser = _serialize_note(request, pk, detail=True)
    return Response(ser.data)


@api_view(["GET"])
def trending(request):
    """Top notes by trending score. ?limit=N (default 10, max 50)."""
    limit = int_or_none(request.query_params.get("limit")) or TRENDING_DEFAULT_LIMIT
    limit = max(1, min(limit, TRENDING_MAX_LIMIT))
    qs = _notes().order_by("-trending_score", "-upload_date")[:limit]
    return Response(NoteSerializer(qs, many=True, context={"request": request}).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def rate_note(request, pk):
    note = get_object_or_404(Note, pk=pk)
    if note.uploader == request.user:
        raise PermissionDenied("You can't rate your own note.")
    inp = RatingInputSerializer(data=request.data)
    inp.is_valid(raise_exception=True)
    stars = inp.validated_data["stars"]
    _, created = Rating.objects.update_or_create(note=note, user=request.user, defaults={"stars": stars})
    _, ser = _serialize_note(request, pk)  # fresh stats incl. the new rating
    data = ser.data
    return Response(
        {"stars": stars, "avg_rating": data["avg_rating"], "rating_count": data["rating_count"],
         "trending_score": data["trending_score"]},
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@api_view(["GET", "POST"])
def comments(request, pk):
    note = get_object_or_404(Note, pk=pk)
    if request.method == "GET":
        paginator = StandardPagination()
        page = paginator.paginate_queryset(note.comments.select_related("user"), request)
        return paginator.get_paginated_response(CommentSerializer(page, many=True).data)
    if not request.user.is_authenticated:  # unreachable with IsAuthenticatedOrReadOnly, kept explicit
        raise PermissionDenied()
    ser = CommentSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    comment = Comment.objects.create(note=note, user=request.user, text=ser.validated_data["text"])
    return Response(CommentSerializer(comment).data, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def toggle_bookmark(request, pk):
    note = get_object_or_404(Note, pk=pk)
    try:
        with transaction.atomic():
            obj, created = Bookmark.objects.get_or_create(user=request.user, note=note)
    except IntegrityError:
        created = False
        obj = Bookmark.objects.filter(user=request.user, note=note).first()
    if not created and obj is not None:
        obj.delete()
    return Response({"bookmarked": created})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    return Response(ProfileSerializer.build(request.user))
