from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    verbose_name = 'Lab Operations'

    def ready(self):
        import core.signals  # noqa: F401
