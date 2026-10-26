"""Storage abstraction for Soultie profile media."""

from __future__ import annotations

import os
from abc import (
    ABC,
    abstractmethod,
)
from pathlib import (
    Path,
    PurePosixPath,
)
from tempfile import NamedTemporaryFile
from uuid import uuid4

from flask import current_app

from app.schemas.media import (
    MEDIA_KIND_PROFILE_PHOTO,
    MEDIA_KIND_PROFILE_VIDEO,
    MEDIA_STORAGE_LOCAL,
)


class MediaStorageError(RuntimeError):
    """Raised when media storage operations fail."""


class MediaStorageBackend(ABC):
    """Abstract storage backend used by Soultie media services."""

    @abstractmethod
    def save(
        self,
        storage_key,
        content,
    ):
        """Persist bytes under a storage key."""

    @abstractmethod
    def delete(
        self,
        storage_key,
    ):
        """Delete an object if it exists."""

    @abstractmethod
    def exists(
        self,
        storage_key,
    ):
        """Return whether an object exists."""

    @abstractmethod
    def read(
        self,
        storage_key,
    ):
        """Read and return object bytes."""

    @abstractmethod
    def get_internal_path(
        self,
        storage_key,
    ):
        """
        Return an internal path for server-side streaming.

        This path must never be returned directly to API clients.
        """


def normalize_storage_key(
    storage_key,
):
    """
    Validate and normalize a storage key.

    Keys are always POSIX-style relative paths and may never escape
    the configured storage root.
    """

    if not isinstance(
        storage_key,
        str,
    ):
        raise MediaStorageError(
            "Storage key must be a string."
        )

    normalized = (
        storage_key.strip()
        .replace("\\", "/")
    )

    if not normalized:
        raise MediaStorageError(
            "Storage key is required."
        )

    path = PurePosixPath(
        normalized
    )

    if path.is_absolute():
        raise MediaStorageError(
            "Absolute storage keys are not allowed."
        )

    if any(
        part in {
            "",
            ".",
            "..",
        }
        for part in path.parts
    ):
        raise MediaStorageError(
            "Invalid storage key."
        )

    if ":" in normalized:
        raise MediaStorageError(
            "Invalid storage key."
        )

    return path.as_posix()


def _normalize_user_component(
    user_id,
):
    value = str(
        user_id
    ).strip()

    if (
        not value
        or "/" in value
        or "\\" in value
        or ".." in value
        or ":" in value
    ):
        raise MediaStorageError(
            "Invalid user identifier for storage."
        )

    return value


def build_profile_media_storage_key(
    user_id,
    kind,
):
    """
    Generate a random storage key for normalized profile media.

    Original client filenames are intentionally ignored.
    """

    user_component = (
        _normalize_user_component(
            user_id
        )
    )

    if kind == MEDIA_KIND_PROFILE_PHOTO:
        extension = ".webp"

    elif kind == MEDIA_KIND_PROFILE_VIDEO:
        extension = ".mp4"

    else:
        raise MediaStorageError(
            "Unsupported profile media kind."
        )

    return normalize_storage_key(
        (
            f"profiles/{user_component}/"
            f"{uuid4().hex}{extension}"
        )
    )


def build_video_thumbnail_storage_key(
    user_id,
):
    """Generate a random WebP thumbnail key for a profile video."""

    user_component = (
        _normalize_user_component(
            user_id
        )
    )

    return normalize_storage_key(
        (
            f"profiles/{user_component}/"
            f"{uuid4().hex}.thumbnail.webp"
        )
    )


class LocalMediaStorage(
    MediaStorageBackend
):
    """Filesystem-backed media storage for development."""

    def __init__(
        self,
        root,
    ):
        if not root:
            raise MediaStorageError(
                "Local media storage root is required."
            )

        self.root = (
            Path(
                root
            )
            .expanduser()
            .resolve()
        )

        try:
            self.root.mkdir(
                parents=True,
                exist_ok=True,
            )

        except OSError as exc:
            raise MediaStorageError(
                "Unable to initialize local media storage."
            ) from exc

    def _resolve(
        self,
        storage_key,
    ):
        normalized = (
            normalize_storage_key(
                storage_key
            )
        )

        candidate = (
            self.root.joinpath(
                *PurePosixPath(
                    normalized
                ).parts
            )
            .resolve()
        )

        try:
            candidate.relative_to(
                self.root
            )

        except ValueError:
            raise MediaStorageError(
                "Storage key escapes the configured root."
            ) from None

        return candidate

    def save(
        self,
        storage_key,
        content,
    ):
        path = self._resolve(
            storage_key
        )

        if not isinstance(
            content,
            (
                bytes,
                bytearray,
                memoryview,
            ),
        ):
            raise MediaStorageError(
                "Media content must be bytes."
            )

        data = bytes(
            content
        )

        if not data:
            raise MediaStorageError(
                "Refusing to store empty media content."
            )

        try:
            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            with NamedTemporaryFile(
                mode="wb",
                delete=False,
                dir=str(
                    path.parent
                ),
                prefix=".xuoroni-media-",
                suffix=".tmp",
            ) as temporary:
                temporary.write(
                    data
                )

                temporary.flush()

                os.fsync(
                    temporary.fileno()
                )

                temporary_path = Path(
                    temporary.name
                )

            os.replace(
                temporary_path,
                path,
            )

        except OSError as exc:
            try:
                if (
                    "temporary_path"
                    in locals()
                    and temporary_path.exists()
                ):
                    temporary_path.unlink()
            except OSError:
                pass

            raise MediaStorageError(
                "Unable to save media object."
            ) from exc

        return normalize_storage_key(
            storage_key
        )

    def delete(
        self,
        storage_key,
    ):
        path = self._resolve(
            storage_key
        )

        try:
            if not path.exists():
                return False

            if not path.is_file():
                raise MediaStorageError(
                    "Storage object is not a regular file."
                )

            path.unlink()

            self._remove_empty_parents(
                path.parent
            )

            return True

        except MediaStorageError:
            raise

        except OSError as exc:
            raise MediaStorageError(
                "Unable to delete media object."
            ) from exc

    def _remove_empty_parents(
        self,
        directory,
    ):
        current = directory

        while current != self.root:
            try:
                current.rmdir()
            except OSError:
                break

            current = (
                current.parent
            )

    def exists(
        self,
        storage_key,
    ):
        path = self._resolve(
            storage_key
        )

        try:
            return (
                path.exists()
                and path.is_file()
            )

        except OSError as exc:
            raise MediaStorageError(
                "Unable to inspect media object."
            ) from exc

    def read(
        self,
        storage_key,
    ):
        path = self._resolve(
            storage_key
        )

        try:
            if (
                not path.exists()
                or not path.is_file()
            ):
                raise MediaStorageError(
                    "Media object does not exist."
                )

            return path.read_bytes()

        except MediaStorageError:
            raise

        except OSError as exc:
            raise MediaStorageError(
                "Unable to read media object."
            ) from exc

    def get_internal_path(
        self,
        storage_key,
    ):
        path = self._resolve(
            storage_key
        )

        try:
            if (
                not path.exists()
                or not path.is_file()
            ):
                raise MediaStorageError(
                    "Media object does not exist."
                )

        except MediaStorageError:
            raise

        except OSError as exc:
            raise MediaStorageError(
                "Unable to resolve media object."
            ) from exc

        return path


def _resolve_local_root_from_config():
    configured_root = current_app.config.get(
        "MEDIA_LOCAL_ROOT",
        "storage/profile_media",
    )

    root = Path(
        configured_root
    ).expanduser()

    if not root.is_absolute():
        backend_root = Path(
            current_app.root_path
        ).parent

        root = (
            backend_root
            / root
        )

    return root.resolve()


def get_media_storage():
    """
    Build the configured storage backend.

    S3 is intentionally not implemented yet, but callers depend only
    on MediaStorageBackend so it can be added without changing business
    logic.
    """

    backend = str(
        current_app.config.get(
            "MEDIA_STORAGE_BACKEND",
            MEDIA_STORAGE_LOCAL,
        )
    ).strip().lower()

    if backend == MEDIA_STORAGE_LOCAL:
        return LocalMediaStorage(
            _resolve_local_root_from_config()
        )

    raise MediaStorageError(
        (
            "Unsupported configured media "
            f"storage backend: {backend}"
        )
    )


__all__ = [
    "LocalMediaStorage",
    "MediaStorageBackend",
    "MediaStorageError",
    "build_profile_media_storage_key",
    "build_video_thumbnail_storage_key",
    "get_media_storage",
    "normalize_storage_key",
]
