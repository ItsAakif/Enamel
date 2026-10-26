"""JPEG preview rendering for thumbnails (T127).

A preview is a lossy convenience image, never the clinical source of truth. If
decoding or encoding fails the caller records ``preview_status=failed`` and the
API returns no image: a blank preview is never fabricated.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image

from .pixels import PixelDecodeError, pixel_array, to_uint8

DEFAULT_MAX_SIZE = 512


class PreviewError(RuntimeError):
    """Raised when a preview cannot be produced."""


def render_jpeg(dataset: Any, *, max_size: int = DEFAULT_MAX_SIZE, quality: int = 85) -> bytes:
    """Render ``dataset`` to JPEG bytes or raise :class:`PreviewError`."""

    try:
        array = pixel_array(dataset)
    except PixelDecodeError as exc:
        raise PreviewError(str(exc)) from exc

    if array.size == 0:
        raise PreviewError("The DICOM pixel data is empty.")

    try:
        frame = array
        if frame.ndim == 3:
            # Use the first frame of a multi-frame object for the thumbnail.
            frame = frame[0]
        image = Image.fromarray(to_uint8(frame))
        if image.mode not in ("L", "RGB"):
            image = image.convert("L")
        image.thumbnail((max_size, max_size))
        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=quality)
    except Exception as exc:  # noqa: BLE001 - any encoder failure is a preview failure
        raise PreviewError("The preview could not be rendered.") from exc

    return buffer.getvalue()


__all__ = ["DEFAULT_MAX_SIZE", "PreviewError", "render_jpeg"]
