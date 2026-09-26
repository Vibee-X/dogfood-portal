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

# WhiteNoise remains enabled in production. Test clients render templates but
# do not need to serve static assets, so omit its middleware to avoid scanning
# for an uncollected STATIC_ROOT during every test request.
MIDDLEWARE = [
    middleware
    for middleware in MIDDLEWARE  # noqa: F405
    if middleware != "whitenoise.middleware.WhiteNoiseMiddleware"
]
