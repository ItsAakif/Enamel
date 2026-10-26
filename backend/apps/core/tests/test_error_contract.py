"""The published OpenAPI document must describe the error contract (F-04/F-10).

The committed document is the contract the frontend client is generated from.
Before this module the document described 2xx responses only, so a generated
client could not type a single error response and the ``ErrorEnvelope`` /
``ErrorDetail`` / ``CodeEnum`` components were declared but referenced by no
operation. These tests fail if that drift comes back:

* every operation documents the standard error statuses, and every one of them
  references ``ErrorEnvelope``;
* the ``CodeEnum`` component is derived from
  :data:`apps.core.errors.ERROR_CODES` and contains every code the runtime
  actually raises;
* the pagination components do not stutter (``Paginated*ListList``).
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from apps.core.errors import ERROR_CODES

BACKEND_DIR = Path(__file__).resolve().parents[3]
SCHEMA_PATH = BACKEND_DIR / "openapi" / "schema.json"

#: The error statuses every operation has to document.
REQUIRED_ERROR_STATUSES = ("400", "401", "403", "404", "409", "422", "429", "500")

ERROR_ENVELOPE_REF = "#/components/schemas/ErrorEnvelope"

#: ``ApiError("some_code", ...)`` is the only way the runtime puts a code into
#: the error envelope, so the literal is the authoritative list of codes.
API_ERROR_CALL = re.compile(r'ApiError\(\s*"([a-z0-9_]+)"')


@pytest.fixture(scope="module")
def committed_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def operations(committed_schema: dict) -> list[tuple[str, str, dict]]:
    """Every ``(path, method, operation)`` triple in the document."""

    http_methods = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
    found: list[tuple[str, str, dict]] = []
    for path, path_item in committed_schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() in http_methods and isinstance(operation, dict):
                found.append((path, method.lower(), operation))
    return found


def _runtime_error_codes() -> set[str]:
    """Every code the runtime can put into the error envelope."""

    codes: set[str] = set()
    for path in sorted((BACKEND_DIR / "apps").rglob("*.py")):
        if "migrations" in path.parts or "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        codes.update(API_ERROR_CALL.findall(text))

    # The AI tool registry rejects a call with its own codes, which reach the
    # envelope through ``apps.ai.views.TOOL_HTTP_STATUS``.
    from apps.ai.views import TOOL_HTTP_STATUS

    codes.update(TOOL_HTTP_STATUS)
    return codes


class TestErrorResponsesArePublished:
    def test_every_operation_documents_the_standard_error_statuses(
        self, operations: list[tuple[str, str, dict]]
    ) -> None:
        assert operations, "the document declares no operation at all"

        missing: list[str] = []
        for path, method, operation in operations:
            responses = operation.get("responses", {})
            for status in REQUIRED_ERROR_STATUSES:
                if status not in responses:
                    missing.append(f"{method.upper()} {path} -> {status}")

        assert not missing, "operations without a documented error response:\n" + "\n".join(
            missing
        )

    def test_every_documented_error_response_references_the_envelope(
        self, operations: list[tuple[str, str, dict]]
    ) -> None:
        wrong: list[str] = []
        for path, method, operation in operations:
            responses = operation.get("responses", {})
            for status in REQUIRED_ERROR_STATUSES:
                schema = (
                    responses.get(status, {})
                    .get("content", {})
                    .get("application/json", {})
                    .get("schema", {})
                )
                if schema.get("$ref") != ERROR_ENVELOPE_REF:
                    wrong.append(f"{method.upper()} {path} -> {status} {schema}")

        assert not wrong, "error responses that do not reference ErrorEnvelope:\n" + "\n".join(
            wrong
        )

    def test_the_document_contains_the_auth_and_not_found_statuses(
        self, committed_schema: dict
    ) -> None:
        text = json.dumps(committed_schema)
        for status in ("401", "403", "404"):
            assert f'"{status}"' in text, f"the document has no {status} response at all"

    def test_the_envelope_components_exist_and_are_referenced(
        self, committed_schema: dict
    ) -> None:
        schemas = committed_schema["components"]["schemas"]
        for name in ("ErrorEnvelope", "ErrorDetail", "CodeEnum"):
            assert name in schemas, f"{name} is not declared"

        text = json.dumps(committed_schema)
        assert ERROR_ENVELOPE_REF in text
        assert "#/components/schemas/ErrorDetail" in text
        assert "#/components/schemas/CodeEnum" in text


class TestCodeEnumIsDerivedFromTheRuntime:
    def test_the_enum_matches_the_single_source_of_truth(
        self, committed_schema: dict
    ) -> None:
        enum = committed_schema["components"]["schemas"]["CodeEnum"]["enum"]
        assert enum == list(ERROR_CODES)

    def test_the_enum_contains_every_code_the_runtime_raises(
        self, committed_schema: dict
    ) -> None:
        enum = set(committed_schema["components"]["schemas"]["CodeEnum"]["enum"])
        runtime = _runtime_error_codes()

        assert runtime, "the scan found no error codes at all"
        missing = sorted(runtime - enum)
        assert not missing, f"codes raised at runtime but absent from CodeEnum: {missing}"

    def test_the_codes_named_in_the_contract_are_published(
        self, committed_schema: dict
    ) -> None:
        enum = set(committed_schema["components"]["schemas"]["CodeEnum"]["enum"])
        required = {
            "overpayment",
            "invalid_signature",
            "invalid_dicom",
            "invalid_credentials",
            "mfa_required",
            "invalid_otp",
            "last_owner",
            "invitation_expired",
            "invitation_used",
            "invitation_revoked",
            "invitation_email_mismatch",
            "invalid_token",
            "expired",
            "policy_cutoff",
            "refund_exceeds_captured",
            "connector_inactive",
            "invalid_arguments",
            "confirmation_required",
            "confirmation_invalid",
            "identity_required",
        }
        assert required <= enum, sorted(required - enum)


class TestPaginationComponentNames:
    def test_no_component_stutters_with_list_list(
        self, committed_schema: dict
    ) -> None:
        stuttering = [
            name
            for name in committed_schema["components"]["schemas"]
            if name.startswith("Paginated") and name.endswith("ListList")
        ]
        assert not stuttering, f"stuttering pagination components: {stuttering}"

    def test_the_patient_pagination_component_has_a_clean_name(
        self, committed_schema: dict
    ) -> None:
        schemas = committed_schema["components"]["schemas"]
        assert "PaginatedPatientList" in schemas
        assert "PaginatedPatientListList" not in schemas

    def test_the_paginated_name_helper_strips_a_single_trailing_list(self) -> None:
        from apps.core.schema import EnamelAutoSchema

        schema = EnamelAutoSchema()
        assert schema.get_paginated_name("PatientList") == "PaginatedPatientList"
        assert schema.get_paginated_name("Allergy") == "PaginatedAllergyList"
        assert schema.get_paginated_name("PortalAppointment") == "PaginatedPortalAppointmentList"


def test_the_error_module_has_no_duplicate_codes() -> None:
    assert len(ERROR_CODES) == len(set(ERROR_CODES))


def test_the_error_codes_are_valid_identifiers() -> None:
    for code in ERROR_CODES:
        assert code.islower() and code.replace("_", "").isalnum(), code


def test_the_schema_file_is_valid_python_free_json() -> None:
    # Cheap guard against a hand-edited document: it has to parse and to be a
    # dictionary with the OpenAPI version this project publishes.
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert data["openapi"] == "3.1.0"
    assert isinstance(data.get("paths"), dict)


def test_the_error_module_docstring_names_the_contract() -> None:
    source = (BACKEND_DIR / "apps" / "core" / "errors.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert ast.get_docstring(tree), "apps/core/errors.py has no module docstring"
