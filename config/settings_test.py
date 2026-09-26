"""Test-only settings that avoid requiring a collected static manifest."""
from .settings import *  # noqa: F403


# Production continues to use WhiteNoise's compressed, manifest-backed storage
# from config.settings. Tests render templates without running collectstatic.
STORAGES = {
    **STORAGES,  # noqa: F405
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
