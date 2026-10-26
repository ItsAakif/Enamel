import { useState, type FormEvent } from "react";
import { Link } from "wouter";
import { KeyRound, Mail, ShieldCheck } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import {
  authCsrfRetrieve,
  authPasswordConfirmCreate,
  authPasswordResetCreate,
} from "@workspace/api-client-react";
import { apiErrorCode, apiErrorFieldErrors, apiErrorMessage } from "@/lib/api";

/**
 * Password reset (`/reset-password`).
 *
 * Two phases, both backed by the real API:
 *
 * 1. **Request** - `POST /api/v1/auth/password/reset/` with `{email}`. The
 *    endpoint answers 202 with the same body for every address, so the screen
 *    never reveals whether an account exists.
 * 2. **Confirm** - `POST /api/v1/auth/password/confirm/` with
 *    `{token, new_password}`. The raw token arrives in the query string of the
 *    emailed link (`{FRONTEND_BASE_URL}/reset-password?token=...`); it is
 *    single use and expires after 30 minutes, so an invalid or expired token
 *    is an expected outcome with its own error state.
 *
 * The password rules are the server's: the client only checks that the two
 * entries match, and every other rule is reported by the API in
 * `field_errors.new_password`.
 */

const INPUT_CLASS =
  "w-full bg-white/70 border border-border rounded-xl pl-11 pr-4 py-3 text-sm font-medium text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all";

type Phase = "request" | "confirm" | "done";

function readToken(): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get("token") ?? "";
}

export default function ResetPasswordPage() {
  const [token, setToken] = useState(readToken);
  const [phase, setPhase] = useState<Phase>(() => (readToken() === "" ? "request" : "confirm"));
  const [email, setEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [requestSent, setRequestSent] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});

  const requestMutation = useMutation({
    mutationFn: async (address: string) => {
      await authCsrfRetrieve();
      return authPasswordResetCreate({ email: address });
    },
    onSuccess: () => {
      setRequestSent(true);
      setFormError(null);
      setFieldErrors({});
    },
    onError: (error) => {
      setRequestSent(false);
      setFieldErrors(apiErrorFieldErrors(error));
      setFormError(apiErrorMessage(error));
    },
  });

  const confirmMutation = useMutation({
    mutationFn: async (payload: { token: string; newPassword: string }) => {
      await authCsrfRetrieve();
      return authPasswordConfirmCreate({
        token: payload.token,
        new_password: payload.newPassword,
      });
    },
    onSuccess: () => {
      setFormError(null);
      setFieldErrors({});
      setPhase("done");
    },
    onError: (error) => {
      setFieldErrors(apiErrorFieldErrors(error));
      if (apiErrorCode(error) === "invalid_token") {
        setFormError(
          "This reset link is invalid or has expired. Request a new link to continue.",
        );
        return;
      }
      setFormError(apiErrorMessage(error));
    },
  });

  function onRequest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    setFieldErrors({});
    requestMutation.mutate(email.trim());
  }

  function onConfirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    setFieldErrors({});
    if (newPassword !== confirmPassword) {
      setFieldErrors({ confirm_password: ["the two passwords do not match"] });
      return;
    }
    confirmMutation.mutate({ token: token.trim(), newPassword });
  }

  return (
    <div
      className="min-h-screen flex items-center justify-center p-4"
      style={{ background: "linear-gradient(135deg, #f3f0ff 0%, #f9f6f5 40%, #f0f4ff 70%, #fdf4fb 100%)" }}
    >
      <div className="w-full max-w-md glass-card rounded-[2rem] p-10 shadow-xl">
        <div className="mb-8">
          <span className="text-2xl font-black uppercase tracking-tighter text-foreground font-display">
            ENAMEL
          </span>
          <h1 className="text-3xl font-extrabold font-display text-foreground mt-4">
            {phase === "done" ? "Password updated" : "Reset your password"}
          </h1>
          <p className="text-sm text-muted-foreground mt-2">
            {phase === "request"
              ? "Enter the email address on your account and we will send a reset link."
              : phase === "confirm"
                ? "Choose a new password for your account."
                : "Your password has been changed. Sign in with the new one."}
          </p>
        </div>

        {formError ? (
          <div
            role="alert"
            data-testid="reset-password-error"
            className="mb-5 rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive font-medium"
          >
            {formError}
          </div>
        ) : null}

        {phase === "done" ? (
          <Link
            href="/login"
            data-testid="reset-password-done"
            className="w-full block text-center bg-gradient-to-br from-primary to-primary-dim text-white py-3.5 rounded-xl font-bold text-sm shadow-lg shadow-primary/20 hover:opacity-90 active:scale-[0.99] transition-all"
          >
            Back to sign in
          </Link>
        ) : null}

        {phase === "request" ? (
          <form onSubmit={onRequest} className="space-y-4" noValidate>
            {requestSent ? (
              <div
                role="status"
                data-testid="reset-password-requested"
                className="mb-5 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800 font-medium"
              >
                If that address belongs to an account, a reset link is on its way. The link
                expires in 30 minutes and can only be used once.
              </div>
            ) : null}

            <div>
              <label
                htmlFor="reset-email"
                className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2"
              >
                Email
              </label>
              <div className="relative">
                <Mail size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <input
                  id="reset-email"
                  data-testid="reset-password-email"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className={INPUT_CLASS}
                  placeholder="you@clinic.com"
                />
              </div>
              {fieldErrors.email ? (
                <p className="text-xs text-destructive mt-1">{fieldErrors.email.join(" ")}</p>
              ) : null}
            </div>

            <button
              type="submit"
              data-testid="reset-password-request-submit"
              disabled={requestMutation.isPending}
              className="w-full bg-gradient-to-br from-primary to-primary-dim text-white py-3.5 rounded-xl font-bold text-sm shadow-lg shadow-primary/20 hover:opacity-90 active:scale-[0.99] transition-all disabled:opacity-60"
            >
              {requestMutation.isPending ? "Sending…" : "Send reset link"}
            </button>
          </form>
        ) : null}

        {phase === "confirm" ? (
          <form onSubmit={onConfirm} className="space-y-4" noValidate>
            <div>
              <label
                htmlFor="reset-token"
                className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2"
              >
                Reset token
              </label>
              <div className="relative">
                <KeyRound size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <input
                  id="reset-token"
                  data-testid="reset-password-token"
                  required
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  className={INPUT_CLASS}
                  placeholder="token from the reset link"
                />
              </div>
            </div>

            <div>
              <label
                htmlFor="reset-new-password"
                className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2"
              >
                New password
              </label>
              <div className="relative">
                <ShieldCheck size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <input
                  id="reset-new-password"
                  data-testid="reset-password-new"
                  type="password"
                  autoComplete="new-password"
                  required
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className={INPUT_CLASS}
                  placeholder="••••••••"
                />
              </div>
              {fieldErrors.new_password ? (
                <p className="text-xs text-destructive mt-1">
                  {fieldErrors.new_password.join(" ")}
                </p>
              ) : null}
            </div>

            <div>
              <label
                htmlFor="reset-confirm-password"
                className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2"
              >
                Confirm new password
              </label>
              <div className="relative">
                <ShieldCheck size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <input
                  id="reset-confirm-password"
                  data-testid="reset-password-confirm"
                  type="password"
                  autoComplete="new-password"
                  required
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className={INPUT_CLASS}
                  placeholder="••••••••"
                />
              </div>
              {fieldErrors.confirm_password ? (
                <p className="text-xs text-destructive mt-1">
                  {fieldErrors.confirm_password.join(" ")}
                </p>
              ) : null}
            </div>

            <button
              type="submit"
              data-testid="reset-password-confirm-submit"
              disabled={confirmMutation.isPending}
              className="w-full bg-gradient-to-br from-primary to-primary-dim text-white py-3.5 rounded-xl font-bold text-sm shadow-lg shadow-primary/20 hover:opacity-90 active:scale-[0.99] transition-all disabled:opacity-60"
            >
              {confirmMutation.isPending ? "Saving…" : "Set new password"}
            </button>

            <button
              type="button"
              data-testid="reset-password-back-to-request"
              onClick={() => {
                setPhase("request");
                setFormError(null);
                setFieldErrors({});
              }}
              className="w-full text-xs font-bold text-muted-foreground hover:text-foreground transition-colors py-2"
            >
              Request a new link instead
            </button>
          </form>
        ) : null}

        {phase !== "done" ? (
          <p className="text-xs text-muted-foreground mt-6 text-center">
            <Link href="/login" className="text-primary font-bold hover:underline">
              Back to sign in
            </Link>
          </p>
        ) : null}
      </div>
    </div>
  );
}
