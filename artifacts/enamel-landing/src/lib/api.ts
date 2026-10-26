/**
 * Runtime configuration for the generated API client plus the small helpers
 * every page needs to talk to the real Django API.
 *
 * The generated client (`@workspace/api-client-react`) owns request shaping:
 * `credentials: "include"`, the double-submit CSRF header, `X-Clinic-Id` and
 * typed error parsing. This module only wires its module-level configuration
 * and normalises the few response shapes the OpenAPI document does not describe
 * precisely (list envelopes, public payloads).
 */
import { ApiError, setBaseUrl, setClinicId } from "@workspace/api-client-react";

const CLINIC_STORAGE_KEY = "enamel.activeClinicId";

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // Private mode / disabled storage: the in-memory scope still works.
  }
}

/**
 * Point the generated client at the API and restore the selected clinic.
 *
 * In development the Vite dev server proxies `/api` and `/media` to Django, so
 * a relative base URL (the default) keeps the session cookie first-party.
 */
export function configureApi(): void {
  const base = import.meta.env.VITE_API_BASE_URL ?? "";
  setBaseUrl(base === "" ? null : base);
  setClinicId(readStorage(CLINIC_STORAGE_KEY));
}

/** The clinic id currently sent as `X-Clinic-Id`, or `null`. */
export function getActiveClinicId(): string | null {
  return readStorage(CLINIC_STORAGE_KEY);
}

/**
 * Select the active clinic for every subsequent request.
 *
 * `customFetch` reads a module-level value, so `setClinicId` is what actually
 * attaches the header; localStorage is only persistence across reloads.
 */
export function setActiveClinicId(clinicId: string | null): void {
  const normalised = clinicId && clinicId.trim() !== "" ? clinicId.trim() : null;
  writeStorage(CLINIC_STORAGE_KEY, normalised);
  setClinicId(normalised);
}

/** Clear the session-scoped state (used on logout). */
export function clearActiveClinicId(): void {
  setActiveClinicId(null);
}

// ---------------------------------------------------------------------------
// Response normalisation
// ---------------------------------------------------------------------------

/**
 * A DRF page-number paginated envelope.
 *
 * The committed OpenAPI document publishes the `{count, next, previous,
 * results}` envelope for list endpoints (see `apps.core.pagination.StandardPagination`).
 * `toPage` still accepts a bare array, so a page keeps working against an
 * older or differently shaped response instead of rendering an empty list.
 */
export interface ApiPage<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export function toPage<T>(payload: unknown): ApiPage<T> {
  if (Array.isArray(payload)) {
    const results = payload as T[];
    return { count: results.length, next: null, previous: null, results };
  }
  if (payload !== null && typeof payload === "object") {
    const record = payload as {
      count?: unknown;
      next?: unknown;
      previous?: unknown;
      results?: unknown;
    };
    const results = Array.isArray(record.results) ? (record.results as T[]) : [];
    return {
      count: typeof record.count === "number" ? record.count : results.length,
      next: typeof record.next === "string" ? record.next : null,
      previous: typeof record.previous === "string" ? record.previous : null,
      results,
    };
  }
  return { count: 0, next: null, previous: null, results: [] };
}

/** Read one field from an untyped (`{[key: string]: unknown}`) API object. */
export function readString(source: unknown, key: string): string {
  if (source !== null && typeof source === "object") {
    const value = (source as Record<string, unknown>)[key];
    if (typeof value === "string") return value;
    if (typeof value === "number") return String(value);
  }
  return "";
}

/** Read a boolean field from an untyped API object. */
export function readBoolean(source: unknown, key: string): boolean {
  if (source !== null && typeof source === "object") {
    return (source as Record<string, unknown>)[key] === true;
  }
  return false;
}

/** Read a numeric field from an untyped API object. */
export function readNumber(source: unknown, key: string): number | null {
  if (source !== null && typeof source === "object") {
    const value = (source as Record<string, unknown>)[key];
    if (typeof value === "number") return value;
    if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) {
      return Number(value);
    }
  }
  return null;
}

/** Narrow an untyped array field (public clinic lists) to typed records. */
export function readRecordArray(source: unknown, key: string): Record<string, unknown>[] {
  if (source !== null && typeof source === "object") {
    const value = (source as Record<string, unknown>)[key];
    if (Array.isArray(value)) {
      return value.filter(
        (item): item is Record<string, unknown> => item !== null && typeof item === "object",
      );
    }
  }
  return [];
}

// ---------------------------------------------------------------------------
// Error helpers
// ---------------------------------------------------------------------------

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** Stable machine code from the API envelope, or `null`. */
export function apiErrorCode(error: unknown): string | null {
  return error instanceof ApiError ? error.code : null;
}

export function apiErrorStatus(error: unknown): number | null {
  return error instanceof ApiError ? error.status : null;
}

export function apiErrorFieldErrors(error: unknown): Record<string, string[]> {
  return error instanceof ApiError ? error.fieldErrors : {};
}

export function apiErrorDetails(error: unknown): Record<string, unknown> {
  return error instanceof ApiError ? error.details : {};
}

export function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.apiMessage ?? error.message;
  }
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

/** True for the "not signed in" responses (never surfaced as an error card). */
export function isUnauthenticated(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  return error.status === 401 || error.code === "unauthenticated" || error.code === "session_expired";
}

/** True for the "pick a clinic" response returned when `X-Clinic-Id` is absent. */
export function isClinicRequired(error: unknown): boolean {
  return error instanceof ApiError && error.code === "clinic_required";
}

/** A stable client-side idempotency key for unsafe creates. */
export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `idem-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
