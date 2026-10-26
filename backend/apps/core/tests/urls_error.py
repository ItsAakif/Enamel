"""Throw-away URLconf used by ``test_prod_security.py`` to force a 500."""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.urls import path


def boom(request: HttpRequest) -> HttpResponse:
    raise RuntimeError("secret-token-do-not-leak")


urlpatterns = [
    path("boom/", boom),
]
