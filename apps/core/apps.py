from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "apps.core"

    def ready(self) -> None:
        from . import checks, signals  # noqa: F401

        signals.connect()
