from django.urls import path

from . import views

urlpatterns = [
    path("auth/token/", views.TokenView.as_view(), name="api_token"),
    path("notes/", views.note_list, name="api_note_list"),
    path("notes/<int:pk>/", views.note_detail, name="api_note_detail"),
    path("notes/<int:pk>/rate/", views.rate_note, name="api_rate_note"),
    path("notes/<int:pk>/comments/", views.comments, name="api_comments"),
    path("notes/<int:pk>/bookmark/", views.toggle_bookmark, name="api_bookmark"),
    path("trending/", views.trending, name="api_trending"),
    path("me/", views.me, name="api_me"),
]
