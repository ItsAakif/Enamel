"""Pixel decoding and windowing (T127).

Decoding goes through pydicom's ``pixel_array`` so every transfer syntax pydicom
supports (including the uncompressed fixtures) is handled uniformly. A dataset
that cannot be decoded raises :class:`PixelDecodeError` rather than returning an
empty image, so a broken instance is reported, never silently blank.
"""

from __future__ import annotations

from typing import Any

import numpy as np


class PixelDecodeError(ValueError):
    """Raised when a DICOM pixel payload cannot be decoded."""


def pixel_array(dataset: Any) -> np.ndarray:
    """Return the decoded pixel array for ``dataset`` as ``float64``."""

    try:
        array = dataset.pixel_array
    except Exception as exc:  # noqa: BLE001 - any codec failure is a decode failure
        raise PixelDecodeError("The DICOM pixel data could not be decoded.") from exc
    return np.asarray(array, dtype=np.float64)


def window(
    array: np.ndarray,
    *,
    center: float | None = None,
    width: float | None = None,
) -> np.ndarray:
    """Apply a linear window and normalise to the 0..255 range.

    Without an explicit window the full observed range is stretched, which is
    the deterministic default used for previews.
    """

    data = np.asarray(array, dtype=np.float64)
    if data.size == 0:
        return np.zeros((0, 0), dtype=np.float64)

    if center is not None and width is not None and width > 0:
        low = center - width / 2.0
        high = center + width / 2.0
    else:
        low = float(np.min(data))
        high = float(np.max(data))

    if high <= low:
        return np.zeros_like(data, dtype=np.float64)

    clipped = np.clip(data, low, high)
    return (clipped - low) / (high - low) * 255.0


def to_uint8(array: np.ndarray) -> np.ndarray:
    """Return an 8-bit unsigned array suitable for an image encoder."""

    return np.clip(np.round(window(array)), 0, 255).astype(np.uint8)


__all__ = ["PixelDecodeError", "pixel_array", "to_uint8", "window"]
