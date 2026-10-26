export type ResponseType = "json" | "text" | "blob" | "auto";

export type CustomFetchOptions = RequestInit & {
  responseType?: ResponseType;
  /**
   * Override the module-level clinic scope for this request. `undefined` uses
   * `setClinicId(...)`; `null` explicitly sends no `X-Clinic-Id` header.
   */
  clinicId?: string | null;
  /** Sets `Idempotency-Key` on unsafe creates. */
  idempotencyKey?: string | null;
  /** Escape hatch for public/unauthenticated calls that must not send CSRF. */
  skipCsrf?: boolean;
};

export type ErrorType<T = unknown> = ApiError<T>;

export type BodyType<T> = T;

export type AuthTokenGetter = () => Promise<string | null> | string | null;

const NO_BODY_STATUS = new Set([204, 205, 304]);
const DEFAULT_JSON_ACCEPT = "application/json, application/problem+json";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const DEFAULT_CSRF_COOKIE_NAME = "csrftoken";
const CLINIC_ID_HEADER = "X-Clinic-Id";
const IDEMPOTENCY_KEY_HEADER = "Idempotency-Key";
const CSRF_HEADER = "X-CSRFToken";

// ---------------------------------------------------------------------------
// Module-level configuration
// ---------------------------------------------------------------------------

let _baseUrl: string | null = null;
let _authTokenGetter: AuthTokenGetter | null = null;
let _clinicId: string | null = null;
let _csrfCookieName = DEFAULT_CSRF_COOKIE_NAME;

/**
 * Set a base URL that is prepended to every relative request URL
 * (i.e. paths that start with `/`).
 *
 * Useful for Expo bundles that need to call a remote API server.
 * Pass `null` to clear the base URL.
 */
export function setBaseUrl(url: string | null): void {
  _baseUrl = url ? url.replace(/\/+$/, "") : null;
}

/**
 * Register a getter that supplies a bearer auth token.  Before every fetch
 * the getter is invoked; when it returns a non-null string, an
 * `Authorization: Bearer <token>` header is attached to the request.
 *
 * Useful for Expo bundles making token-gated API calls.
 * Pass `null` to clear the getter.
 */
export function setAuthTokenGetter(getter: AuthTokenGetter | null): void {
  _authTokenGetter = getter;
}

/**
 * Set the active clinic scope. Every request then carries `X-Clinic-Id`,
 * which the backend resolves against the caller's active membership.
 * Pass `null` to clear it (single-membership accounts do not need the header).
 */
export function setClinicId(clinicId: string | null): void {
  _clinicId = clinicId && clinicId.trim() !== "" ? clinicId.trim() : null;
}

export function getClinicId(): string | null {
  return _clinicId;
}

/**
 * CSRF cookie name (default `csrftoken`, matching Django's settings).
 * Exposed so the portal or embedded clients can point at a different cookie.
 */
export function setCsrfCookieName(name: string | null): void {
  _csrfCookieName = name && name.trim() !== "" ? name.trim() : DEFAULT_CSRF_COOKIE_NAME;
}

function isRequest(input: RequestInfo | URL): input is Request {
  return typeof Request !== "undefined" && input instanceof Request;
}

function resolveMethod(input: RequestInfo | URL, explicitMethod?: string): string {
  if (explicitMethod) return explicitMethod.toUpperCase();
  if (isRequest(input)) return input.method.toUpperCase();
  return "GET";
}

// Use loose check for URL — some runtimes (e.g. React Native) polyfill URL
// differently, so `instanceof URL` can fail.
function isUrl(input: RequestInfo | URL): input is URL {
  return typeof URL !== "undefined" && input instanceof URL;
}

function applyBaseUrl(input: RequestInfo | URL): RequestInfo | URL {
  if (!_baseUrl) return input;
  const url = resolveUrl(input);
  // Only prepend to relative paths (starting with /)
  if (!url.startsWith("/")) return input;

  const absolute = `${_baseUrl}${url}`;
  if (typeof input === "string") return absolute;
  if (isUrl(input)) return new URL(absolute);
  return new Request(absolute, input as Request);
}

function resolveUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (isUrl(input)) return input.toString();
  return input.url;
}

function mergeHeaders(...sources: Array<HeadersInit | undefined>): Headers {
  const headers = new Headers();

  for (const source of sources) {
    if (!source) continue;
    new Headers(source).forEach((value, key) => {
      headers.set(key, value);
    });
  }

  return headers;
}

function getMediaType(headers: Headers): string | null {
  const value = headers.get("content-type");
  return value ? value.split(";", 1)[0].trim().toLowerCase() : null;
}

function isJsonMediaType(mediaType: string | null): boolean {
  return mediaType === "application/json" || Boolean(mediaType?.endsWith("+json"));
}

function isTextMediaType(mediaType: string | null): boolean {
  return Boolean(
    mediaType &&
      (mediaType.startsWith("text/") ||
        mediaType === "application/xml" ||
        mediaType === "text/xml" ||
        mediaType.endsWith("+xml") ||
        mediaType === "application/x-www-form-urlencoded"),
  );
}

// Use strict equality: in browsers, `response.body` is `null` when the
// response genuinely has no content.  In React Native, `response.body` is
// always `undefined` because the ReadableStream API is not implemented —
// even when the response carries a full payload readable via `.text()` or
// `.json()`.  Loose equality (`== null`) matches both `null` and `undefined`,
// which causes every React Native response to be treated as empty.
function hasNoBody(response: Response, method: string): boolean {
  if (method === "HEAD") return true;
  if (NO_BODY_STATUS.has(response.status)) return true;
  if (response.headers.get("content-length") === "0") return true;
  if (response.body === null) return true;
  return false;
}

function stripBom(text: string): string {
  return text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
}

function looksLikeJson(text: string): boolean {
  const trimmed = text.trimStart();
  return trimmed.startsWith("{") || trimmed.startsWith("[");
}

function getStringField(value: unknown, key: string): string | undefined {
  if (!value || typeof value !== "object") return undefined;

  const candidate = (value as Record<string, unknown>)[key];
  if (typeof candidate !== "string") return undefined;

  const trimmed = candidate.trim();
  return trimmed === "" ? undefined : trimmed;
}

function truncate(text: string, maxLength = 300): string {
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

// ---------------------------------------------------------------------------
// RFC 9457-shaped envelope
// ---------------------------------------------------------------------------

export interface ApiErrorDetail {
  code: string;
  message: string;
  field_errors: Record<string, string[]>;
  request_id: string | null;
  details: Record<string, unknown>;
}

export interface ApiErrorEnvelope {
  error: ApiErrorDetail;
}

export function isApiErrorEnvelope(value: unknown): value is ApiErrorEnvelope {
  if (!value || typeof value !== "object") return false;
  const error = (value as Record<string, unknown>)["error"];
  if (!error || typeof error !== "object") return false;
  const code = (error as Record<string, unknown>)["code"];
  const message = (error as Record<string, unknown>)["message"];
  return typeof code === "string" && typeof message === "string";
}

function readErrorDetail(value: unknown): ApiErrorDetail | null {
  if (!isApiErrorEnvelope(value)) return null;

  const error = value.error;
  return {
    code: error.code,
    message: error.message,
    field_errors:
      error.field_errors && typeof error.field_errors === "object"
        ? (error.field_errors as Record<string, string[]>)
        : {},
    request_id: typeof error.request_id === "string" ? error.request_id : null,
    details:
      error.details && typeof error.details === "object"
        ? (error.details as Record<string, unknown>)
        : {},
  };
}

function parseRetryAfter(headers: Headers): number | null {
  const value = headers.get("retry-after");
  if (!value) return null;
  const seconds = Number.parseInt(value.trim(), 10);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

function buildErrorMessage(response: Response, data: unknown): string {
  const prefix = `HTTP ${response.status} ${response.statusText}`;

  if (typeof data === "string") {
    const text = data.trim();
    return text ? `${prefix}: ${truncate(text)}` : prefix;
  }

  const envelope = readErrorDetail(data);
  if (envelope) return `${prefix} [${envelope.code}]: ${truncate(envelope.message)}`;

  const title = getStringField(data, "title");
  const detail = getStringField(data, "detail");
  const message =
    getStringField(data, "message") ??
    getStringField(data, "error_description") ??
    getStringField(data, "error");

  if (title && detail) return `${prefix}: ${title} — ${detail}`;
  if (detail) return `${prefix}: ${detail}`;
  if (message) return `${prefix}: ${message}`;
  if (title) return `${prefix}: ${title}`;

  return prefix;
}

export class ApiError<T = unknown> extends Error {
  readonly name = "ApiError";
  readonly status: number;
  readonly statusText: string;
  readonly data: T | null;
  readonly headers: Headers;
  readonly response: Response;
  readonly method: string;
  readonly url: string;
  /** Stable machine code from the API envelope, when present. */
  readonly code: string | null;
  /** The `error.message` from the API envelope, when present. */
  readonly apiMessage: string | null;
  readonly fieldErrors: Record<string, string[]>;
  readonly requestId: string | null;
  readonly details: Record<string, unknown>;
  readonly retryAfter: number | null;

  constructor(
    response: Response,
    data: T | null,
    requestInfo: { method: string; url: string },
  ) {
    super(buildErrorMessage(response, data));
    Object.setPrototypeOf(this, new.target.prototype);

    this.status = response.status;
    this.statusText = response.statusText;
    this.data = data;
    this.headers = response.headers;
    this.response = response;
    this.method = requestInfo.method;
    this.url = response.url || requestInfo.url;

    const envelope = readErrorDetail(data);
    this.code = envelope?.code ?? null;
    this.apiMessage = envelope?.message ?? null;
    this.fieldErrors = envelope?.field_errors ?? {};
    this.requestId =
      envelope?.request_id ?? response.headers.get("X-Request-Id") ?? null;
    this.details = envelope?.details ?? {};
    this.retryAfter = parseRetryAfter(response.headers);
  }
}

export class ResponseParseError extends Error {
  readonly name = "ResponseParseError";
  readonly status: number;
  readonly statusText: string;
  readonly headers: Headers;
  readonly response: Response;
  readonly method: string;
  readonly url: string;
  readonly rawBody: string;
  readonly cause: unknown;

  constructor(
    response: Response,
    rawBody: string,
    cause: unknown,
    requestInfo: { method: string; url: string },
  ) {
    super(
      `Failed to parse response from ${requestInfo.method} ${response.url || requestInfo.url} ` +
        `(${response.status} ${response.statusText}) as JSON`,
    );
    Object.setPrototypeOf(this, new.target.prototype);

    this.status = response.status;
    this.statusText = response.statusText;
    this.headers = response.headers;
    this.response = response;
    this.method = requestInfo.method;
    this.url = response.url || requestInfo.url;
    this.rawBody = rawBody;
    this.cause = cause;
  }
}

async function parseJsonBody(
  response: Response,
  requestInfo: { method: string; url: string },
): Promise<unknown> {
  const raw = await response.text();
  const normalized = stripBom(raw);

  if (normalized.trim() === "") {
    return null;
  }

  try {
    return JSON.parse(normalized);
  } catch (cause) {
    throw new ResponseParseError(response, raw, cause, requestInfo);
  }
}

async function parseErrorBody(response: Response, method: string): Promise<unknown> {
  if (hasNoBody(response, method)) {
    return null;
  }

  const mediaType = getMediaType(response.headers);

  // Fall back to text when blob() is unavailable (e.g. some React Native builds).
  if (mediaType && !isJsonMediaType(mediaType) && !isTextMediaType(mediaType)) {
    return typeof response.blob === "function" ? response.blob() : response.text();
  }

  const raw = await response.text();
  const normalized = stripBom(raw);
  const trimmed = normalized.trim();

  if (trimmed === "") {
    return null;
  }

  if (isJsonMediaType(mediaType) || looksLikeJson(normalized)) {
    try {
      return JSON.parse(normalized);
    } catch {
      return raw;
    }
  }

  return raw;
}

function inferResponseType(response: Response): "json" | "text" | "blob" {
  const mediaType = getMediaType(response.headers);

  if (isJsonMediaType(mediaType)) return "json";
  if (isTextMediaType(mediaType) || mediaType == null) return "text";
  return "blob";
}

async function parseSuccessBody(
  response: Response,
  responseType: ResponseType,
  requestInfo: { method: string; url: string },
): Promise<unknown> {
  if (hasNoBody(response, requestInfo.method)) {
    return null;
  }

  const effectiveType =
    responseType === "auto" ? inferResponseType(response) : responseType;

  switch (effectiveType) {
    case "json":
      return parseJsonBody(response, requestInfo);

    case "text": {
      const text = await response.text();
      return text === "" ? null : text;
    }

    case "blob":
      if (typeof response.blob !== "function") {
        throw new TypeError(
          "Blob responses are not supported in this runtime. " +
            "Use responseType \"json\" or \"text\" instead.",
        );
      }
      return response.blob();

    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Cookies and request headers
// ---------------------------------------------------------------------------

function readCookie(name: string): string | null {
  const cookie = typeof document !== "undefined" ? document.cookie : "";
  if (!cookie) return null;

  for (const part of cookie.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) {
      const value = rest.join("=");
      return value === "" ? null : value;
    }
  }

  return null;
}

function applyDefaultHeaders(
  headers: Headers,
  method: string,
  options: CustomFetchOptions,
): void {
  if (options.responseType === "json" && !headers.has("accept")) {
    headers.set("accept", DEFAULT_JSON_ACCEPT);
  }

  const clinicId =
    options.clinicId !== undefined ? options.clinicId : _clinicId;
  if (clinicId && !headers.has(CLINIC_ID_HEADER)) {
    headers.set(CLINIC_ID_HEADER, clinicId);
  }

  if (options.idempotencyKey && !headers.has(IDEMPOTENCY_KEY_HEADER)) {
    headers.set(IDEMPOTENCY_KEY_HEADER, options.idempotencyKey);
  }

  if (!options.skipCsrf && UNSAFE_METHODS.has(method) && !headers.has(CSRF_HEADER)) {
    const token = readCookie(_csrfCookieName);
    if (token) headers.set(CSRF_HEADER, token);
  }
}

export async function customFetch<T = unknown>(
  input: RequestInfo | URL,
  options: CustomFetchOptions = {},
): Promise<T> {
  input = applyBaseUrl(input);
  const {
    responseType = "auto",
    headers: headersInit,
    clinicId: _clinicIdOption,
    idempotencyKey: _idempotencyKeyOption,
    skipCsrf: _skipCsrfOption,
    ...init
  } = options;

  const method = resolveMethod(input, init.method);

  if (init.body != null && (method === "GET" || method === "HEAD")) {
    throw new TypeError(`customFetch: ${method} requests cannot have a body.`);
  }

  const headers = mergeHeaders(isRequest(input) ? input.headers : undefined, headersInit);

  if (
    typeof init.body === "string" &&
    !headers.has("content-type") &&
    looksLikeJson(init.body)
  ) {
    headers.set("content-type", "application/json");
  }

  applyDefaultHeaders(headers, method, options);

  // Attach bearer token when an auth getter is configured and no
  // Authorization header has been explicitly provided.
  if (_authTokenGetter && !headers.has("authorization")) {
    const token = await _authTokenGetter();
    if (token) {
      headers.set("authorization", `Bearer ${token}`);
    }
  }

  const requestInfo = { method, url: resolveUrl(input) };

  const response = await fetch(input, {
    credentials: "include",
    ...init,
    method,
    headers,
  });

  if (!response.ok) {
    const errorData = await parseErrorBody(response, method);
    throw new ApiError(response, errorData, requestInfo);
  }

  return (await parseSuccessBody(response, responseType, requestInfo)) as T;
}
