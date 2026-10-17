"""Static files storage for production: content-hashed, compressed, and tolerant of folder references."""
import os

from django.core.files.storage import FileSystemStorage
from whitenoise.storage import CompressedManifestStaticFilesStorage


class DirectoryTolerantManifestStorage(CompressedManifestStaticFilesStorage):
    """Same as WhiteNoise's manifest storage, except `{% static 'some/folder' %}` returns a plain URL.

    Jazzmin's admin layout renders `{% static 'vendor/bootswatch' %}` (a folder, used as a JavaScript base path). The manifest
    only lists files, so the strict storage raised ValueError and every admin page after login returned a 500 in production.
    A name without a file extension has nothing to fingerprint. Missing real files still raise, so typos stay visible.
    """

    def url(self, name, force=False):
        if name and not os.path.splitext(name)[1]:
            return FileSystemStorage.url(self, name)
        return super().url(name, force=force)
