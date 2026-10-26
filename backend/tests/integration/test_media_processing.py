"""Integration tests for Soultie profile media processing."""

from hashlib import sha256
from io import BytesIO
import json
import subprocess

from flask import Flask
from PIL import Image
import pytest

from app.schemas.media import (
    MEDIA_KIND_PROFILE_PHOTO,
    MEDIA_KIND_PROFILE_VIDEO,
    PHOTO_MIME_TYPE,
    VIDEO_MIME_TYPE,
    VIDEO_THUMBNAIL_MIME_TYPE,
)
from app.services.media_service import (
    MediaValidationError,
    ProcessedPhoto,
    ProcessedVideo,
    process_profile_photo,
    process_profile_video,
)


@pytest.fixture
def app():
    application = Flask(
        __name__
    )

    application.config.update(
        MEDIA_MAX_UPLOAD_BYTES=(
            10 * 1024 * 1024
        ),
        MEDIA_MAX_WIDTH=2000,
        MEDIA_MAX_HEIGHT=2000,
        MEDIA_MAX_IMAGE_PIXELS=40_000_000,
        MEDIA_OUTPUT_FORMAT="WEBP",
        MEDIA_WEBP_QUALITY=85,
        MEDIA_VIDEO_MAX_DURATION_MS=4000,
        MEDIA_VIDEO_MAX_WIDTH=1080,
        MEDIA_VIDEO_MAX_HEIGHT=1920,
        MEDIA_VIDEO_CODEC="libx264",
        MEDIA_VIDEO_AUDIO_CODEC="aac",
        MEDIA_VIDEO_CRF=23,
        MEDIA_VIDEO_PRESET="ultrafast",
        MEDIA_VIDEO_THUMBNAIL_QUALITY=85,
        MEDIA_FFMPEG_BINARY="ffmpeg",
        MEDIA_FFPROBE_BINARY="ffprobe",
    )

    return application


def encode_image(
    image,
    *,
    format_name,
    exif=None,
):
    output = BytesIO()

    kwargs = {}

    if exif is not None:
        kwargs["exif"] = exif

    image.save(
        output,
        format=format_name,
        **kwargs,
    )

    return output.getvalue()


@pytest.mark.parametrize(
    "format_name",
    [
        "JPEG",
        "PNG",
        "WEBP",
    ],
)
def test_supported_photo_formats_are_normalized(
    app,
    format_name,
):
    image = Image.new(
        "RGB",
        (
            640,
            480,
        ),
        (
            120,
            80,
            40,
        ),
    )

    source = encode_image(
        image,
        format_name=format_name,
    )

    with app.app_context():
        result = process_profile_photo(
            source
        )

    assert isinstance(
        result,
        ProcessedPhoto,
    )

    assert (
        result.kind
        == MEDIA_KIND_PROFILE_PHOTO
    )

    assert (
        result.mime_type
        == PHOTO_MIME_TYPE
    )

    assert result.width == 640
    assert result.height == 480

    assert (
        result.size_bytes
        == len(result.content)
    )

    assert result.sha256 == sha256(
        result.content
    ).hexdigest()

    with Image.open(
        BytesIO(result.content)
    ) as stored:
        assert stored.format == "WEBP"
        assert stored.size == (
            640,
            480,
        )


def test_large_photo_is_resized_preserving_ratio(
    app,
):
    app.config[
        "MEDIA_MAX_WIDTH"
    ] = 1000

    app.config[
        "MEDIA_MAX_HEIGHT"
    ] = 1000

    image = Image.new(
        "RGB",
        (
            4000,
            2000,
        ),
        (
            10,
            20,
            30,
        ),
    )

    source = encode_image(
        image,
        format_name="JPEG",
    )

    with app.app_context():
        result = process_profile_photo(
            source
        )

    assert result.width == 1000
    assert result.height == 500


def test_exif_orientation_is_applied(
    app,
):
    image = Image.new(
        "RGB",
        (
            80,
            40,
        ),
        (
            200,
            100,
            50,
        ),
    )

    exif = Image.Exif()

    # Orientation 6 means rotate 90 degrees clockwise
    # for correct visual presentation.
    exif[274] = 6

    source = encode_image(
        image,
        format_name="JPEG",
        exif=exif,
    )

    with app.app_context():
        result = process_profile_photo(
            source
        )

    assert result.width == 40
    assert result.height == 80


def test_source_exif_metadata_is_removed(
    app,
):
    image = Image.new(
        "RGB",
        (
            200,
            200,
        ),
        (
            90,
            120,
            150,
        ),
    )

    exif = Image.Exif()

    # Camera manufacturer.
    exif[271] = "Xuoroni Test Camera"

    # Orientation.
    exif[274] = 1

    # DateTimeOriginal.
    exif[36867] = (
        "2026:10:09 10:30:00"
    )

    source = encode_image(
        image,
        format_name="JPEG",
        exif=exif,
    )

    with app.app_context():
        result = process_profile_photo(
            source
        )

    with Image.open(
        BytesIO(result.content)
    ) as stored:
        stored_exif = (
            stored.getexif()
        )

        assert len(
            stored_exif
        ) == 0

        assert (
            "exif"
            not in stored.info
        )


def test_png_transparency_is_preserved(
    app,
):
    image = Image.new(
        "RGBA",
        (
            100,
            100,
        ),
        (
            255,
            0,
            0,
            80,
        ),
    )

    source = encode_image(
        image,
        format_name="PNG",
    )

    with app.app_context():
        result = process_profile_photo(
            source
        )

    with Image.open(
        BytesIO(result.content)
    ) as stored:
        assert stored.mode == "RGBA"

        alpha = stored.getchannel(
            "A"
        )

        minimum, maximum = (
            alpha.getextrema()
        )

        assert minimum < 255
        assert maximum < 255


def test_fake_image_is_rejected(
    app,
):
    with app.app_context():
        with pytest.raises(
            MediaValidationError
        ) as exc_info:
            process_profile_photo(
                (
                    b"this-is-not-"
                    b"an-image"
                )
            )

    assert (
        exc_info.value.code
        == "MEDIA_INVALID_IMAGE"
    )


def test_empty_upload_is_rejected(
    app,
):
    with app.app_context():
        with pytest.raises(
            MediaValidationError
        ) as exc_info:
            process_profile_photo(
                b""
            )

    assert (
        exc_info.value.code
        == "MEDIA_FILE_REQUIRED"
    )


def test_upload_byte_limit_is_enforced(
    app,
):
    app.config[
        "MEDIA_MAX_UPLOAD_BYTES"
    ] = 10

    with app.app_context():
        with pytest.raises(
            MediaValidationError
        ) as exc_info:
            process_profile_photo(
                b"x" * 11
            )

    assert (
        exc_info.value.code
        == "MEDIA_FILE_TOO_LARGE"
    )

    assert (
        exc_info.value.status_code
        == 413
    )


def test_image_pixel_limit_is_enforced(
    app,
):
    app.config[
        "MEDIA_MAX_IMAGE_PIXELS"
    ] = 10_000

    image = Image.new(
        "RGB",
        (
            101,
            100,
        ),
        (
            1,
            2,
            3,
        ),
    )

    source = encode_image(
        image,
        format_name="PNG",
    )

    with app.app_context():
        with pytest.raises(
            MediaValidationError
        ) as exc_info:
            process_profile_photo(
                source
            )

    assert (
        exc_info.value.code
        == "MEDIA_INVALID_IMAGE"
    )


def test_valid_bmp_is_rejected_as_unsupported(
    app,
):
    image = Image.new(
        "RGB",
        (
            100,
            100,
        ),
        (
            20,
            40,
            60,
        ),
    )

    source = encode_image(
        image,
        format_name="BMP",
    )

    with app.app_context():
        with pytest.raises(
            MediaValidationError
        ) as exc_info:
            process_profile_photo(
                source
            )

    assert (
        exc_info.value.code
        == "MEDIA_UNSUPPORTED_TYPE"
    )


def test_animated_webp_is_rejected(
    app,
):
    first = Image.new(
        "RGB",
        (
            100,
            100,
        ),
        (
            255,
            0,
            0,
        ),
    )

    second = Image.new(
        "RGB",
        (
            100,
            100,
        ),
        (
            0,
            255,
            0,
        ),
    )

    output = BytesIO()

    first.save(
        output,
        format="WEBP",
        save_all=True,
        append_images=[
            second
        ],
        duration=100,
        loop=0,
    )

    with app.app_context():
        with pytest.raises(
            MediaValidationError
        ) as exc_info:
            process_profile_photo(
                output.getvalue()
            )

    assert (
        exc_info.value.code
        == "MEDIA_UNSUPPORTED_TYPE"
    )


def test_normalized_output_hash_matches_bytes(
    app,
):
    image = Image.new(
        "RGB",
        (
            320,
            240,
        ),
        (
            50,
            100,
            150,
        ),
    )

    source = encode_image(
        image,
        format_name="PNG",
    )

    with app.app_context():
        result = process_profile_photo(
            source
        )

    expected = sha256(
        result.content
    ).hexdigest()

    assert result.sha256 == expected
    assert len(
        result.sha256
    ) == 64


def test_original_dimensions_are_not_upscaled(
    app,
):
    image = Image.new(
        "RGB",
        (
            150,
            100,
        ),
        (
            100,
            100,
            100,
        ),
    )

    source = encode_image(
        image,
        format_name="JPEG",
    )

    with app.app_context():
        result = process_profile_photo(
            source
        )

    assert result.width == 150
    assert result.height == 100

def run_ffmpeg(
    arguments,
):
    completed = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            *arguments,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, (
        completed.stderr
    )


def generate_test_video(
    tmp_path,
    *,
    container="mp4",
    duration=3.0,
    width=320,
    height=240,
    with_audio=False,
    add_metadata=False,
):
    extension = container

    path = (
        tmp_path
        / f"input.{extension}"
    )

    arguments = [
        "-f",
        "lavfi",
        "-i",
        (
            "color=c=blue:"
            f"s={width}x{height}:"
            f"r=24:d={duration}"
        ),
    ]

    if with_audio:
        arguments.extend(
            [
                "-f",
                "lavfi",
                "-i",
                (
                    "sine=frequency=440:"
                    "sample_rate=44100:"
                    f"duration={duration}"
                ),
            ]
        )

    if container in {
        "mp4",
        "mov",
    }:
        arguments.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
            ]
        )

        if with_audio:
            arguments.extend(
                [
                    "-c:a",
                    "aac",
                    "-b:a",
                    "96k",
                ]
            )

    elif container == "webm":
        arguments.extend(
            [
                "-c:v",
                "libvpx",
                "-b:v",
                "300k",
            ]
        )

    elif container == "avi":
        arguments.extend(
            [
                "-c:v",
                "mpeg4",
            ]
        )

    else:
        raise AssertionError(
            (
                "Unsupported test "
                f"container: {container}"
            )
        )

    if add_metadata:
        arguments.extend(
            [
                "-metadata",
                (
                    "title="
                    "PRIVATE-XUORONI-TITLE"
                ),
                "-metadata",
                (
                    "comment="
                    "PRIVATE-XUORONI-COMMENT"
                ),
            ]
        )

    arguments.extend(
        [
            "-t",
            str(
                duration
            ),
        ]
    )

    if with_audio:
        arguments.append(
            "-shortest"
        )

    arguments.append(
        str(
            path
        )
    )

    run_ffmpeg(
        arguments
    )

    assert path.is_file()
    assert path.stat().st_size > 0

    return path.read_bytes()


def probe_video_bytes(
    tmp_path,
    content,
    *,
    filename="probe.mp4",
):
    path = (
        tmp_path
        / filename
    )

    path.write_bytes(
        content
    )

    completed = subprocess.run(
        [
            "ffprobe",
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
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, (
        completed.stderr
    )

    return json.loads(
        completed.stdout
    )


@pytest.mark.parametrize(
    "container",
    [
        "mp4",
        "mov",
        "webm",
    ],
)
def test_supported_video_formats_are_normalized(
    app,
    tmp_path,
    container,
):
    source = generate_test_video(
        tmp_path,
        container=container,
        duration=3.0,
    )

    with app.app_context():
        result = process_profile_video(
            source
        )

    assert isinstance(
        result,
        ProcessedVideo,
    )

    assert (
        result.kind
        == MEDIA_KIND_PROFILE_VIDEO
    )

    assert (
        result.mime_type
        == VIDEO_MIME_TYPE
    )

    assert (
        result.thumbnail_mime_type
        == VIDEO_THUMBNAIL_MIME_TYPE
    )

    assert 0 < result.duration_ms <= 4000

    assert result.size_bytes == len(
        result.content
    )

    assert result.sha256 == sha256(
        result.content
    ).hexdigest()


def test_fake_video_is_rejected(
    app,
):
    with app.app_context():
        with pytest.raises(
            MediaValidationError
        ) as exc_info:
            process_profile_video(
                b"not-a-real-video"
            )

    assert (
        exc_info.value.code
        == "MEDIA_INVALID_VIDEO"
    )


def test_video_over_four_seconds_is_rejected(
    app,
    tmp_path,
):
    source = generate_test_video(
        tmp_path,
        container="mp4",
        duration=4.2,
    )

    with app.app_context():
        with pytest.raises(
            MediaValidationError
        ) as exc_info:
            process_profile_video(
                source
            )

    assert (
        exc_info.value.code
        == "MEDIA_VIDEO_TOO_LONG"
    )


def test_exact_four_second_video_is_accepted(
    app,
    tmp_path,
):
    source = generate_test_video(
        tmp_path,
        container="mp4",
        duration=4.0,
    )

    with app.app_context():
        result = process_profile_video(
            source
        )

    assert 0 < result.duration_ms <= 4000


def test_normalized_video_is_h264_mp4_and_hash_matches(
    app,
    tmp_path,
):
    source = generate_test_video(
        tmp_path,
        container="webm",
        duration=3.0,
    )

    with app.app_context():
        result = process_profile_video(
            source
        )

    metadata = probe_video_bytes(
        tmp_path,
        result.content,
    )

    video_streams = [
        stream
        for stream in metadata[
            "streams"
        ]
        if stream.get(
            "codec_type"
        ) == "video"
    ]

    assert len(
        video_streams
    ) == 1

    assert (
        video_streams[0][
            "codec_name"
        ]
        == "h264"
    )

    format_names = set(
        str(
            metadata[
                "format"
            ].get(
                "format_name",
                "",
            )
        ).split(
            ","
        )
    )

    assert "mp4" in format_names

    assert result.sha256 == sha256(
        result.content
    ).hexdigest()


def test_video_thumbnail_is_webp_and_metadata_free(
    app,
    tmp_path,
):
    source = generate_test_video(
        tmp_path,
        container="mp4",
        duration=3.0,
    )

    with app.app_context():
        result = process_profile_video(
            source
        )

    assert result.thumbnail_content

    with Image.open(
        BytesIO(
            result.thumbnail_content
        )
    ) as thumbnail:
        assert thumbnail.format == "WEBP"

        assert thumbnail.size == (
            result.thumbnail_width,
            result.thumbnail_height,
        )

        assert len(
            thumbnail.getexif()
        ) == 0

        assert (
            "exif"
            not in thumbnail.info
        )


@pytest.mark.parametrize(
    (
        "source_width",
        "source_height",
        "expected_width",
        "expected_height",
    ),
    [
        (
            1920,
            1080,
            1080,
            606,
        ),
        (
            320,
            240,
            320,
            240,
        ),
    ],
)
def test_video_resize_and_no_upscale(
    app,
    tmp_path,
    source_width,
    source_height,
    expected_width,
    expected_height,
):
    source = generate_test_video(
        tmp_path,
        container="mp4",
        duration=3.0,
        width=source_width,
        height=source_height,
    )

    with app.app_context():
        result = process_profile_video(
            source
        )

    assert (
        result.width
        == expected_width
    )

    assert (
        result.height
        == expected_height
    )


def test_source_video_metadata_is_removed(
    app,
    tmp_path,
):
    source = generate_test_video(
        tmp_path,
        container="mp4",
        duration=3.0,
        add_metadata=True,
    )

    with app.app_context():
        result = process_profile_video(
            source
        )

    metadata = probe_video_bytes(
        tmp_path,
        result.content,
    )

    format_tags = {
        str(key).lower(): str(value)
        for key, value in (
            metadata[
                "format"
            ].get(
                "tags"
            )
            or {}
        ).items()
    }

    assert (
        format_tags.get(
            "title"
        )
        != "PRIVATE-XUORONI-TITLE"
    )

    assert (
        format_tags.get(
            "comment"
        )
        != "PRIVATE-XUORONI-COMMENT"
    )


def test_audio_stream_is_preserved(
    app,
    tmp_path,
):
    source = generate_test_video(
        tmp_path,
        container="mp4",
        duration=3.0,
        with_audio=True,
    )

    with app.app_context():
        result = process_profile_video(
            source
        )

    metadata = probe_video_bytes(
        tmp_path,
        result.content,
    )

    audio_streams = [
        stream
        for stream in metadata[
            "streams"
        ]
        if stream.get(
            "codec_type"
        ) == "audio"
    ]

    assert len(
        audio_streams
    ) == 1

    assert (
        audio_streams[0][
            "codec_name"
        ]
        == "aac"
    )


def test_valid_avi_is_rejected_as_unsupported(
    app,
    tmp_path,
):
    source = generate_test_video(
        tmp_path,
        container="avi",
        duration=3.0,
    )

    with app.app_context():
        with pytest.raises(
            MediaValidationError
        ) as exc_info:
            process_profile_video(
                source
            )

    assert (
        exc_info.value.code
        == "MEDIA_UNSUPPORTED_TYPE"
    )


def test_video_upload_byte_limit_is_enforced(
    app,
):
    app.config[
        "MEDIA_MAX_UPLOAD_BYTES"
    ] = 10

    with app.app_context():
        with pytest.raises(
            MediaValidationError
        ) as exc_info:
            process_profile_video(
                b"x" * 11
            )

    assert (
        exc_info.value.code
        == "MEDIA_FILE_TOO_LARGE"
    )

    assert (
        exc_info.value.status_code
        == 413
    )
