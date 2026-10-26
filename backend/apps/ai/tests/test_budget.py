"""Monthly budget guard (T123)."""

from __future__ import annotations

import pytest

from apps.ai.models import AIInvocation
from apps.ai.providers.base import AIProviderError
from apps.ai.providers.mock import MockProvider
from apps.ai.services.budget import (
    BudgetExceeded,
    check_budget,
    monthly_budget_minor,
    spent_minor,
)
from apps.ai.services.invoke import CODE_BUDGET_EXCEEDED, invoke


class _ExplodingProvider(MockProvider):
    """A provider that fails the test if it is ever called."""

    def complete(self, messages, **kwargs):  # noqa: D102
        raise AssertionError("The provider must not be called when the budget is exhausted.")


def _spend(clinic, minor: int) -> AIInvocation:
    return AIInvocation.objects.create(
        clinic=clinic,
        provider="mock",
        model="mock-deterministic",
        purpose="chat",
        status=AIInvocation.Status.OK,
        cost_minor=minor,
    )


def test_budget_zero_disables_guard(clinic_factory, settings):
    settings.AI_MONTHLY_BUDGET_MINOR = 0
    clinic = clinic_factory()
    _spend(clinic, 10_000)
    assert monthly_budget_minor(clinic) == 0
    assert check_budget(clinic) == 0


def test_spend_is_summed_per_clinic(clinic_factory, settings):
    settings.AI_MONTHLY_BUDGET_MINOR = 100
    clinic_a = clinic_factory(slug="clinic-a")
    clinic_b = clinic_factory(slug="clinic-b")
    _spend(clinic_a, 30)
    _spend(clinic_a, 20)
    _spend(clinic_b, 5)
    assert spent_minor(clinic_a) == 50
    assert spent_minor(clinic_b) == 5


def test_check_budget_raises_when_exhausted(clinic_factory, settings):
    settings.AI_MONTHLY_BUDGET_MINOR = 40
    clinic = clinic_factory()
    _spend(clinic, 40)
    with pytest.raises(BudgetExceeded) as excinfo:
        check_budget(clinic)
    assert excinfo.value.code == "budget_exceeded"
    assert excinfo.value.budget_minor == 40


def test_clinic_setting_overrides_default(clinic_factory, settings):
    settings.AI_MONTHLY_BUDGET_MINOR = 100
    clinic = clinic_factory(settings_json={"ai": {"monthly_budget_minor": 7}})
    assert monthly_budget_minor(clinic) == 7


def test_invoke_blocks_before_provider_and_logs(clinic_factory, settings):
    settings.AI_MONTHLY_BUDGET_MINOR = 25
    clinic = clinic_factory()
    _spend(clinic, 25)

    with pytest.raises(BudgetExceeded):
        invoke(
            purpose="chat",
            messages=[{"role": "user", "content": "hello"}],
            clinic=clinic,
            provider=_ExplodingProvider(),
        )

    row = AIInvocation.objects.filter(clinic=clinic, error_code=CODE_BUDGET_EXCEEDED).first()
    assert row is not None
    assert row.status == AIInvocation.Status.ERROR
    assert row.cost_minor == 0
    assert row.prompt_tokens == 0


def test_invoke_under_budget_calls_provider(clinic_factory, settings):
    settings.AI_MONTHLY_BUDGET_MINOR = 100
    clinic = clinic_factory()
    _spend(clinic, 10)
    result = invoke(
        purpose="chat",
        messages=[{"role": "user", "content": "hello"}],
        clinic=clinic,
        provider=MockProvider(),
    )
    assert result.answer
    assert not isinstance(result.answer, AIProviderError)
