from django.contrib.auth.models import Group, Permission
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Note

MODERATOR_GROUP = "Moderators"
REVIEWER_GROUP = "Reviewers"

# Permissions granted to the Moderators group (codename list).
MODERATOR_PERMS = [
    "view_note", "change_note", "delete_note",
    "view_comment", "delete_comment",
    "view_rating", "delete_rating",
    "view_subject", "add_subject", "change_subject",
    "view_report", "change_report",
]

# Reviewers triage the report queue (resolve / dismiss) but have no delete_note,
# so they cannot remove notes.
REVIEWER_PERMS = ["view_note", "view_report", "change_report"]

GROUP_PERMS = {MODERATOR_GROUP: MODERATOR_PERMS, REVIEWER_GROUP: REVIEWER_PERMS}


def create_moderator_group(sender, **kwargs):
    """Create the Moderators and Reviewers groups (with permissions) after migrations."""
    for name, codenames in GROUP_PERMS.items():
        group, _ = Group.objects.get_or_create(name=name)
        perms = Permission.objects.filter(content_type__app_label="notes", codename__in=codenames)
        group.permissions.set(perms)


@receiver(post_delete, sender=Note)
def delete_note_file(sender, instance, **kwargs):
    """Remove the uploaded file and its thumbnail from disk when a note is deleted."""
    if instance.file:
        instance.file.delete(save=False)
    if instance.thumbnail:
        instance.thumbnail.delete(save=False)
