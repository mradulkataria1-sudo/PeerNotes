from django.urls import path

from . import views

urlpatterns = [
    path("", views.note_list, name="note_list"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("bookmarks/", views.bookmarks, name="bookmarks"),
    path("signup/", views.signup, name="signup"),
    path("notes/new/", views.note_create, name="note_create"),
    path("notes/<int:pk>/", views.note_detail, name="note_detail"),
    path("notes/<int:pk>/edit/", views.note_update, name="note_update"),
    path("notes/<int:pk>/delete/", views.note_delete, name="note_delete"),
    path("notes/<int:pk>/download/", views.note_download, name="note_download"),
    path("notes/<int:pk>/preview/", views.note_preview, name="note_preview"),
    path("notes/<int:pk>/report/", views.report_note, name="report_note"),
    path("moderation/", views.moderation_queue, name="moderation_queue"),
    path("moderation/export.csv", views.moderation_export, name="moderation_export"),
    path("moderation/<int:pk>/<str:action>/", views.report_action, name="report_action"),
    path("notes/<int:pk>/rate/", views.rate_note, name="rate_note"),
    path("notes/<int:pk>/comment/", views.add_comment, name="add_comment"),
    path("notes/<int:pk>/bookmark/", views.toggle_bookmark, name="toggle_bookmark"),
    path("comments/<int:pk>/delete/", views.delete_comment, name="delete_comment"),
]
