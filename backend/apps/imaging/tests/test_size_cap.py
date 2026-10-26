"""The 50 MB upload cap and its 413 rejection path.

The application cap must match the 50 MB per-object limit of the free-tier
object storage target (Supabase Storage) and the ``client_max_body_size`` of
both nginx tiers (``deploy/nginx.conf`` and
``deploy/render/nginx.conf.template``), so an over-cap upload is refused by
the first guard with the existing 413 ``payload_too_large`` error instead of
being buffered end to end.
"""

from __future__ import annotations

from django.core.files.uploadedfile import SimpleUploadedFile

from apps.imaging.services import ingest

SIZE_LIMIT = 50 * 1024 * 1024


def test_default_cap_is_50_mb():
    """The effective default cap is 50 MB everywhere it is defined.

    ``ingest.max_bytes()`` prefers the ``IMAGING_MAX_BYTES`` settings value;
    the settings default (``config.settings.base``) and the service constant
    (``DEFAULT_MAX_BYTES``) must therefore stay in sync.
    """

    from django.conf import settings

    assert ingest.DEFAULT_MAX_BYTES == SIZE_LIMIT
    assert settings.IMAGING_MAX_BYTES == SIZE_LIMIT
    assert ingest.max_bytes() == SIZE_LIMIT


def test_over_cap_upload_is_rejected_with_413(owner, patient_factory):
    """A file larger than the default cap answers the existing 413 path.

    The size guard runs before the DICOM parse, so the bytes do not have to be
    a valid DICOM object; nothing may be written either way.
    """

    patient = patient_factory(owner.clinic)
    over_cap = SimpleUploadedFile(
        "oversized.bin",
        b"\x00" * (SIZE_LIMIT + 1),
        content_type="application/dicom",
    )
    response = owner.post_multipart(
        "/api/v1/imaging/studies/",
        {"patient": str(patient.id), "files": [over_cap]},
    )

    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "payload_too_large"
    assert error["details"]["max_bytes"] == SIZE_LIMIT
