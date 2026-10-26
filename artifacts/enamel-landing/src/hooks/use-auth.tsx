import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  authCsrfRetrieve,
  authLoginCreate,
  authLogoutCreate,
  authMeRetrieve,
  authRegisterCreate,
  getAuthMeRetrieveQueryKey,
  type Clinic,
  type MembershipWithClinic,
  type Register,
  type User,
} from "@workspace/api-client-react";
import { clearActiveClinicId, getActiveClinicId, setActiveClinicId } from "@/lib/api";

export interface LoginInput {
  email: string;
  password: string;
  otp?: string;
}

export interface LoginOutcome {
  /** The account has MFA enabled and the request must be retried with `otp`. */
  mfaRequired: boolean;
}

interface AuthContextValue {
  user: User | null;
  memberships: MembershipWithClinic[];
  currentClinic: Clinic | null;
  activeClinicId: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: unknown;
  login: (input: LoginInput) => Promise<LoginOutcome>;
  register: (input: Register) => Promise<void>;
  logout: () => Promise<void>;
  selectClinic: (clinicId: string) => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function chooseClinicId(memberships: MembershipWithClinic[]): string | null {
  if (memberships.length === 0) return null;
  const stored = getActiveClinicId();
  if (stored && memberships.some((m) => m.clinic_id === stored)) return stored;
  return memberships[0].clinic_id;
}

async function fetchMe() {
  try {
    return await authMeRetrieve();
  } catch (error) {
    // A stale clinic header (member removed, clinic archived) is indistinguishable
    // from a foreign clinic. Drop it and retry once so the user is not locked out.
    if (
      error instanceof ApiError &&
      (error.code === "not_found" || error.code === "permission_denied") &&
      getActiveClinicId() !== null
    ) {
      clearActiveClinicId();
      return await authMeRetrieve();
    }
    throw error;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [activeClinicId, setActiveClinicIdState] = useState<string | null>(() => getActiveClinicId());

  const meQuery = useQuery({
    queryKey: getAuthMeRetrieveQueryKey(),
    queryFn: fetchMe,
    retry: false,
    staleTime: 30_000,
  });

  const data = meQuery.data;
  const unauthorised =
    meQuery.error instanceof ApiError &&
    (meQuery.error.status === 401 ||
      meQuery.error.code === "unauthenticated" ||
      meQuery.error.code === "session_expired");

  // With more than one active membership the server cannot infer the clinic and
  // returns `current_clinic: null`. Pin the stored (or first) clinic and reload.
  useEffect(() => {
    if (!data || data.current_clinic !== null || data.memberships.length === 0) return;
    const clinicId = chooseClinicId(data.memberships);
    if (clinicId === null) return;
    setActiveClinicId(clinicId);
    setActiveClinicIdState(clinicId);
    void queryClient.invalidateQueries();
  }, [data, queryClient]);

  const login = useCallback(
    async ({ email, password, otp }: LoginInput): Promise<LoginOutcome> => {
      // CSRF is enforced on login; fetch the cookie first (double-submit).
      await authCsrfRetrieve();
      try {
        const response = await authLoginCreate({
          email,
          password,
          ...(otp && otp.trim() !== "" ? { otp: otp.trim() } : {}),
        });
        const clinicId = chooseClinicId(response.memberships);
        setActiveClinicId(clinicId);
        setActiveClinicIdState(clinicId);
        await queryClient.invalidateQueries();
        return { mfaRequired: false };
      } catch (error) {
        if (error instanceof ApiError && error.code === "mfa_required") {
          return { mfaRequired: true };
        }
        throw error;
      }
    },
    [queryClient],
  );

  const register = useCallback(
    async (input: Register): Promise<void> => {
      await authCsrfRetrieve();
      const response = await authRegisterCreate(input);
      setActiveClinicId(response.clinic.id);
      setActiveClinicIdState(response.clinic.id);
      await queryClient.invalidateQueries();
    },
    [queryClient],
  );

  const logout = useCallback(async (): Promise<void> => {
    try {
      await authLogoutCreate();
    } finally {
      clearActiveClinicId();
      setActiveClinicIdState(null);
      // Drop every cached PHI page so back-navigation cannot reveal it.
      queryClient.clear();
      await queryClient.invalidateQueries();
    }
  }, [queryClient]);

  const selectClinic = useCallback(
    (clinicId: string): void => {
      setActiveClinicId(clinicId);
      setActiveClinicIdState(clinicId);
      void queryClient.invalidateQueries();
    },
    [queryClient],
  );

  const refresh = useCallback(async (): Promise<void> => {
    await meQuery.refetch();
  }, [meQuery]);

  const value = useMemo<AuthContextValue>(
    () => ({
      user: data?.user ?? null,
      memberships: data?.memberships ?? [],
      currentClinic: data?.current_clinic ?? null,
      activeClinicId,
      isAuthenticated: Boolean(data) && !unauthorised,
      isLoading: meQuery.isPending && !unauthorised,
      error: unauthorised ? null : meQuery.error,
      login,
      register,
      logout,
      selectClinic,
      refresh,
    }),
    [data, activeClinicId, unauthorised, meQuery.isPending, meQuery.error, login, register, logout, selectClinic, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth must be used inside <AuthProvider>.");
  }
  return context;
}
