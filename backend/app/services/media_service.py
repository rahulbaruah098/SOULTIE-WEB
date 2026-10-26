"""Business and processing services for Soultie profile media."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import warnings

from flask import current_app
from PIL import (
    Image,
    ImageOps,
    UnidentifiedImageError,
)
from pymongo.errors import DuplicateKeyError

from app.repositories.match_repository import (
    get_match_between,
)
from app.repositories.media_repository import (
    count_active_profile_media,
    create_profile_media,
    delete_media_record,
    find_active_duplicate_by_hash,
    get_media_by_id,
    get_owned_media,
    get_primary_media,
    get_next_media_position,
    list_active_profile_media,
    mark_media_deleted,
    normalize_media_positions,
    restore_media_active,
    set_primary_media,
    update_media_positions,
)
from app.repositories.profile_repository import (
    ensure_profile,
    get_profile_by_user_id,
    sync_profile_media_fields,
)
from app.repositories.safety_repository import (
    pair_is_blocked,
)
from app.schemas.media import (
    MEDIA_KIND_PROFILE_PHOTO,
    MEDIA_KIND_PROFILE_VIDEO,
    MEDIA_STORAGE_LOCAL,
    PHOTO_MIME_TYPE,
    VIDEO_MIME_TYPE,
    VIDEO_THUMBNAIL_MIME_TYPE,
    build_profile_media_reference,
    serialize_profile_media,
)
from app.services.media_storage_service import (
    MediaStorageError,
    build_profile_media_storage_key,
    build_video_thumbnail_storage_key,
    get_media_storage,
)
from app.services.profile_validation import (
    calculate_profile_completion,
)


SUPPORTED_PHOTO_INPUT_FORMATS = frozenset(
    {
        "JPEG",
        "PNG",
        "WEBP",
    }
)

SUPPORTED_VIDEO_INPUT_FORMATS = frozenset(
    {
        "mp4",
        "mov",
        "webm",
        "matroska",
    }
)


class MediaServiceError(RuntimeError):
    """Base error for Soultie media operations."""

    def __init__(
        self,
        code,
        message,
        status_code=400,
    ):
        super().__init__(
            message
        )

        self.code = code
        self.message = message
        self.status_code = status_code


class MediaValidationError(
    MediaServiceError
):
    """Raised when uploaded media fails validation."""


class MediaProcessingError(
    MediaServiceError
):
    """Raised when valid-looking media cannot be processed."""


@dataclass(frozen=True)
class ProcessedPhoto:
    """Normalized profile photo ready for storage."""

    content: bytes
    kind: str
    mime_type: str
    size_bytes: int
    width: int
    height: int
    sha256: str



@dataclass(frozen=True)
class ProcessedVideo:
    """Normalized short profile video ready for storage."""

    content: bytes
    kind: str
    mime_type: str
    size_bytes: int
    width: int
    height: int
    duration_ms: int
    sha256: str
    thumbnail_content: bytes
    thumbnail_mime_type: str
    thumbnail_width: int
    thumbnail_height: int

def _require_upload_bytes(
    content,
):
    if not isinstance(
        content,
        (
            bytes,
            bytearray,
            memoryview,
        ),
    ):
        raise MediaValidationError(
            "MEDIA_INVALID_IMAGE",
            "Uploaded photo must contain binary image data.",
        )

    data = bytes(
        content
    )

    if not data:
        raise MediaValidationError(
            "MEDIA_FILE_REQUIRED",
            "A profile photo is required.",
        )

    max_bytes = int(
        current_app.config.get(
            "MEDIA_MAX_UPLOAD_BYTES",
            10 * 1024 * 1024,
        )
    )

    if len(
        data
    ) > max_bytes:
        raise MediaValidationError(
            "MEDIA_FILE_TOO_LARGE",
            (
                "The uploaded media file exceeds "
                "the allowed size."
            ),
            status_code=413,
        )

    return data


def _safe_image_mode(
    image,
):
    """
    Convert Pillow images to a clean WebP-compatible mode.

    Alpha is preserved where it exists. Palette and uncommon
    camera/image modes are flattened into RGB/RGBA.
    """

    has_transparency = (
        image.mode in {
            "RGBA",
            "LA",
        }
        or (
            image.mode == "P"
            and "transparency"
            in image.info
        )
    )

    if has_transparency:
        return image.convert(
            "RGBA"
        )

    if image.mode != "RGB":
        return image.convert(
            "RGB"
        )

    return image.copy()


def _strip_image_metadata(
    image,
):
    """
    Create a new pixel-only image so EXIF, GPS, ICC and other
    source metadata are not carried into stored profile media.
    """

    clean = Image.new(
        image.mode,
        image.size,
    )

    # paste() copies pixel data only and avoids materializing
    # the entire image as a Python list.
    clean.paste(
        image
    )

    return clean


def process_profile_photo(
    content,
):
    """
    Validate and normalize a profile photo.

    Accepted source formats:
    JPEG, PNG and WebP.

    Stored output:
    clean WebP without source EXIF or other metadata.
    """

    data = _require_upload_bytes(
        content
    )

    max_width = int(
        current_app.config.get(
            "MEDIA_MAX_WIDTH",
            2000,
        )
    )

    max_height = int(
        current_app.config.get(
            "MEDIA_MAX_HEIGHT",
            2000,
        )
    )

    max_pixels = int(
        current_app.config.get(
            "MEDIA_MAX_IMAGE_PIXELS",
            40_000_000,
        )
    )

    webp_quality = int(
        current_app.config.get(
            "MEDIA_WEBP_QUALITY",
            85,
        )
    )

    output_format = str(
        current_app.config.get(
            "MEDIA_OUTPUT_FORMAT",
            "WEBP",
        )
    ).strip().upper()

    if output_format != "WEBP":
        raise MediaProcessingError(
            "MEDIA_PROCESSING_ERROR",
            (
                "Xuoroni profile photo output "
                "must be configured as WebP."
            ),
            status_code=500,
        )

    if (
        max_width <= 0
        or max_height <= 0
        or max_pixels <= 0
    ):
        raise MediaProcessingError(
            "MEDIA_PROCESSING_ERROR",
            "Invalid media image configuration.",
            status_code=500,
        )

    if not (
        1
        <= webp_quality
        <= 100
    ):
        raise MediaProcessingError(
            "MEDIA_PROCESSING_ERROR",
            "Invalid WebP quality configuration.",
            status_code=500,
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter(
                "error",
                Image.DecompressionBombWarning,
            )

            try:
                with Image.open(
                    BytesIO(
                        data
                    )
                ) as probe:
                    source_format = str(
                        probe.format
                        or ""
                    ).upper()

                    pixel_count = (
                        int(probe.width)
                        * int(probe.height)
                    )

                    if pixel_count > max_pixels:
                        raise MediaValidationError(
                            "MEDIA_INVALID_IMAGE",
                            (
                                "The profile photo is too large "
                                "to process safely."
                            ),
                        )

                    if (
                        source_format
                        not in
                        SUPPORTED_PHOTO_INPUT_FORMATS
                    ):
                        raise MediaValidationError(
                            "MEDIA_UNSUPPORTED_TYPE",
                            (
                                "Profile photos must be "
                                "JPEG, PNG, or WebP images."
                            ),
                        )

                    if bool(
                        getattr(
                            probe,
                            "is_animated",
                            False,
                        )
                    ) or int(
                        getattr(
                            probe,
                            "n_frames",
                            1,
                        )
                    ) > 1:
                        raise MediaValidationError(
                            "MEDIA_UNSUPPORTED_TYPE",
                            (
                                "Animated profile photos "
                                "are not supported."
                            ),
                        )

                    probe.verify()

            except MediaServiceError:
                raise

            except (
                Image.DecompressionBombError,
                Image.DecompressionBombWarning,
            ) as exc:
                raise MediaValidationError(
                    "MEDIA_INVALID_IMAGE",
                    (
                        "The profile photo is too large "
                        "to process safely."
                    ),
                ) from exc

            except (
                UnidentifiedImageError,
                OSError,
                SyntaxError,
                ValueError,
            ) as exc:
                raise MediaValidationError(
                    "MEDIA_INVALID_IMAGE",
                    (
                        "The uploaded file is not a valid "
                        "supported image."
                    ),
                ) from exc

            try:
                with Image.open(
                    BytesIO(
                        data
                    )
                ) as source:
                    if bool(
                        getattr(
                            source,
                            "is_animated",
                            False,
                        )
                    ) or int(
                        getattr(
                            source,
                            "n_frames",
                            1,
                        )
                    ) > 1:
                        raise MediaValidationError(
                            "MEDIA_UNSUPPORTED_TYPE",
                            (
                                "Animated profile photos "
                                "are not supported."
                            ),
                        )

                    # Apply phone/camera orientation before
                    # all source metadata is discarded.
                    oriented = (
                        ImageOps.exif_transpose(
                            source
                        )
                    )

                    normalized = (
                        _safe_image_mode(
                            oriented
                        )
                    )

                    clean = (
                        _strip_image_metadata(
                            normalized
                        )
                    )

            except MediaServiceError:
                raise

            except (
                Image.DecompressionBombError,
                Image.DecompressionBombWarning,
            ) as exc:
                raise MediaValidationError(
                    "MEDIA_INVALID_IMAGE",
                    (
                        "The profile photo is too large "
                        "to process safely."
                    ),
                ) from exc

            except (
                UnidentifiedImageError,
                OSError,
                SyntaxError,
                ValueError,
            ) as exc:
                raise MediaValidationError(
                    "MEDIA_INVALID_IMAGE",
                    (
                        "The uploaded image could not "
                        "be decoded safely."
                    ),
                ) from exc

        if (
            clean.width > max_width
            or clean.height > max_height
        ):
            clean.thumbnail(
                (
                    max_width,
                    max_height,
                ),
                Image.Resampling.LANCZOS,
            )

        output = BytesIO()

        try:
            clean.save(
                output,
                format="WEBP",
                quality=webp_quality,
                method=6,
            )

        except (
            OSError,
            ValueError,
        ) as exc:
            raise MediaProcessingError(
                "MEDIA_PROCESSING_ERROR",
                (
                    "The profile photo could not "
                    "be normalized."
                ),
                status_code=500,
            ) from exc

        normalized_content = (
            output.getvalue()
        )

        if not normalized_content:
            raise MediaProcessingError(
                "MEDIA_PROCESSING_ERROR",
                (
                    "The profile photo normalization "
                    "produced an empty result."
                ),
                status_code=500,
            )

        digest = sha256(
            normalized_content
        ).hexdigest()

        return ProcessedPhoto(
            content=normalized_content,
            kind=MEDIA_KIND_PROFILE_PHOTO,
            mime_type=PHOTO_MIME_TYPE,
            size_bytes=len(
                normalized_content
            ),
            width=clean.width,
            height=clean.height,
            sha256=digest,
        )




    except MediaServiceError:
        raise


def _require_video_upload_bytes(
    content,
):
    if not isinstance(
        content,
        (
            bytes,
            bytearray,
            memoryview,
        ),
    ):
        raise MediaValidationError(
            "MEDIA_INVALID_VIDEO",
            "Uploaded video must contain binary video data.",
        )

    data = bytes(
        content
    )

    if not data:
        raise MediaValidationError(
            "MEDIA_FILE_REQUIRED",
            "A profile video is required.",
        )

    max_bytes = int(
        current_app.config.get(
            "MEDIA_MAX_UPLOAD_BYTES",
            10 * 1024 * 1024,
        )
    )

    if len(
        data
    ) > max_bytes:
        raise MediaValidationError(
            "MEDIA_FILE_TOO_LARGE",
            (
                "The uploaded media file exceeds "
                "the allowed size."
            ),
            status_code=413,
        )

    return data


def _positive_float(
    value,
):
    try:
        parsed = float(
            value
        )
    except (
        TypeError,
        ValueError,
    ):
        return None

    if parsed <= 0:
        return None

    return parsed


def _video_rotation(
    stream,
):
    tags = stream.get(
        "tags"
    ) or {}

    try:
        if tags.get(
            "rotate"
        ) is not None:
            return int(
                float(
                    tags["rotate"]
                )
            )
    except (
        TypeError,
        ValueError,
    ):
        pass

    for item in (
        stream.get(
            "side_data_list"
        )
        or []
    ):
        try:
            if item.get(
                "rotation"
            ) is not None:
                return int(
                    float(
                        item["rotation"]
                    )
                )
        except (
            TypeError,
            ValueError,
        ):
            continue

    return 0


def _display_video_dimensions(
    stream,
):
    try:
        width = int(
            stream.get(
                "width"
            )
        )

        height = int(
            stream.get(
                "height"
            )
        )

    except (
        TypeError,
        ValueError,
    ) as exc:
        raise MediaValidationError(
            "MEDIA_INVALID_VIDEO",
            (
                "The uploaded video does not contain "
                "valid dimensions."
            ),
        ) from exc

    if (
        width <= 0
        or height <= 0
    ):
        raise MediaValidationError(
            "MEDIA_INVALID_VIDEO",
            (
                "The uploaded video does not contain "
                "valid dimensions."
            ),
        )

    rotation = (
        _video_rotation(
            stream
        )
        % 360
    )

    if rotation in {
        90,
        270,
    }:
        return (
            height,
            width,
        )

    return (
        width,
        height,
    )


def _target_video_dimensions(
    width,
    height,
    max_width,
    max_height,
):
    scale = min(
        1.0,
        max_width / width,
        max_height / height,
    )

    target_width = max(
        2,
        int(
            width * scale
        ),
    )

    target_height = max(
        2,
        int(
            height * scale
        ),
    )

    # H.264 yuv420p requires even dimensions.
    if target_width % 2:
        target_width -= 1

    if target_height % 2:
        target_height -= 1

    return (
        max(
            2,
            target_width,
        ),
        max(
            2,
            target_height,
        ),
    )


def _run_media_process(
    command,
    *,
    timeout,
    failure_message,
):
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=timeout,
            shell=False,
        )

    except FileNotFoundError as exc:
        raise MediaProcessingError(
            "MEDIA_PROCESSING_ERROR",
            (
                "Required video processing software "
                "is not available."
            ),
            status_code=500,
        ) from exc

    except subprocess.TimeoutExpired as exc:
        raise MediaProcessingError(
            "MEDIA_PROCESSING_ERROR",
            failure_message,
            status_code=500,
        ) from exc

    except OSError as exc:
        raise MediaProcessingError(
            "MEDIA_PROCESSING_ERROR",
            failure_message,
            status_code=500,
        ) from exc

    return completed


def _probe_video(
    path,
    *,
    invalid_upload,
):
    ffprobe_binary = str(
        current_app.config.get(
            "MEDIA_FFPROBE_BINARY",
            "ffprobe",
        )
    ).strip()

    completed = _run_media_process(
        [
            ffprobe_binary,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(
                path
            ),
        ],
        timeout=15,
        failure_message=(
            "The video could not be inspected."
        ),
    )

    if completed.returncode != 0:
        if invalid_upload:
            raise MediaValidationError(
                "MEDIA_INVALID_VIDEO",
                (
                    "The uploaded file is not a valid "
                    "supported video."
                ),
            )

        raise MediaProcessingError(
            "MEDIA_PROCESSING_ERROR",
            (
                "The normalized video could not "
                "be inspected."
            ),
            status_code=500,
        )

    try:
        metadata = json.loads(
            completed.stdout.decode(
                "utf-8",
                errors="strict",
            )
        )

    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        if invalid_upload:
            raise MediaValidationError(
                "MEDIA_INVALID_VIDEO",
                (
                    "The uploaded video metadata "
                    "could not be read."
                ),
            ) from exc

        raise MediaProcessingError(
            "MEDIA_PROCESSING_ERROR",
            (
                "The normalized video metadata "
                "could not be read."
            ),
            status_code=500,
        ) from exc

    format_info = (
        metadata.get(
            "format"
        )
        or {}
    )

    format_names = {
        value.strip().lower()
        for value in str(
            format_info.get(
                "format_name"
            )
            or ""
        ).split(
            ","
        )
        if value.strip()
    }

    if invalid_upload and not (
        format_names
        & SUPPORTED_VIDEO_INPUT_FORMATS
    ):
        raise MediaValidationError(
            "MEDIA_UNSUPPORTED_TYPE",
            (
                "Profile videos must be MP4, MOV, "
                "or WebM files."
            ),
        )

    video_stream = None

    for stream in (
        metadata.get(
            "streams"
        )
        or []
    ):
        if (
            stream.get(
                "codec_type"
            )
            == "video"
            and not bool(
                (
                    stream.get(
                        "disposition"
                    )
                    or {}
                ).get(
                    "attached_pic",
                    0,
                )
            )
        ):
            video_stream = stream
            break

    if video_stream is None:
        if invalid_upload:
            raise MediaValidationError(
                "MEDIA_INVALID_VIDEO",
                (
                    "The uploaded file does not "
                    "contain a video stream."
                ),
            )

        raise MediaProcessingError(
            "MEDIA_PROCESSING_ERROR",
            (
                "The normalized media does not "
                "contain a video stream."
            ),
            status_code=500,
        )

    duration_seconds = _positive_float(
        format_info.get(
            "duration"
        )
    )

    if duration_seconds is None:
        duration_seconds = (
            _positive_float(
                video_stream.get(
                    "duration"
                )
            )
        )

    if duration_seconds is None:
        if invalid_upload:
            raise MediaValidationError(
                "MEDIA_INVALID_VIDEO",
                (
                    "The uploaded video duration "
                    "could not be determined."
                ),
            )

        raise MediaProcessingError(
            "MEDIA_PROCESSING_ERROR",
            (
                "The normalized video duration "
                "could not be determined."
            ),
            status_code=500,
        )

    width, height = (
        _display_video_dimensions(
            video_stream
        )
    )

    return {
        "metadata": metadata,
        "format_names": format_names,
        "video_stream": video_stream,
        "duration_seconds": duration_seconds,
        "duration_ms": int(
            round(
                duration_seconds
                * 1000
            )
        ),
        "width": width,
        "height": height,
    }


def _encode_video_thumbnail(
    frame_path,
    *,
    quality,
):
    try:
        with Image.open(
            frame_path
        ) as source:
            normalized = (
                _safe_image_mode(
                    source
                )
            )

            clean = (
                _strip_image_metadata(
                    normalized
                )
            )

    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise MediaProcessingError(
            "MEDIA_PROCESSING_ERROR",
            (
                "The profile video thumbnail "
                "could not be decoded."
            ),
            status_code=500,
        ) from exc

    output = BytesIO()

    try:
        clean.save(
            output,
            format="WEBP",
            quality=quality,
            method=6,
        )

    except (
        OSError,
        ValueError,
    ) as exc:
        raise MediaProcessingError(
            "MEDIA_PROCESSING_ERROR",
            (
                "The profile video thumbnail "
                "could not be encoded."
            ),
            status_code=500,
        ) from exc

    content = output.getvalue()

    if not content:
        raise MediaProcessingError(
            "MEDIA_PROCESSING_ERROR",
            (
                "The profile video thumbnail "
                "is empty."
            ),
            status_code=500,
        )

    return (
        content,
        clean.width,
        clean.height,
    )


def process_profile_video(
    content,
):
    """
    Validate and normalize a short profile video.

    Accepted inputs:
    MP4, MOV and WebM.

    Stored output:
    MP4/H.264 with stripped source metadata plus a
    metadata-free WebP poster frame.
    """

    data = _require_video_upload_bytes(
        content
    )

    max_duration_ms = int(
        current_app.config.get(
            "MEDIA_VIDEO_MAX_DURATION_MS",
            4000,
        )
    )

    max_width = int(
        current_app.config.get(
            "MEDIA_VIDEO_MAX_WIDTH",
            1080,
        )
    )

    max_height = int(
        current_app.config.get(
            "MEDIA_VIDEO_MAX_HEIGHT",
            1920,
        )
    )

    codec = str(
        current_app.config.get(
            "MEDIA_VIDEO_CODEC",
            "libx264",
        )
    ).strip()

    audio_codec = str(
        current_app.config.get(
            "MEDIA_VIDEO_AUDIO_CODEC",
            "aac",
        )
    ).strip()

    preset = str(
        current_app.config.get(
            "MEDIA_VIDEO_PRESET",
            "medium",
        )
    ).strip()

    crf = int(
        current_app.config.get(
            "MEDIA_VIDEO_CRF",
            23,
        )
    )

    thumbnail_quality = int(
        current_app.config.get(
            "MEDIA_VIDEO_THUMBNAIL_QUALITY",
            85,
        )
    )

    ffmpeg_binary = str(
        current_app.config.get(
            "MEDIA_FFMPEG_BINARY",
            "ffmpeg",
        )
    ).strip()

    if (
        max_duration_ms <= 0
        or max_width <= 0
        or max_height <= 0
        or not codec
        or not audio_codec
        or not preset
        or not ffmpeg_binary
        or crf < 0
        or crf > 51
        or thumbnail_quality < 1
        or thumbnail_quality > 100
    ):
        raise MediaProcessingError(
            "MEDIA_PROCESSING_ERROR",
            "Invalid profile video configuration.",
            status_code=500,
        )

    with TemporaryDirectory(
        prefix="xuoroni-media-"
    ) as temporary_directory:
        directory = Path(
            temporary_directory
        )

        input_path = (
            directory
            / "input.media"
        )

        output_path = (
            directory
            / "normalized.mp4"
        )

        frame_path = (
            directory
            / "thumbnail.png"
        )

        input_path.write_bytes(
            data
        )

        source = _probe_video(
            input_path,
            invalid_upload=True,
        )

        if (
            source["duration_ms"]
            > max_duration_ms
        ):
            raise MediaValidationError(
                "MEDIA_VIDEO_TOO_LONG",
                (
                    "Profile videos must be "
                    "4 seconds or shorter."
                ),
            )

        target_width, target_height = (
            _target_video_dimensions(
                source["width"],
                source["height"],
                max_width,
                max_height,
            )
        )

        max_duration_seconds = (
            max_duration_ms
            / 1000.0
        )

        completed = _run_media_process(
            [
                ffmpeg_binary,
                "-v",
                "error",
                "-y",
                "-i",
                str(
                    input_path
                ),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-sn",
                "-dn",
                "-vf",
                (
                    f"scale="
                    f"{target_width}:"
                    f"{target_height}"
                ),
                "-c:v",
                codec,
                "-preset",
                preset,
                "-crf",
                str(
                    crf
                ),
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                audio_codec,
                "-b:a",
                "128k",
                "-t",
                (
                    f"{max_duration_seconds:.3f}"
                ),
                "-movflags",
                "+faststart",
                str(
                    output_path
                ),
            ],
            timeout=60,
            failure_message=(
                "The profile video could not "
                "be normalized."
            ),
        )

        if (
            completed.returncode != 0
            or not output_path.is_file()
            or output_path.stat().st_size <= 0
        ):
            raise MediaProcessingError(
                "MEDIA_PROCESSING_ERROR",
                (
                    "The profile video could not "
                    "be normalized."
                ),
                status_code=500,
            )

        normalized = _probe_video(
            output_path,
            invalid_upload=False,
        )

        # A tiny container timestamp rounding difference may
        # occur at the exact four-second boundary. Anything
        # materially beyond that is rejected.
        if (
            normalized["duration_ms"]
            > max_duration_ms + 50
        ):
            raise MediaProcessingError(
                "MEDIA_PROCESSING_ERROR",
                (
                    "The normalized profile video "
                    "exceeds the duration limit."
                ),
                status_code=500,
            )

        thumbnail_time = min(
            max(
                normalized[
                    "duration_seconds"
                ]
                / 2.0,
                0.001,
            ),
            2.0,
        )

        completed = _run_media_process(
            [
                ffmpeg_binary,
                "-v",
                "error",
                "-y",
                "-ss",
                f"{thumbnail_time:.3f}",
                "-i",
                str(
                    output_path
                ),
                "-frames:v",
                "1",
                "-an",
                "-sn",
                "-dn",
                "-map_metadata",
                "-1",
                str(
                    frame_path
                ),
            ],
            timeout=30,
            failure_message=(
                "The profile video thumbnail "
                "could not be generated."
            ),
        )

        if (
            completed.returncode != 0
            or not frame_path.is_file()
            or frame_path.stat().st_size <= 0
        ):
            raise MediaProcessingError(
                "MEDIA_PROCESSING_ERROR",
                (
                    "The profile video thumbnail "
                    "could not be generated."
                ),
                status_code=500,
            )

        (
            thumbnail_content,
            thumbnail_width,
            thumbnail_height,
        ) = _encode_video_thumbnail(
            frame_path,
            quality=thumbnail_quality,
        )

        normalized_content = (
            output_path.read_bytes()
        )

        digest = sha256(
            normalized_content
        ).hexdigest()

        duration_ms = min(
            normalized["duration_ms"],
            max_duration_ms,
        )

        return ProcessedVideo(
            content=normalized_content,
            kind=MEDIA_KIND_PROFILE_VIDEO,
            mime_type=VIDEO_MIME_TYPE,
            size_bytes=len(
                normalized_content
            ),
            width=normalized[
                "width"
            ],
            height=normalized[
                "height"
            ],
            duration_ms=duration_ms,
            sha256=digest,
            thumbnail_content=(
                thumbnail_content
            ),
            thumbnail_mime_type=(
                VIDEO_THUMBNAIL_MIME_TYPE
            ),
            thumbnail_width=(
                thumbnail_width
            ),
            thumbnail_height=(
                thumbnail_height
            ),
        )

def _ensure_media_profile_editable(
    profile,
):
    if (
        profile.get(
            "profile_status"
        )
        == "disabled"
    ):
        raise MediaServiceError(
            "MEDIA_PROFILE_DISABLED",
            (
                "This profile is currently disabled "
                "and its media cannot be changed."
            ),
            status_code=409,
        )


def _rollback_uploaded_media(
    *,
    storage,
    storage_keys,
    media_id=None,
    user_id=None,
):
    """
    Best-effort compensating rollback.

    Local/S3 object storage and MongoDB do not share one transaction,
    so every completed side effect is explicitly compensated.
    """

    if media_id is not None:
        try:
            delete_media_record(
                media_id,
                user_id=user_id,
            )
        except Exception:
            pass

    for storage_key in reversed(
        storage_keys
    ):
        try:
            storage.delete(
                storage_key
            )
        except Exception:
            pass

    if user_id is not None:
        if media_id is not None:
            try:
                normalize_media_positions(
                    user_id
                )
            except Exception:
                pass

        # A failure may occur after profile.media was already
        # synchronized. Rebuild it from the authoritative active
        # profile_media records so rollback cannot leave a stale
        # profile reference behind.
        try:
            _sync_profile_media_state(
                user_id
            )
        except Exception:
            # Rollback is best-effort. If the original failure was
            # caused by profile/database availability, do not hide
            # that original exception with a rollback exception.
            pass


def _sync_profile_media_state(
    user_id,
):
    """
    Rebuild the lightweight profile.media representation from the
    authoritative profile_media collection.
    """

    media_items = (
        list_active_profile_media(
            user_id
        )
    )

    references = [
        build_profile_media_reference(
            item
        )
        for item in media_items
    ]

    primary = get_primary_media(
        user_id
    )

    primary_media_id = (
        str(
            primary["_id"]
        )
        if primary is not None
        else None
    )

    profile = ensure_profile(
        user_id
    )

    candidate = dict(
        profile
    )

    candidate[
        "media"
    ] = references

    candidate[
        "primary_media_id"
    ] = primary_media_id

    completion = (
        calculate_profile_completion(
            candidate
        )
    )

    updated_profile = (
        sync_profile_media_fields(
            user_id,
            media=references,
            primary_media_id=(
                primary_media_id
            ),
            profile_completion_percent=(
                completion
            ),
        )
    )

    if updated_profile is None:
        raise MediaProcessingError(
            "MEDIA_PROFILE_SYNC_ERROR",
            (
                "Profile media could not be "
                "synchronized."
            ),
            status_code=500,
        )

    return {
        "profile": updated_profile,
        "media_items": media_items,
        "references": references,
        "primary_media_id": (
            primary_media_id
        ),
        "profile_completion_percent": (
            completion
        ),
    }


def upload_profile_media(
    user_id,
    *,
    kind,
    content,
):
    """
    Process, persist, and synchronize one profile photo or video.

    Uploading media does not itself mark the onboarding media step
    complete. Onboarding progress remains controlled exclusively by
    the onboarding API.
    """

    profile = ensure_profile(
        user_id
    )

    _ensure_media_profile_editable(
        profile
    )

    normalized_kind = str(
        kind
        or ""
    ).strip().lower()

    if normalized_kind not in {
        MEDIA_KIND_PROFILE_PHOTO,
        MEDIA_KIND_PROFILE_VIDEO,
    }:
        raise MediaValidationError(
            "MEDIA_INVALID_KIND",
            (
                "Media kind must be profile_photo "
                "or profile_video."
            ),
        )

    max_items = int(
        current_app.config.get(
            "MEDIA_MAX_PROFILE_ITEMS",
            6,
        )
    )

    if max_items <= 0:
        raise MediaProcessingError(
            "MEDIA_CONFIGURATION_ERROR",
            (
                "Profile media capacity is "
                "configured incorrectly."
            ),
            status_code=500,
        )

    if (
        count_active_profile_media(
            user_id
        )
        >= max_items
    ):
        raise MediaServiceError(
            "MEDIA_LIMIT_REACHED",
            (
                f"A profile can contain at most "
                f"{max_items} media items."
            ),
            status_code=409,
        )

    if (
        normalized_kind
        == MEDIA_KIND_PROFILE_PHOTO
    ):
        processed = (
            process_profile_photo(
                content
            )
        )
    else:
        processed = (
            process_profile_video(
                content
            )
        )

    duplicate = (
        find_active_duplicate_by_hash(
            user_id,
            processed.sha256,
        )
    )

    if duplicate is not None:
        raise MediaServiceError(
            "MEDIA_DUPLICATE",
            (
                "This media item is already "
                "on the profile."
            ),
            status_code=409,
        )

    try:
        storage = get_media_storage()
    except MediaStorageError as exc:
        raise MediaProcessingError(
            "MEDIA_STORAGE_ERROR",
            (
                "Profile media storage is "
                "currently unavailable."
            ),
            status_code=500,
        ) from exc

    storage_backend = str(
        current_app.config.get(
            "MEDIA_STORAGE_BACKEND",
            MEDIA_STORAGE_LOCAL,
        )
    ).strip().lower()

    storage_key = (
        build_profile_media_storage_key(
            user_id,
            normalized_kind,
        )
    )

    thumbnail_storage_key = None

    if (
        normalized_kind
        == MEDIA_KIND_PROFILE_VIDEO
    ):
        thumbnail_storage_key = (
            build_video_thumbnail_storage_key(
                user_id
            )
        )

    saved_storage_keys = []
    created_media = None

    try:
        storage.save(
            storage_key,
            processed.content,
        )

        saved_storage_keys.append(
            storage_key
        )

        if (
            normalized_kind
            == MEDIA_KIND_PROFILE_VIDEO
        ):
            storage.save(
                thumbnail_storage_key,
                processed.thumbnail_content,
            )

            saved_storage_keys.append(
                thumbnail_storage_key
            )

        position = (
            get_next_media_position(
                user_id
            )
        )

        create_kwargs = {
            "user_id": user_id,
            "kind": normalized_kind,
            "storage_backend": (
                storage_backend
            ),
            "storage_key": storage_key,
            "mime_type": (
                processed.mime_type
            ),
            "size_bytes": (
                processed.size_bytes
            ),
            "width": processed.width,
            "height": processed.height,
            "position": position,
            "sha256": processed.sha256,
            # Primary selection is done separately so that
            # concurrent first uploads do not both attempt
            # to insert an active primary record.
            "is_primary": False,
        }

        if (
            normalized_kind
            == MEDIA_KIND_PROFILE_VIDEO
        ):
            create_kwargs.update(
                {
                    "duration_ms": (
                        processed.duration_ms
                    ),
                    "thumbnail_storage_key": (
                        thumbnail_storage_key
                    ),
                    "thumbnail_mime_type": (
                        processed.thumbnail_mime_type
                    ),
                    "thumbnail_width": (
                        processed.thumbnail_width
                    ),
                    "thumbnail_height": (
                        processed.thumbnail_height
                    ),
                }
            )

        try:
            created_media = (
                create_profile_media(
                    **create_kwargs
                )
            )

        except DuplicateKeyError as exc:
            duplicate = (
                find_active_duplicate_by_hash(
                    user_id,
                    processed.sha256,
                )
            )

            if duplicate is not None:
                raise MediaServiceError(
                    "MEDIA_DUPLICATE",
                    (
                        "This media item is already "
                        "on the profile."
                    ),
                    status_code=409,
                ) from exc

            raise MediaProcessingError(
                "MEDIA_PERSISTENCE_ERROR",
                (
                    "Profile media metadata could "
                    "not be saved."
                ),
                status_code=500,
            ) from exc

        # Recheck after insertion. This protects the six-item
        # invariant against concurrent uploads that both passed
        # the pre-insert count check.
        if (
            count_active_profile_media(
                user_id
            )
            > max_items
        ):
            raise MediaServiceError(
                "MEDIA_LIMIT_REACHED",
                (
                    f"A profile can contain at most "
                    f"{max_items} media items."
                ),
                status_code=409,
            )

        normalize_media_positions(
            user_id
        )

        primary = get_primary_media(
            user_id
        )

        if primary is None:
            try:
                selected = set_primary_media(
                    user_id,
                    created_media["_id"],
                )
            except DuplicateKeyError:
                selected = get_primary_media(
                    user_id
                )

            if selected is None:
                raise MediaProcessingError(
                    "MEDIA_PRIMARY_ERROR",
                    (
                        "A primary profile media item "
                        "could not be selected."
                    ),
                    status_code=500,
                )

        state = _sync_profile_media_state(
            user_id
        )

        final_media = get_owned_media(
            user_id,
            created_media["_id"],
        )

        if final_media is None:
            raise MediaProcessingError(
                "MEDIA_PERSISTENCE_ERROR",
                (
                    "The saved profile media item "
                    "could not be loaded."
                ),
                status_code=500,
            )

        return {
            "media": serialize_profile_media(
                final_media
            ),
            "profile_media": state[
                "references"
            ],
            "primary_media_id": state[
                "primary_media_id"
            ],
            "profile_completion_percent": (
                state[
                    "profile_completion_percent"
                ]
            ),
        }

    except MediaServiceError:
        _rollback_uploaded_media(
            storage=storage,
            storage_keys=(
                saved_storage_keys
            ),
            media_id=(
                created_media["_id"]
                if created_media
                is not None
                else None
            ),
            user_id=user_id,
        )
        raise

    except MediaStorageError as exc:
        _rollback_uploaded_media(
            storage=storage,
            storage_keys=(
                saved_storage_keys
            ),
            media_id=(
                created_media["_id"]
                if created_media
                is not None
                else None
            ),
            user_id=user_id,
        )

        raise MediaProcessingError(
            "MEDIA_STORAGE_ERROR",
            (
                "Profile media could not be "
                "stored."
            ),
            status_code=500,
        ) from exc

    except Exception as exc:
        _rollback_uploaded_media(
            storage=storage,
            storage_keys=(
                saved_storage_keys
            ),
            media_id=(
                created_media["_id"]
                if created_media
                is not None
                else None
            ),
            user_id=user_id,
        )

        raise MediaProcessingError(
            "MEDIA_UPLOAD_FAILED",
            (
                "Profile media upload could "
                "not be completed."
            ),
            status_code=500,
        ) from exc

def _media_not_found():
    raise MediaServiceError(
        "MEDIA_NOT_FOUND",
        (
            "The requested profile media "
            "item was not found."
        ),
        status_code=404,
    )


def get_viewable_profile_media(
    viewer_user_id,
    media_id,
):
    """
    Return active profile media only when the authenticated viewer is
    allowed to see it.

    Owners may always read their own active media.

    Cross-user access requires:
    - an active, completed owner profile;
    - no block in either direction;
    - either public profile visibility or an active match.

    Unauthorized cross-user requests deliberately return the same
    MEDIA_NOT_FOUND response as nonexistent media to avoid disclosing
    private media identifiers.
    """

    media = get_media_by_id(
        media_id
    )

    if media is None:
        _media_not_found()

    owner_id = media.get(
        "user_id"
    )

    if owner_id is None:
        _media_not_found()

    # Owners retain access to their own active media even when their
    # profile is hidden, paused, draft, or administratively disabled.
    if str(
        owner_id
    ) == str(
        viewer_user_id
    ):
        return media

    owner_profile = (
        get_profile_by_user_id(
            owner_id
        )
    )

    if owner_profile is None:
        _media_not_found()

    if (
        owner_profile.get(
            "profile_status"
        )
        != "active"
        or owner_profile.get(
            "onboarding_status"
        )
        != "completed"
    ):
        _media_not_found()

    if pair_is_blocked(
        viewer_user_id,
        owner_id,
    ):
        _media_not_found()

    # A normal visible profile may expose its media to authenticated
    # viewers who are not blocked.
    if (
        owner_profile.get(
            "visibility"
        )
        == "visible"
    ):
        return media

    # Hidden/paused discovery visibility must not break an existing
    # active relationship. Active matches retain media access.
    match = get_match_between(
        viewer_user_id,
        owner_id,
    )

    if (
        match is not None
        and match.get(
            "status"
        )
        == "active"
    ):
        return media

    _media_not_found()

def _serialize_media_state(
    state,
):
    return {
        "media": [
            serialize_profile_media(
                item
            )
            for item in state[
                "media_items"
            ]
        ],
        "primary_media_id": state[
            "primary_media_id"
        ],
        "profile_completion_percent": state[
            "profile_completion_percent"
        ],
    }


def list_profile_media(
    user_id,
):
    """
    Return the authenticated user's active profile media without
    exposing storage internals.
    """

    profile = ensure_profile(
        user_id
    )

    media_items = (
        list_active_profile_media(
            user_id
        )
    )

    primary = get_primary_media(
        user_id
    )

    return {
        "media": [
            serialize_profile_media(
                item
            )
            for item in media_items
        ],
        "primary_media_id": (
            str(
                primary["_id"]
            )
            if primary is not None
            else None
        ),
        "profile_completion_percent": (
            profile.get(
                "profile_completion_percent"
            )
        ),
    }


def set_profile_primary_media(
    user_id,
    media_id,
):
    """
    Set one active owned media item as primary and synchronize the
    lightweight profile representation.
    """

    profile = ensure_profile(
        user_id
    )

    _ensure_media_profile_editable(
        profile
    )

    target = get_owned_media(
        user_id,
        media_id,
    )

    if target is None:
        raise MediaServiceError(
            "MEDIA_NOT_FOUND",
            (
                "The requested profile media "
                "item was not found."
            ),
            status_code=404,
        )

    previous_primary = (
        get_primary_media(
            user_id
        )
    )

    previous_primary_id = (
        previous_primary["_id"]
        if previous_primary
        is not None
        else None
    )

    # Idempotent: selecting the current primary is still a valid
    # successful operation.
    if (
        previous_primary_id
        == target["_id"]
    ):
        state = (
            _sync_profile_media_state(
                user_id
            )
        )

        return _serialize_media_state(
            state
        )

    try:
        selected = set_primary_media(
            user_id,
            target["_id"],
        )

        if selected is None:
            raise MediaServiceError(
                "MEDIA_NOT_FOUND",
                (
                    "The requested profile media "
                    "item is no longer available."
                ),
                status_code=404,
            )

        state = (
            _sync_profile_media_state(
                user_id
            )
        )

        return _serialize_media_state(
            state
        )

    except Exception as exc:
        # Restore the previous primary if the mutation completed but
        # profile synchronization failed.
        if previous_primary_id is not None:
            try:
                set_primary_media(
                    user_id,
                    previous_primary_id,
                )
            except Exception:
                pass

        try:
            _sync_profile_media_state(
                user_id
            )
        except Exception:
            pass

        if isinstance(
            exc,
            MediaServiceError,
        ):
            raise

        raise MediaProcessingError(
            "MEDIA_PRIMARY_UPDATE_FAILED",
            (
                "The primary profile media item "
                "could not be updated."
            ),
            status_code=500,
        ) from exc


def reorder_profile_media(
    user_id,
    ordered_media_ids,
):
    """
    Reorder the complete active profile-media set.

    Partial ordering is deliberately rejected at the service layer,
    even though the repository supports valid partial updates.
    """

    profile = ensure_profile(
        user_id
    )

    _ensure_media_profile_editable(
        profile
    )

    if not isinstance(
        ordered_media_ids,
        list,
    ):
        raise MediaValidationError(
            "MEDIA_REORDER_INVALID",
            (
                "media_ids must be a list "
                "containing every active media item."
            ),
        )

    active_media = (
        list_active_profile_media(
            user_id
        )
    )

    original_ids = [
        str(
            item["_id"]
        )
        for item in active_media
    ]

    normalized_ids = []

    for media_id in ordered_media_ids:
        value = str(
            media_id
            or ""
        ).strip()

        if not value:
            raise MediaValidationError(
                "MEDIA_REORDER_INVALID",
                (
                    "Every media ID must be "
                    "a non-empty value."
                ),
            )

        normalized_ids.append(
            value
        )

    if (
        len(
            normalized_ids
        )
        != len(
            original_ids
        )
        or len(
            set(
                normalized_ids
            )
        )
        != len(
            normalized_ids
        )
        or set(
            normalized_ids
        )
        != set(
            original_ids
        )
    ):
        raise MediaValidationError(
            "MEDIA_REORDER_INVALID",
            (
                "The reorder request must contain "
                "every active profile media ID "
                "exactly once."
            ),
        )

    if normalized_ids == original_ids:
        state = (
            _sync_profile_media_state(
                user_id
            )
        )

        return _serialize_media_state(
            state
        )

    try:
        updated = (
            update_media_positions(
                user_id,
                normalized_ids,
            )
        )

        if not updated:
            raise MediaValidationError(
                "MEDIA_REORDER_INVALID",
                (
                    "The requested profile media "
                    "order is invalid."
                ),
            )

        state = (
            _sync_profile_media_state(
                user_id
            )
        )

        return _serialize_media_state(
            state
        )

    except Exception as exc:
        try:
            update_media_positions(
                user_id,
                original_ids,
            )
        except Exception:
            pass

        try:
            _sync_profile_media_state(
                user_id
            )
        except Exception:
            pass

        if isinstance(
            exc,
            MediaServiceError,
        ):
            raise

        raise MediaProcessingError(
            "MEDIA_REORDER_FAILED",
            (
                "Profile media could not "
                "be reordered."
            ),
            status_code=500,
        ) from exc


def _cleanup_deleted_media_storage(
    media,
):
    """
    Remove physical media after the logical delete has committed.

    Storage cleanup is best-effort. Deleted metadata is already hidden
    from authenticated content routes, so an object cleanup failure
    must not resurrect user-visible media.
    """

    storage_keys = [
        media.get(
            "storage_key"
        )
    ]

    thumbnail_key = media.get(
        "thumbnail_storage_key"
    )

    if thumbnail_key:
        storage_keys.append(
            thumbnail_key
        )

    try:
        storage = get_media_storage()

    except MediaStorageError as exc:
        current_app.logger.warning(
            "Media storage cleanup unavailable for %s: %s",
            media.get(
                "_id"
            ),
            exc,
        )
        return

    for storage_key in storage_keys:
        if not storage_key:
            continue

        try:
            storage.delete(
                storage_key
            )

        except Exception as exc:
            current_app.logger.warning(
                "Unable to delete media storage object %s: %s",
                storage_key,
                exc,
            )


def delete_profile_media(
    user_id,
    media_id,
):
    """
    Soft-delete one owned profile media item, repair ordering/primary
    state, synchronize the profile, then remove physical objects.
    """

    profile = ensure_profile(
        user_id
    )

    _ensure_media_profile_editable(
        profile
    )

    target = get_owned_media(
        user_id,
        media_id,
    )

    if target is None:
        raise MediaServiceError(
            "MEDIA_NOT_FOUND",
            (
                "The requested profile media "
                "item was not found."
            ),
            status_code=404,
        )

    original_media = (
        list_active_profile_media(
            user_id
        )
    )

    original_order = [
        str(
            item["_id"]
        )
        for item in original_media
    ]

    original_primary = (
        get_primary_media(
            user_id
        )
    )

    original_primary_id = (
        original_primary["_id"]
        if original_primary
        is not None
        else None
    )

    deleted = None

    try:
        deleted = mark_media_deleted(
            user_id,
            target["_id"],
        )

        if deleted is None:
            raise MediaServiceError(
                "MEDIA_NOT_FOUND",
                (
                    "The requested profile media "
                    "item is no longer available."
                ),
                status_code=404,
            )

        remaining = (
            normalize_media_positions(
                user_id
            )
        )

        # Deleting the primary clears its flag in the repository.
        # Always repair the invariant if active media remain without
        # a primary.
        if (
            remaining
            and get_primary_media(
                user_id
            )
            is None
        ):
            selected = set_primary_media(
                user_id,
                remaining[0][
                    "_id"
                ],
            )

            if selected is None:
                raise MediaProcessingError(
                    "MEDIA_PRIMARY_ERROR",
                    (
                        "A replacement primary "
                        "media item could not "
                        "be selected."
                    ),
                    status_code=500,
                )

        state = (
            _sync_profile_media_state(
                user_id
            )
        )

    except Exception as exc:
        if deleted is not None:
            try:
                restore_media_active(
                    user_id,
                    target["_id"],
                    is_primary=False,
                )
            except Exception:
                pass

            try:
                update_media_positions(
                    user_id,
                    original_order,
                )
            except Exception:
                pass

            if original_primary_id is not None:
                try:
                    set_primary_media(
                        user_id,
                        original_primary_id,
                    )
                except Exception:
                    pass

            try:
                _sync_profile_media_state(
                    user_id
                )
            except Exception:
                pass

        if isinstance(
            exc,
            MediaServiceError,
        ):
            raise

        raise MediaProcessingError(
            "MEDIA_DELETE_FAILED",
            (
                "Profile media could not "
                "be deleted."
            ),
            status_code=500,
        ) from exc

    # Physical cleanup happens only after Mongo/profile state has
    # successfully committed. Any cleanup failure leaves an
    # inaccessible orphan rather than corrupting profile state.
    _cleanup_deleted_media_storage(
        target
    )

    result = _serialize_media_state(
        state
    )

    result[
        "deleted_media_id"
    ] = str(
        target["_id"]
    )

    return result

__all__ = [
    "MediaProcessingError",
    "MediaServiceError",
    "MediaValidationError",
    "ProcessedPhoto",
    "ProcessedVideo",
    "SUPPORTED_PHOTO_INPUT_FORMATS",
    "SUPPORTED_VIDEO_INPUT_FORMATS",
    "delete_profile_media",
    "get_viewable_profile_media",
    "list_profile_media",
    "process_profile_photo",
    "process_profile_video",
    "reorder_profile_media",
    "set_profile_primary_media",
    "upload_profile_media",
]
