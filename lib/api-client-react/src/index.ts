export * from "./generated/api";
export * from "./generated/api.schemas";
export {
  ApiError,
  ResponseParseError,
  customFetch,
  isApiErrorEnvelope,
  setAuthTokenGetter,
  setBaseUrl,
  setClinicId,
  getClinicId,
  setCsrfCookieName,
} from "./custom-fetch";
export type {
  ApiErrorDetail,
  ApiErrorEnvelope,
  AuthTokenGetter,
  BodyType,
  CustomFetchOptions,
  ErrorType,
  ResponseType,
} from "./custom-fetch";
