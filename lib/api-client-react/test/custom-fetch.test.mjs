import assert from "node:assert/strict";
import test from "node:test";

import {
  ApiError,
  customFetch,
  setBaseUrl,
  setClinicId,
  setCsrfCookieName,
  setAuthTokenGetter,
} from "../src/custom-fetch.ts";

const originalFetch = globalThis.fetch;

function resetClient() {
  setBaseUrl(null);
  setClinicId(null);
  setCsrfCookieName(null);
  setAuthTokenGetter(null);
}

function setDocumentCookie(cookie) {
  if (cookie === null) {
    delete globalThis.document;
    return;
  }
  globalThis.document = { cookie };
}

function mockFetch(handler) {
  const calls = [];
  globalThis.fetch = async (input, init) => {
    calls.push({ input, init });
    const result = await handler(input, init);
    return result instanceof Response ? result : new Response(result.body, result);
  };
  return calls;
}

function jsonResponse(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

test.afterEach(() => {
  globalThis.fetch = originalFetch;
  resetClient();
  setDocumentCookie(null);
});

test("sends credentials: include by default", async () => {
  const calls = mockFetch(() => jsonResponse({ ok: true }));
  await customFetch("/api/v1/auth/me/");

  assert.equal(calls.length, 1);
  assert.equal(calls[0].init.credentials, "include");
});

test("allows an explicit credentials override", async () => {
  const calls = mockFetch(() => jsonResponse({ ok: true }));
  await customFetch("/api/v1/auth/me/", { credentials: "omit" });

  assert.equal(calls[0].init.credentials, "omit");
});

test("sets X-CSRFToken from the csrftoken cookie on unsafe methods only", async () => {
  setDocumentCookie("csrftoken=csrf-abc123; enamel_session=ignored");
  const calls = mockFetch(() => jsonResponse({ ok: true }));

  await customFetch("/api/v1/patients/", { method: "POST", body: "{}" });
  await customFetch("/api/v1/patients/1/", { method: "PATCH", body: "{}" });
  await customFetch("/api/v1/patients/1/", { method: "DELETE" });
  await customFetch("/api/v1/patients/");
  await customFetch("/api/v1/patients/", { method: "HEAD" });

  const unsafe = calls.slice(0, 3).map((call) => call.init.headers.get("X-CSRFToken"));
  assert.deepEqual(unsafe, ["csrf-abc123", "csrf-abc123", "csrf-abc123"]);
  assert.equal(calls[3].init.headers.get("X-CSRFToken"), null);
  assert.equal(calls[4].init.headers.get("X-CSRFToken"), null);
});

test("does not overwrite an explicit CSRF header and honours skipCsrf", async () => {
  setDocumentCookie("csrftoken=csrf-abc123");
  const calls = mockFetch(() => jsonResponse({ ok: true }));

  await customFetch("/api/v1/patients/", {
    method: "POST",
    headers: { "X-CSRFToken": "explicit" },
    body: "{}",
  });
  await customFetch("/api/v1/public/bookings/", { method: "POST", body: "{}", skipCsrf: true });

  assert.equal(calls[0].init.headers.get("X-CSRFToken"), "explicit");
  assert.equal(calls[1].init.headers.get("X-CSRFToken"), null);
});

test("a configurable CSRF cookie name is read", async () => {
  setCsrfCookieName("portal_csrf");
  setDocumentCookie("portal_csrf=portal-token");
  const calls = mockFetch(() => jsonResponse({ ok: true }));

  await customFetch("/api/v1/portal/auth/logout/", { method: "POST", body: "{}" });
  assert.equal(calls[0].init.headers.get("X-CSRFToken"), "portal-token");
});

test("sets X-Clinic-Id from the module scope and per-request override", async () => {
  const clinicId = "01920000-0000-7000-8000-000000000001";
  setClinicId(clinicId);
  const calls = mockFetch(() => jsonResponse({ ok: true }));

  await customFetch("/api/v1/clinics/");
  await customFetch("/api/v1/clinics/", { clinicId: "override-clinic" });
  await customFetch("/api/v1/clinics/", { clinicId: null });

  assert.equal(calls[0].init.headers.get("X-Clinic-Id"), clinicId);
  assert.equal(calls[1].init.headers.get("X-Clinic-Id"), "override-clinic");
  assert.equal(calls[2].init.headers.get("X-Clinic-Id"), null);
});

test("sets Idempotency-Key when requested", async () => {
  const calls = mockFetch(() => jsonResponse({ id: "1" }, 201));
  await customFetch("/api/v1/invoices/", {
    method: "POST",
    body: "{}",
    idempotencyKey: "key-123",
  });

  assert.equal(calls[0].init.headers.get("Idempotency-Key"), "key-123");
});

test("parses the RFC 9457 envelope into a typed ApiError", async () => {
  mockFetch(() =>
    jsonResponse(
      {
        error: {
          code: "slot_conflict",
          message: "The selected slot is no longer available.",
          field_errors: { start_at: ["conflicts with an existing appointment"] },
          request_id: "req_01JTEST",
          details: { appointment_id: "appt-1" },
        },
      },
      409,
      { "retry-after": "30" },
    ),
  );

  await assert.rejects(
    () => customFetch("/api/v1/appointments/", { method: "POST", body: "{}" }),
    (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, 409);
      assert.equal(error.code, "slot_conflict");
      assert.equal(error.apiMessage, "The selected slot is no longer available.");
      assert.deepEqual(error.fieldErrors.start_at, [
        "conflicts with an existing appointment",
      ]);
      assert.equal(error.requestId, "req_01JTEST");
      assert.deepEqual(error.details, { appointment_id: "appt-1" });
      assert.equal(error.retryAfter, 30);
      assert.match(error.message, /slot_conflict/);
      return true;
    },
  );
});

test("falls back to request id header and non-envelope errors", async () => {
  mockFetch(() =>
    jsonResponse({ detail: "Nope." }, 403, { "x-request-id": "req_header" }),
  );

  await assert.rejects(
    () => customFetch("/api/v1/clinics/"),
    (error) => {
      assert.equal(error.status, 403);
      assert.equal(error.code, null);
      assert.equal(error.requestId, "req_header");
      assert.match(error.message, /Nope\./);
      return true;
    },
  );
});

test("parses successful JSON and handles 204", async () => {
  mockFetch(() => jsonResponse({ id: "abc" }));
  const body = await customFetch("/api/v1/clinics/");
  assert.deepEqual(body, { id: "abc" });

  globalThis.fetch = async () => new Response(null, { status: 204 });
  const empty = await customFetch("/api/v1/clinics/1/", { method: "DELETE" });
  assert.equal(empty, null);
});

test("applies the configured base URL to relative paths", async () => {
  setBaseUrl("http://127.0.0.1:8000");
  const calls = mockFetch(() => jsonResponse({ ok: true }));

  await customFetch("/api/v1/auth/me/");
  assert.equal(calls[0].input, "http://127.0.0.1:8000/api/v1/auth/me/");
});

test("still supports the bearer token getter", async () => {
  setAuthTokenGetter(() => "token-abc");
  const calls = mockFetch(() => jsonResponse({ ok: true }));

  await customFetch("/api/v1/auth/me/");
  assert.equal(calls[0].init.headers.get("authorization"), "Bearer token-abc");
});
