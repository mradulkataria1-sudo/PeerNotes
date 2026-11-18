from django.apps import AppConfig
from django.db.models.signals import post_migrate


class NotesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "notes"

    def ready(self):
        from . import signals  # noqa: F401  (registers post_delete receivers)

        post_migrate.connect(signals.create_moderator_group, sender=self)
