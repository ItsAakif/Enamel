"""The de-identification limits have to stay disclosed (F-14).

De-identification removes the configured *top-level* tags and never touches the
pixel data. That leaves two residual risks that used to be documented nowhere:
burned-in annotations survive, and nested sequences are not scrubbed. A
regression guard keeps the disclosure in the code, the backend documentation and
the upload dialog.
"""

from __future__ import annotations

from pathlib import Path

from apps.imaging.dicom import deid

REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND_README = REPO_ROOT / "backend" / "README.md"
UPLOAD_DIALOG = (
    REPO_ROOT
    / "artifacts"
    / "enamel-landing"
    / "src"
    / "pages"
    / "DiagnosticsPage.tsx"
)


def test_the_deid_module_documents_both_limitations():
    doc = deid.__doc__ or ""

    assert "Burned-in annotations are not removed" in doc
    assert "Nested sequences are not scrubbed" in doc


def test_the_backend_readme_documents_both_limitations():
    text = BACKEND_README.read_text(encoding="utf-8")

    assert "Burned-in annotations are not removed" in text
    assert "Nested sequences are not scrubbed by the default tag list" in text
    # The source system has to de-identify before the upload.
    assert "de-identify the image" in text


def test_the_upload_dialog_discloses_both_limitations():
    text = UPLOAD_DIALOG.read_text(encoding="utf-8")

    assert "De-identification limits" in text
    assert "not" in text and "removed" in text  # "are not removed"
    assert "Nested sequences are not scrubbed" in text
    assert "source system de-identifies the image" in text
