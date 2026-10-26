"""Money hygiene and card-data absence (T119)."""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

import pytest

from apps.billing.models import Payment, WebhookEvent

pytestmark = pytest.mark.django_db

#: Modules whose only job is money arithmetic. They must be integer-only.
MONEY_MODULES = (
    "apps/core/money.py",
    "apps/plans/services/plans.py",
    "apps/billing/services/invoices.py",
    "apps/billing/services/payments.py",
    "apps/billing/services/refunds.py",
    "apps/billing/services/numbering.py",
)

#: Field-name tokens that would mean card data has leaked into the schema.
FORBIDDEN_FIELD_TOKENS = (
    "card_number",
    "card_no",
    "cardnumber",
    "cvv",
    "cvc",
    "card_token",
    "cardholder",
    "pan_number",
    "card_pan",
    "card_expiry",
    "expiry_date",
)

PAN_RE = re.compile(r"(?<!\d)\d{13,19}(?!\d)")

#: Identifiers that are hexadecimal by construction: the request id
#: (``req_`` + 32 hex characters) and a UUID. A run of 13-19 digits inside one
#: of them is an identifier, not a card number - a random request id contains
#: such a run in roughly one of twenty cases, which made this test fail at
#: random. They are removed before the scan; a card number is a bare run of
#: digits and is never removed by this pattern.
IDENTIFIER_RE = re.compile(
    r"\b(?:req_[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.IGNORECASE,
)

BACKEND_DIR = Path(__file__).resolve().parents[3]


def _source(relative: str) -> str:
    return (BACKEND_DIR / relative).read_text(encoding="utf-8")


def _float_or_decimal_uses(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"float", "Decimal"}:
                found.append(f"call:{node.func.id}@{node.lineno}")
        if isinstance(node, ast.Name) and node.id in {"float", "Decimal"}:
            found.append(f"name:{node.id}@{node.lineno}")
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            found.append(f"literal:float@{node.lineno}")
        if isinstance(node, ast.ImportFrom) and node.module == "decimal":
            found.append(f"import:decimal@{node.lineno}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "decimal":
                    found.append(f"import:decimal@{node.lineno}")
    return found


@pytest.mark.parametrize("module", MONEY_MODULES)
def test_money_modules_are_integer_only(module):
    tree = ast.parse(_source(module))
    assert _float_or_decimal_uses(tree) == []


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    ids.add(id(body[0].value))
    return ids


def _declared_names(relative: str) -> set[str]:
    """Assignment target names plus non-docstring string literals."""

    tree = ast.parse(_source(relative))
    docstrings = _docstring_node_ids(tree)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                names.add(node.value)
    return names


def test_no_card_fields_in_models_or_serializers():
    for relative in ("apps/billing/models.py", "apps/billing/serializers.py"):
        names = {name.lower() for name in _declared_names(relative)}
        for token in FORBIDDEN_FIELD_TOKENS:
            assert token not in names, f"{token} found in {relative}"


def _scan_for_pan(text: str) -> bool:
    return PAN_RE.search(IDENTIFIER_RE.sub(" ", text)) is not None


def test_the_pan_scan_ignores_identifiers_but_still_catches_card_numbers():
    # A request id and a UUID are hexadecimal identifiers; a long digit run
    # inside one of them is not a card number.
    assert not _scan_for_pan("req_01a15535420377569961bdb4eafb5d34")
    assert not _scan_for_pan("payment 01a15535-41e7-76af-8508-49862c969691 refunded")
    # A bare run of 13-19 digits is still a card number.
    assert _scan_for_pan("card 4111111111111111")
    assert _scan_for_pan("card 4111111111111")
    assert _scan_for_pan("'4111111111111111'")
    # A shorter run is not a card number.
    assert not _scan_for_pan("invoice 411111111111")
    # A longer run is not a single card number either.
    assert not _scan_for_pan("41111111111111111111")


def test_payment_flow_logs_contain_no_pan_like_sequence(
    caplog, owner, patient_factory, invoice_factory
):
    caplog.set_level(logging.DEBUG)
    patient = patient_factory(owner.clinic)
    invoice = invoice_factory(owner.clinic, patient)

    owner.post(
        f"/api/v1/invoices/{invoice.id}/payments/",
        {"amount_minor": 12_345, "method": "upi"},
        HTTP_IDEMPOTENCY_KEY="pan-scan-1",
    )
    invoice.refresh_from_db()
    payment = Payment.all_objects.get(invoice=invoice)
    owner.post(
        f"/api/v1/payments/{payment.id}/refund/",
        {"amount_minor": 1_000, "reason": "goodwill"},
        HTTP_IDEMPOTENCY_KEY="pan-scan-2",
    )

    for record in caplog.records:
        blob = record.getMessage()
        extra = {
            key: value
            for key, value in vars(record).items()
            if key not in {"msg", "args", "exc_info", "exc_text", "stack_info"}
        }
        blob += " " + str(extra)
        assert not _scan_for_pan(blob), blob

    for row in Payment.all_objects.values_list("metadata", flat=True):
        assert not _scan_for_pan(str(row))
    for row in WebhookEvent.objects.values_list("payload_json", flat=True):
        assert not _scan_for_pan(str(row))


def test_payment_metadata_never_accepts_card_fields(owner, patient_factory, invoice_factory):
    patient = patient_factory(owner.clinic)
    invoice = invoice_factory(owner.clinic, patient)
    response = owner.post(
        f"/api/v1/invoices/{invoice.id}/payments/",
        {
            "amount_minor": 5_000,
            "method": "card",
            "gateway": "sandbox",
            "gateway_payment_id": "sbx_ok",
            "metadata": {"status_code": "ok"},
        },
        HTTP_IDEMPOTENCY_KEY="meta-1",
    )
    assert response.status_code == 201, response.json()
    payment = Payment.all_objects.get(invoice=invoice)
    assert "card" not in payment.metadata
    assert set(payment.metadata) == {"status_code"}
