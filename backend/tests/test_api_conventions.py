"""T102 API contract: errors, pagination, idempotency, throttles and schema.

The probes live in ``tests/urls_api_conventions.py`` and are mounted only for
this module, so they never leak into the committed OpenAPI document.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.db import connections
from django.utils import timezone
from drf_spectacular.validation import validate_schema
from rest_framework.test import APIClient

from apps.clinics.models import Location
from apps.core.idempotency import IDEMPOTENCY_HEADER
from apps.core.models import IdempotencyRecord

PROBE_URLS = "tests.urls_api_conventions"
ENVELOPE_KEYS = {"code", "message", "field_errors", "request_id", "details"}


def _assert_envelope(response, *, status_code: int, code: str):
    assert response.status_code == status_code
    assert response["Content-Type"].startswith("application/json")
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == ENVELOPE_KEYS
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert body["error"]["field_errors"] == {} or isinstance(
        body["error"]["field_errors"], dict
    )
    assert isinstance(body["error"]["details"], dict)
    assert body["error"]["request_id"] == response["X-Request-Id"]
    return body["error"]


@pytest.mark.urls(PROBE_URLS)
@pytest.mark.django_db
class TestErrorEnvelope:
    def test_validation_error_maps_to_400(self, api_client):
        response = api_client.post("/probe/validation/", {}, format="json")
        error = _assert_envelope(response, status_code=400, code="validation_error")
        assert "name" in error["field_errors"]

    def test_unauthenticated_maps_to_401(self, api_client):
        response = api_client.get("/probe/auth/")
        _assert_envelope(response, status_code=401, code="unauthenticated")

    def test_permission_denied_maps_to_403(self, api_client):
        response = api_client.get("/probe/permission/")
        _assert_envelope(response, status_code=403, code="permission_denied")

    def test_not_found_maps_to_404(self, api_client):
        response = api_client.get("/probe/not-found/")
        error = _assert_envelope(response, status_code=404, code="not_found")
        assert "probe-missing" not in json.dumps(error)

    def test_conflict_maps_to_409(self, api_client):
        response = api_client.get("/probe/conflict/")
        _assert_envelope(response, status_code=409, code="conflict")

    def test_throttled_maps_to_429_with_retry_after(self, api_client):
        response = None
        for _ in range(6):
            response = api_client.get("/probe/auth-throttle/")
        error = _assert_envelope(response, status_code=429, code="throttled")
        assert int(response["Retry-After"]) > 0
        assert error["details"]["retry_after"] > 0

    def test_unhandled_exception_maps_to_500_without_leaking_message(self, api_client):
        response = api_client.get("/probe/server-error/")
        _assert_envelope(response, status_code=500, code="internal_error")
        assert b"boom-secret-message" not in response.content
        assert b"Traceback" not in response.content

    def test_unknown_api_path_uses_the_envelope(self, api_client):
        # handler404 is active because tests run with DEBUG=False.
        response = api_client.get("/api/v1/does-not-exist/")
        _assert_envelope(response, status_code=404, code="not_found")


@pytest.mark.urls(PROBE_URLS)
@pytest.mark.django_db
class TestPagination:
    @pytest.fixture
    def locations(self, clinic_factory):
        clinic = clinic_factory(slug="pagination-clinic")
        Location.all_objects.bulk_create(
            [
                Location(clinic=clinic, name=f"Room {index:03d}")
                for index in range(105)
            ]
        )
        return clinic

    def test_page_one_middle_and_last_envelope(self, api_client, locations):
        page1 = api_client.get("/probe/locations/", {"page_size": 25})
        assert page1.status_code == 200
        body = page1.json()
        assert set(body) == {"count", "next", "previous", "results"}
        assert body["count"] == 105
        assert body["previous"] is None
        assert body["next"] is not None
        assert len(body["results"]) == 25

        page2 = api_client.get("/probe/locations/", {"page": 2, "page_size": 25})
        body2 = page2.json()
        assert body2["previous"] is not None
        assert body2["next"] is not None
        assert len(body2["results"]) == 25

        page5 = api_client.get("/probe/locations/", {"page": 5, "page_size": 25})
        body5 = page5.json()
        assert body5["previous"] is not None
        assert body5["next"] is None
        assert len(body5["results"]) == 5

    def test_page_size_is_capped_at_100(self, api_client, locations):
        response = api_client.get("/probe/locations/", {"page_size": 5000})
        body = response.json()
        assert response.status_code == 200
        assert len(body["results"]) == 100
        assert body["count"] == 105
        assert body["next"] is not None

    @pytest.mark.parametrize("page", ["0", "-1", "999", "abc"])
    def test_invalid_page_returns_400_envelope(self, api_client, locations, page):
        response = api_client.get("/probe/locations/", {"page": page})
        error = _assert_envelope(response, status_code=400, code="invalid_page")
        assert "page" in error["field_errors"]

    def test_next_link_preserves_filters_and_ordering(self, api_client, locations):
        response = api_client.get(
            "/probe/locations/",
            {"search": "Room", "ordering": "-name", "page_size": 10, "page": 2},
        )
        body = response.json()
        assert response.status_code == 200
        assert body["previous"] is not None
        assert body["next"] is not None

        next_query = parse_qs(urlparse(body["next"]).query)
        assert next_query["page"] == ["3"]
        assert next_query["page_size"] == ["10"]
        assert next_query["ordering"] == ["-name"]
        assert next_query["search"] == ["Room"]


@pytest.mark.urls(PROBE_URLS)
@pytest.mark.django_db
class TestIdempotency:
    @pytest.fixture
    def clinic_user(self, user_factory, clinic_factory, membership_factory):
        user = user_factory("idem@bright.test")
        clinic = clinic_factory(slug="idem-clinic")
        membership_factory(clinic, user)
        return user, clinic

    def _post(self, client, clinic, key, name, *, query=""):
        return client.post(
            f"/probe/locations/idempotent/{query}",
            {"name": name},
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
            HTTP_X_CLINIC_ID=str(clinic.id),
        )

    def test_replay_returns_the_stored_response_and_one_side_effect(
        self, api_client, clinic_user
    ):
        user, clinic = clinic_user
        api_client.force_login(user)

        first = self._post(api_client, clinic, "key-replay-1", "Replay Room")
        assert first.status_code == 201
        first_body = first.json()

        second = self._post(api_client, clinic, "key-replay-1", "Replay Room")
        assert second.status_code == 201
        assert second["Idempotency-Replayed"] == "true"
        assert second.json() == first_body

        assert Location.all_objects.filter(clinic=clinic).count() == 1
        assert IdempotencyRecord.objects.count() == 1

    def test_same_key_with_different_body_returns_409(self, api_client, clinic_user):
        user, clinic = clinic_user
        api_client.force_login(user)

        assert self._post(api_client, clinic, "key-mismatch", "First").status_code == 201
        mismatch = self._post(api_client, clinic, "key-mismatch", "Second")

        error = _assert_envelope(mismatch, status_code=409, code="idempotency_mismatch")
        assert error["details"]["key"] == "key-mismatch"
        assert Location.all_objects.filter(clinic=clinic).count() == 1
        assert IdempotencyRecord.objects.count() == 1

    def test_without_key_each_request_is_independent(self, api_client, clinic_user):
        user, clinic = clinic_user
        api_client.force_login(user)

        first = self._post(api_client, clinic, "", "No Key One")
        second = self._post(api_client, clinic, "", "No Key Two")

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] != second.json()["id"]
        assert Location.all_objects.filter(clinic=clinic).count() == 2

    def test_required_key_rejects_missing_header(self, api_client, clinic_user):
        user, clinic = clinic_user
        api_client.force_login(user)

        response = api_client.post(
            "/probe/locations/idempotent-required/",
            {"name": "Required Room"},
            format="json",
            HTTP_X_CLINIC_ID=str(clinic.id),
        )
        error = _assert_envelope(response, status_code=400, code="validation_error")
        assert IDEMPOTENCY_HEADER in error["field_errors"]

    def test_expired_record_allows_a_new_request_and_is_purged(
        self, api_client, clinic_user
    ):
        user, clinic = clinic_user
        api_client.force_login(user)

        assert self._post(api_client, clinic, "key-expiry", "Before Expiry").status_code == 201
        IdempotencyRecord.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

        after = self._post(api_client, clinic, "key-expiry", "After Expiry")
        assert after.status_code == 201
        assert after.get("Idempotency-Replayed") is None
        assert after.json()["name"] == "After Expiry"
        assert Location.all_objects.filter(clinic=clinic).count() == 2
        assert IdempotencyRecord.objects.count() == 1

    @pytest.mark.django_db(transaction=True)
    def test_concurrent_same_key_has_one_side_effect(self, clinic_user):
        user, clinic = clinic_user
        body = {"name": "Concurrent Room"}
        key = "key-concurrent-1"

        # Create the sessions up front: ``force_authenticate`` is applied inside
        # DRF's view, which is too late for ClinicScopeMiddleware in the
        # pipeline. Session auth resolves the clinic before the view runs.
        clients = [APIClient(), APIClient()]
        for client in clients:
            client.force_login(user)

        def send(client, delay: float):
            connections.close_all()
            try:
                response = client.post(
                    f"/probe/locations/idempotent/?delay={delay}",
                    body,
                    format="json",
                    HTTP_IDEMPOTENCY_KEY=key,
                    HTTP_X_CLINIC_ID=str(clinic.id),
                )
                return response.status_code, response.get("Idempotency-Replayed")
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(send, clients[0], 0.4)
            time.sleep(0.05)
            second = pool.submit(send, clients[1], 0.0)
            results = [first.result(timeout=15), second.result(timeout=15)]

        assert sorted(status_code for status_code, _ in results) == [201, 201]
        assert sorted(replayed or "false" for _, replayed in results) == ["false", "true"]
        assert Location.all_objects.filter(clinic=clinic).count() == 1
        assert IdempotencyRecord.objects.count() == 1


@pytest.mark.django_db
class TestThrottles:
    def test_login_sixth_attempt_is_429_with_retry_after(self, api_client):
        responses = [
            api_client.post(
                "/api/v1/auth/login/",
                {"email": "nobody@bright.test", "password": "wrong-password"},
                format="json",
            )
            for _ in range(5)
        ]
        assert [response.status_code for response in responses] == [401] * 5

        throttled = api_client.post(
            "/api/v1/auth/login/",
            {"email": "nobody@bright.test", "password": "wrong-password"},
            format="json",
        )
        error = _assert_envelope(throttled, status_code=429, code="throttled")
        assert 0 < int(throttled["Retry-After"]) <= 60
        assert error["details"]["retry_after"] > 0

    @pytest.mark.urls(PROBE_URLS)
    def test_public_booking_eleventh_request_is_429(self, api_client):
        for _ in range(10):
            assert api_client.post("/probe/public-booking/", {}, format="json").status_code == 200

        throttled = api_client.post("/probe/public-booking/", {}, format="json")
        _assert_envelope(throttled, status_code=429, code="throttled")
        assert int(throttled["Retry-After"]) > 0

    @pytest.mark.urls(PROBE_URLS)
    def test_ai_scope_is_30_per_minute_per_user(
        self, api_client, user_factory, clinic_factory, membership_factory
    ):
        user = user_factory("ai@bright.test")
        clinic = clinic_factory(slug="ai-clinic")
        membership_factory(clinic, user)
        api_client.force_login(user)

        for _ in range(30):
            assert api_client.get("/probe/ai-throttle/").status_code == 200
        _assert_envelope(api_client.get("/probe/ai-throttle/"), status_code=429, code="throttled")

    @pytest.mark.urls(PROBE_URLS)
    def test_export_scope_is_5_per_hour_per_user(
        self, api_client, user_factory, clinic_factory, membership_factory
    ):
        user = user_factory("export@bright.test")
        clinic = clinic_factory(slug="export-clinic")
        membership_factory(clinic, user)
        api_client.force_login(user)

        for _ in range(5):
            assert api_client.get("/probe/export-throttle/").status_code == 200
        _assert_envelope(api_client.get("/probe/export-throttle/"), status_code=429, code="throttled")

    def test_configured_rates_match_the_contract(self, settings):
        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        assert rates["auth"] == "5/min"
        assert rates["public_booking"] == "10/min"
        assert rates["public_booking_phone"] == "3/min"
        assert rates["ai"] == "30/min"
        assert rates["export"] == "5/hour"


@pytest.mark.django_db
class TestOpenApiSchema:
    REQUIRED_PATHS = {
        "/api/v1/auth/login/",
        "/api/v1/auth/me/",
        "/api/v1/clinics/",
        "/api/v1/clinics/{clinic_id}/locations/",
    }

    def test_schema_endpoint_is_public_and_returns_openapi_31(self, api_client):
        response = api_client.get("/api/v1/schema/", HTTP_ACCEPT="application/json")
        assert response.status_code == 200
        schema = response.json()
        assert schema["openapi"] == "3.1.0"
        assert schema["info"]["title"] == "Enamel API"
        assert len(schema["paths"]) >= 18
        assert all(path.startswith("/api/v1/") for path in schema["paths"])
        assert self.REQUIRED_PATHS <= set(schema["paths"])

    def test_committed_schema_is_valid_and_matches_the_endpoint(self, api_client, settings):
        committed_path = settings.BASE_DIR / "openapi" / "schema.json"
        committed = json.loads(committed_path.read_text(encoding="utf-8"))

        validate_schema(committed)
        served = api_client.get("/api/v1/schema/", HTTP_ACCEPT="application/json").json()
        assert served == committed
        assert "ErrorEnvelope" in committed["components"]["schemas"]
        assert "ErrorDetail" in committed["components"]["schemas"]

    def test_export_command_is_deterministic(self, tmp_path, settings):
        out_a = tmp_path / "schema-a.json"
        out_b = tmp_path / "schema-b.json"

        call_command("export_schema", out=str(out_a))
        call_command("export_schema", out=str(out_b))

        assert out_a.read_bytes() == out_b.read_bytes()
        generated = json.loads(out_a.read_text(encoding="utf-8"))
        committed = json.loads(
            (settings.BASE_DIR / "openapi" / "schema.json").read_text(encoding="utf-8")
        )
        assert generated == committed
        assert out_a.read_bytes().endswith(b"\n")

    def test_idempotency_header_is_documented_on_creates(self, settings):
        committed = json.loads(
            (settings.BASE_DIR / "openapi" / "schema.json").read_text(encoding="utf-8")
        )
        params = committed["paths"]["/api/v1/clinics/"]["post"]["parameters"]
        names = {parameter["name"] for parameter in params}
        assert "Idempotency-Key" in names


# Keep the cache clear explicit for tests that read the shared throttle cache
# outside the autouse fixture (e.g. debugging in isolation).
def teardown_function(function):
    cache.clear()
