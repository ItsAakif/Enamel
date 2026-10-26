import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useLocation } from "wouter";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  authCsrfRetrieve,
  portalAuthLogoutCreate,
  portalAuthRequestCodeCreate,
  portalAuthVerifyCreate,
  portalMeRetrieve,
  type CustomFetchOptions,
  type PortalMe,
  type PortalPatient,
} from "@workspace/api-client-react";
import { clearAppCaches } from "@/lib/pwa";

/**
 * Patient-portal authentication.
 *
 * This provider talks **only** to ``/api/v1/portal/*`` and is deliberately
 * separate from the staff ``AuthProvider``: the portal authenticates with the
 * ``enamel_portal`` cookie, which the backend's ``PatientSessionAuthentication``
 * reads instead of the staff session. A staff session in the same browser is
 * irrelevant here, and the layout says so explicitly.
 */

/** Portal requests must never carry the staff ``X-Clinic-Id`` scope header. */
export const PORTAL_REQUEST: CustomFetchOptions = { clinicId: null };

export interface RequestCodeResult {
  challengeId: string;
  /** Present only when the API echoes the code (DEBUG + PORTAL_OTP_ECHO). */
  devCode: string | null;
  /** Present only when the server runs with DEMO_MODE (the hosted public demo). */
  demoCode: string | null;
  /** Present together with demoCode: the demo-mode notice. */
  demoNotice: string | null;
}

interface PortalAuthContextValue {
  patient: PortalPatient | null;
  clinicId: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: unknown;
  requestCode: (input: { clinicSlug: string; contact: string }) => Promise<RequestCodeResult>;
  verifyCode: (input: { challengeId: string; code: string }) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const PortalAuthContext = createContext<PortalAuthContextValue | null>(null);

export const PORTAL_ME_QUERY_KEY = ["portal", "me"] as const;

function isUnauthenticated(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    (error.status === 401 || error.code === "unauthenticated" || error.code === "session_expired")
  );
}

export function PortalAuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [location] = useLocation();
  const onPortalRoute = location.startsWith("/portal");

  const meQuery = useQuery({
    queryKey: PORTAL_ME_QUERY_KEY,
    queryFn: () => portalMeRetrieve(PORTAL_REQUEST),
    enabled: onPortalRoute,
    retry: false,
    staleTime: 15_000,
  });

  const data: PortalMe | undefined = meQuery.data;
  const unauthorised = isUnauthenticated(meQuery.error);

  // When the portal route is left, drop the portal identity so the next visit
  // re-validates the cookie instead of trusting a stale in-memory principal.
  useEffect(() => {
    if (!onPortalRoute) {
      queryClient.removeQueries({ queryKey: PORTAL_ME_QUERY_KEY });
    }
  }, [onPortalRoute, queryClient]);

  const requestCode = useCallback(
    async ({ clinicSlug, contact }: { clinicSlug: string; contact: string }): Promise<RequestCodeResult> => {
      // Portal POSTs are CSRF-protected (double-submit); fetch the cookie first.
      await authCsrfRetrieve(PORTAL_REQUEST);
      const response = await portalAuthRequestCodeCreate(
        { clinic_slug: clinicSlug.trim(), contact: contact.trim() },
        PORTAL_REQUEST,
      );
      return {
        challengeId: response.challenge_id,
        devCode: response.code ?? null,
        demoCode: response.demo_code ?? null,
        demoNotice: response.demo_notice ?? null,
      };
    },
    [],
  );

  const verifyCode = useCallback(
    async ({ challengeId, code }: { challengeId: string; code: string }): Promise<void> => {
      await authCsrfRetrieve(PORTAL_REQUEST);
      await portalAuthVerifyCreate({ challenge_id: challengeId, code: code.trim() }, PORTAL_REQUEST);
      await queryClient.invalidateQueries();
    },
    [queryClient],
  );

  const logout = useCallback(async (): Promise<void> => {
    try {
      await portalAuthLogoutCreate(PORTAL_REQUEST);
    } finally {
      // Drop every portal-scoped cache and the app-shell caches.
      queryClient.removeQueries({ queryKey: ["portal"] });
      // ``removeQueries`` does not reset data already held by an active
      // observer, so explicitly reset the identity query. The refetch runs
      // against the just-revoked cookie and resolves to 401, which flips the
      // provider to unauthenticated instead of leaving a stale patient.
      await queryClient.resetQueries({ queryKey: PORTAL_ME_QUERY_KEY });
      await clearAppCaches();
    }
  }, [queryClient]);

  const refresh = useCallback(async (): Promise<void> => {
    await meQuery.refetch();
  }, [meQuery]);

  const value = useMemo<PortalAuthContextValue>(
    () => ({
      patient: data?.patient ?? null,
      clinicId: data?.clinic_id ?? null,
      isAuthenticated: Boolean(data) && !unauthorised,
      isLoading: meQuery.isPending && !unauthorised && onPortalRoute,
      error: unauthorised ? null : meQuery.error,
      requestCode,
      verifyCode,
      logout,
      refresh,
    }),
    [data, unauthorised, meQuery.isPending, meQuery.error, onPortalRoute, requestCode, verifyCode, logout, refresh],
  );

  return <PortalAuthContext.Provider value={value}>{children}</PortalAuthContext.Provider>;
}

export function usePortalAuth(): PortalAuthContextValue {
  const context = useContext(PortalAuthContext);
  if (context === null) {
    throw new Error("usePortalAuth must be used inside <PortalAuthProvider>.");
  }
  return context;
}
