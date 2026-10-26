import { useState, type FormEvent } from "react";
import { Link, Redirect, useLocation } from "wouter";
import { Lock, Mail, ShieldCheck } from "lucide-react";
import { useAuth } from "@/hooks/use-auth";
import { apiErrorMessage, apiErrorFieldErrors, isApiError } from "@/lib/api";

const INPUT_CLASS =
  "w-full bg-white/70 border border-border rounded-xl pl-11 pr-4 py-3 text-sm font-medium text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all";

function readNext(): string {
  if (typeof window === "undefined") return "/app";
  const next = new URLSearchParams(window.location.search).get("next");
  if (next && next.startsWith("/") && !next.startsWith("//")) return next;
  return "/app";
}

export default function LoginPage() {
  const { login, isAuthenticated } = useAuth();
  const [, navigate] = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [mfaRequired, setMfaRequired] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});

  const next = readNext();

  if (isAuthenticated) return <Redirect to={next} />;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setFormError(null);
    setFieldErrors({});
    try {
      const outcome = await login({ email, password, otp: mfaRequired ? otp : undefined });
      if (outcome.mfaRequired) {
        setMfaRequired(true);
        setFormError("This account uses multi-factor authentication. Enter the 6-digit code.");
        return;
      }
      navigate(next);
    } catch (error) {
      setFieldErrors(apiErrorFieldErrors(error));
      if (isApiError(error) && error.code === "throttled") {
        setFormError("Too many attempts. Wait a moment and try again.");
      } else {
        setFormError(apiErrorMessage(error));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="min-h-screen flex items-center justify-center p-4"
      style={{ background: "linear-gradient(135deg, #f3f0ff 0%, #f9f6f5 40%, #f0f4ff 70%, #fdf4fb 100%)" }}
    >
      <div className="w-full max-w-md glass-card rounded-[2rem] p-10 shadow-xl">
        <div className="mb-8">
          <span className="text-2xl font-black uppercase tracking-tighter text-foreground font-display">ENAMEL</span>
          <h1 className="text-3xl font-extrabold font-display text-foreground mt-4">Sign in</h1>
          <p className="text-sm text-muted-foreground mt-2">Access your clinical workspace.</p>
        </div>

        {formError ? (
          <div role="alert" className="mb-5 rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive font-medium">
            {formError}
          </div>
        ) : null}

        <form onSubmit={onSubmit} className="space-y-4" noValidate>
          <div>
            <label htmlFor="email" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
              Email
            </label>
            <div className="relative">
              <Mail size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input
                id="email"
                data-testid="login-email"
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

          <div>
            <label htmlFor="password" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
              Password
            </label>
            <div className="relative">
              <Lock size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input
                id="password"
                data-testid="login-password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={INPUT_CLASS}
                placeholder="••••••••"
              />
            </div>
            {fieldErrors.password ? (
              <p className="text-xs text-destructive mt-1">{fieldErrors.password.join(" ")}</p>
            ) : null}
          </div>

          {mfaRequired ? (
            <div>
              <label htmlFor="otp" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                One-time code
              </label>
              <div className="relative">
                <ShieldCheck size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <input
                  id="otp"
                  data-testid="login-otp"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={16}
                  value={otp}
                  onChange={(e) => setOtp(e.target.value)}
                  className={INPUT_CLASS}
                  placeholder="123456"
                />
              </div>
              {fieldErrors.otp ? (
                <p className="text-xs text-destructive mt-1">{fieldErrors.otp.join(" ")}</p>
              ) : null}
            </div>
          ) : null}

          {fieldErrors.non_field_errors ? (
            <p className="text-xs text-destructive">{fieldErrors.non_field_errors.join(" ")}</p>
          ) : null}

          <button
            type="submit"
            data-testid="login-submit"
            disabled={submitting}
            className="w-full bg-gradient-to-br from-primary to-primary-dim text-white py-3.5 rounded-xl font-bold text-sm shadow-lg shadow-primary/20 hover:opacity-90 active:scale-[0.99] transition-all disabled:opacity-60"
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="text-xs text-muted-foreground mt-6 text-center">
          <Link href="/reset-password" data-testid="login-forgot-password" className="text-primary font-bold hover:underline">
            Forgot password?
          </Link>
        </p>

        <p className="text-xs text-muted-foreground mt-3 text-center">
          New clinic?{" "}
          <Link href="/register" className="text-primary font-bold hover:underline">
            Create an account
          </Link>
        </p>

        <p className="text-xs text-muted-foreground mt-3 text-center">
          New here?{" "}
          <Link href="/guide" data-testid="login-guide-link" className="text-primary font-bold hover:underline">
            Read the user guide
          </Link>
        </p>
      </div>
    </div>
  );
}
