"""Integer money helpers (ARCHITECTURE.md section 9).

All money is stored and computed as integer *minor units* (paise, cents) plus an
ISO-4217 ``currency``. There is deliberately no ``float`` and no ``Decimal``
anywhere in a money path: binary floating point cannot represent 0.1 exactly and
``Decimal`` invites silent scale drift between the database and the application.

Tax is expressed in *basis points* (1 bp = 0.01%, so 1800 bp = 18%). Rounding is
half-up at line level and totals are summed from the already-rounded lines, which
is the documented, tested rule (a 0.005 remainder rounds away from zero).

The helpers here are intentionally tiny; they are imported by both ``plans`` and
``billing`` so the two apps can never disagree about what a line total means.
"""

from __future__ import annotations

from dataclasses import dataclass

#: 1 basis point = 1/10000; 10000 bp = 100%.
BASIS_POINTS = 10_000


class CurrencyMismatch(ValueError):
    """Two amounts in different currencies were combined."""


def round_half_up(numerator: int, denominator: int) -> int:
    """Return ``numerator / denominator`` rounded half-up, using integers only.

    ``denominator`` must be positive. A tie (exactly ``.5``) rounds away from
    zero, matching the documented line-rounding rule. Negative numerators round
    symmetrically so a credit note is the exact mirror of its charge.
    """

    if denominator <= 0:
        raise ValueError("denominator must be a positive integer")
    if numerator >= 0:
        return (numerator + denominator // 2) // denominator
    return -((-numerator + denominator // 2) // denominator)


def tax_minor(taxable_minor: int, tax_rate_bp: int) -> int:
    """Return the tax on ``taxable_minor`` at ``tax_rate_bp`` basis points."""

    if tax_rate_bp < 0:
        raise ValueError("tax_rate_bp must not be negative")
    return round_half_up(taxable_minor * tax_rate_bp, BASIS_POINTS)


def line_tax_minor(
    quantity: int,
    unit_price_minor: int,
    discount_minor: int = 0,
    tax_rate_bp: int = 0,
) -> int:
    """Tax for one line: ``(qty * unit - discount) * rate`` rounded half-up."""

    taxable = quantity * unit_price_minor - discount_minor
    return tax_minor(taxable, tax_rate_bp)


def line_total_minor(
    quantity: int,
    unit_price_minor: int,
    discount_minor: int = 0,
    tax_rate_bp: int = 0,
) -> int:
    """Net total for one line: taxable amount plus rounded tax."""

    taxable = quantity * unit_price_minor - discount_minor
    return taxable + tax_minor(taxable, tax_rate_bp)


def line_subtotal_minor(quantity: int, unit_price_minor: int) -> int:
    """Gross (pre-discount, pre-tax) amount for one line."""

    return quantity * unit_price_minor


@dataclass(frozen=True)
class Money:
    """An amount in minor units with its currency.

    Addition and subtraction assert that both operands share a currency, so a
    bug that mixes two clinics' currencies fails loudly instead of silently
    adding apples to oranges.
    """

    amount_minor: int
    currency: str

    def _assert_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(
                f"cannot combine {self.currency} and {other.currency}"
            )

    def __add__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def multiply(self, factor: int) -> Money:
        return Money(self.amount_minor * factor, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount_minor, self.currency)

    def __str__(self) -> str:
        return f"{self.amount_minor} {self.currency}"


__all__ = [
    "BASIS_POINTS",
    "CurrencyMismatch",
    "Money",
    "line_subtotal_minor",
    "line_tax_minor",
    "line_total_minor",
    "round_half_up",
    "tax_minor",
]
