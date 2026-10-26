import { useState, type FormEvent } from "react";
import { Link, Redirect, useLocation } from "wouter";
import { KeyRound, Mail, ShieldCheck } from "lucide-react";
import { usePortalAuth } from "@/hooks/use-portal-auth";
import { apiErrorFieldErrors, apiErrorMessage } from "@/lib/api";

const INPUT_CLASS =
  "w-full bg-white/70 border border-border rounded-xl px-4 py-3.5 text-base font-medium text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all min-h-[44px]";

function readNext(): string {
  if (typeof window === "undefined") return "/portal";
  const next = new URLSearchParams(window.location.search).get("next");
  if (next && next.startsWith("/portal")) return next;
  return "/portal";
}

export default function PortalLoginPage() {
  const { requestCode, verifyCode, isAuthenticated } = usePortalAuth();
  const [, navigate] = useLocation();

  const [clinicSlug, setClinicSlug] = useState("");
  const [contact, setContact] = useState("");
  const [code, setCode] = useState("");
  const [challengeId, setChallengeId] = useState<string | null>(null);
  const [devCode, setDevCode] = useState<string | null>(null);
  const [demoCode, setDemoCode] = useState<string | null>(null);
  const [demoNotice, setDemoNotice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});

  const next = readNext();

  if (isAuthenticated) return <Redirect to={next} />;

  async function onRequest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setFormError(null);
    setFieldErrors({});
    try {
      const result = await requestCode({ clinicSlug, contact });
      setChallengeId(result.challengeId);
      setDevCode(result.devCode);
      setDemoCode(result.demoCode);
      setDemoNotice(result.demoNotice);
      if (result.devCode) setCode(result.devCode);
      if (result.demoCode) setCode(result.demoCode);
      setFormError(null);
    } catch (error) {
      setFieldErrors(apiErrorFieldErrors(error));
      setFormError(apiErrorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  async function onVerify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!challengeId) return;
    setSubmitting(true);
    setFormError(null);
    setFieldErrors({});
    try {
      await verifyCode({ challengeId, code });
      navigate(next);
    } catch (error) {
      setFieldErrors(apiErrorFieldErrors(error));
      setFormError(apiErrorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  function backToContact() {
    setChallengeId(null);
    setCode("");
    setDevCode(null);
    setDemoCode(null);
    setDemoNotice(null);
    setFormError(null);
    setFieldErrors({});
  }

  return (
    <div
      className="min-h-screen flex items-center justify-center p-4"
      style={{ background: "linear-gradient(135deg, #f3f0ff 0%, #f9f6f5 40%, #f0f4ff 70%, #fdf4fb 100%)" }}
    >
      <div className="w-full max-w-md glass-card rounded-[2rem] p-8 sm:p-10 shadow-xl">
        <div className="mb-8">
          <span className="text-2xl font-black uppercase tracking-tighter text-foreground font-display">ENAMEL</span>
          <h1 className="text-3xl font-extrabold font-display text-foreground mt-4">Patient Portal</h1>
          <p className="text-sm text-muted-foreground mt-2">
            {challengeId
              ? "Enter the code. There is no portal password: the portal signs you in with a one-time code only."
              : "Sign in with a one-time code in three steps: enter the clinic your clinic gave you, enter the email or phone they have on file, then request a code. In production the code is delivered to that contact; nothing is echoed."}
          </p>
          {challengeId === null ? (
            <p className="text-xs text-muted-foreground mt-3 rounded-xl border border-border/70 bg-muted/40 px-4 py-3 leading-relaxed">
              Exploring the demo? Use clinic <code className="font-mono font-bold">enamel-central</code> and the demo
              contact <code className="font-mono font-bold">patient@enamel.demo</code> (or{" "}
              <code className="font-mono font-bold">+919800000001</code>). The demo server runs with DEMO_MODE, so the
              code is shown on screen after you request it. See the user guide at{" "}
              <Link href="/guide" className="text-primary font-bold hover:underline">
                /guide
              </Link>
              .
            </p>
          ) : null}
        </div>

        {formError ? (
          <div
            role="alert"
            className="mb-5 rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive font-medium"
          >
            {formError}
          </div>
        ) : null}

        {challengeId === null ? (
          <form onSubmit={onRequest} className="space-y-4" noValidate>
            <div>
              <label htmlFor="portal-clinic" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                Clinic
              </label>
              <input
                id="portal-clinic"
                data-testid="portal-clinic"
                required
                value={clinicSlug}
                onChange={(e) => setClinicSlug(e.target.value)}
                className={INPUT_CLASS}
                placeholder="clinic-slug"
                autoComplete="off"
              />
              {fieldErrors.clinic_slug ? (
                <p className="text-xs text-destructive mt-1">{fieldErrors.clinic_slug.join(" ")}</p>
              ) : null}
            </div>
            <div>
              <label htmlFor="portal-contact" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                Email or mobile
              </label>
              <div className="relative">
                <Mail size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <input
                  id="portal-contact"
                  data-testid="portal-contact"
                  required
                  value={contact}
                  onChange={(e) => setContact(e.target.value)}
                  className={`${INPUT_CLASS} pl-11`}
                  placeholder="you@example.com"
                  autoComplete="email"
                />
              </div>
              {fieldErrors.contact ? (
                <p className="text-xs text-destructive mt-1">{fieldErrors.contact.join(" ")}</p>
              ) : null}
            </div>
            <button
              type="submit"
              data-testid="portal-request-code"
              disabled={submitting}
              className="w-full bg-gradient-to-br from-primary to-primary-dim text-white py-3.5 rounded-xl font-bold text-sm shadow-lg shadow-primary/20 hover:opacity-90 active:scale-[0.99] transition-all disabled:opacity-60 min-h-[44px]"
            >
              {submitting ? "Sending…" : "Send code"}
            </button>
          </form>
        ) : (
          <form onSubmit={onVerify} className="space-y-4" noValidate>
            {demoCode ? (
              <div
                data-testid="portal-demo-code"
                className="rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 text-xs text-sky-900"
              >
                <p className="font-bold uppercase tracking-wider">Demo mode</p>
                <p className="mt-1 leading-relaxed">{demoNotice ?? "The server runs with DEMO_MODE, so your one-time code is shown here."}</p>
                <p className="mt-2 font-mono text-lg font-bold tracking-[0.3em]">{demoCode}</p>
              </div>
            ) : null}
            {devCode ? (
              <div
                data-testid="portal-dev-code"
                className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800"
              >
                <p className="font-bold">Development mode: code echoed by the API</p>
                <code className="font-mono text-base">{devCode}</code>
              </div>
            ) : null}
            <div>
              <label htmlFor="portal-code" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                One-time code
              </label>
              <div className="relative">
                <KeyRound size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <input
                  id="portal-code"
                  data-testid="portal-code"
                  required
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={12}
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  className={`${INPUT_CLASS} pl-11 tracking-[0.4em]`}
                  placeholder="123456"
                />
              </div>
              {fieldErrors.code ? <p className="text-xs text-destructive mt-1">{fieldErrors.code.join(" ")}</p> : null}
            </div>
            <button
              type="submit"
              data-testid="portal-verify-code"
              disabled={submitting}
              className="w-full bg-gradient-to-br from-primary to-primary-dim text-white py-3.5 rounded-xl font-bold text-sm shadow-lg shadow-primary/20 hover:opacity-90 active:scale-[0.99] transition-all disabled:opacity-60 min-h-[44px]"
            >
              {submitting ? "Verifying…" : "Verify code"}
            </button>
            <button
              type="button"
              onClick={backToContact}
              className="w-full text-xs font-bold text-muted-foreground hover:text-foreground transition-colors py-2 min-h-[44px]"
            >
              Use a different contact
            </button>
          </form>
        )}

        <p className="text-[11px] text-muted-foreground mt-6 flex items-center gap-2">
          <ShieldCheck size={12} />
          The portal uses a separate patient session; staff sign-in is not required.
        </p>
      </div>
    </div>
  );
}
