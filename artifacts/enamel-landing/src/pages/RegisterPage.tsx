import { useState, type FormEvent } from "react";
import { Link, Redirect, useLocation } from "wouter";
import { Building2, Lock, Mail, User } from "lucide-react";
import { useAuth } from "@/hooks/use-auth";
import { apiErrorMessage, apiErrorFieldErrors, isApiError } from "@/lib/api";

const INPUT_CLASS =
  "w-full bg-white/70 border border-border rounded-xl pl-11 pr-4 py-3 text-sm font-medium text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all";

function slugify(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9_]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export default function RegisterPage() {
  const { register, isAuthenticated } = useAuth();
  const [, navigate] = useLocation();
  const [clinicName, setClinicName] = useState("");
  const [clinicSlug, setClinicSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});

  if (isAuthenticated) return <Redirect to="/app" />;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setFormError(null);
    setFieldErrors({});
    try {
      await register({
        clinic_name: clinicName,
        clinic_slug: clinicSlug,
        full_name: fullName,
        email,
        password,
      });
      navigate("/app");
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

  function fieldError(name: string) {
    const messages = fieldErrors[name];
    if (!messages || messages.length === 0) return null;
    return <p className="text-xs text-destructive mt-1">{messages.join(" ")}</p>;
  }

  return (
    <div
      className="min-h-screen flex items-center justify-center p-4"
      style={{ background: "linear-gradient(135deg, #f3f0ff 0%, #f9f6f5 40%, #f0f4ff 70%, #fdf4fb 100%)" }}
    >
      <div className="w-full max-w-lg glass-card rounded-[2rem] p-10 shadow-xl">
        <div className="mb-8">
          <span className="text-2xl font-black uppercase tracking-tighter text-foreground font-display">ENAMEL</span>
          <h1 className="text-3xl font-extrabold font-display text-foreground mt-4">Create your clinic</h1>
          <p className="text-sm text-muted-foreground mt-2">
            Register a clinic and become its first owner.
          </p>
        </div>

        {formError ? (
          <div role="alert" className="mb-5 rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive font-medium">
            {formError}
          </div>
        ) : null}

        <form onSubmit={onSubmit} className="space-y-4" noValidate>
          <div>
            <label htmlFor="clinic-name" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
              Clinic name
            </label>
            <div className="relative">
              <Building2 size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input
                id="clinic-name"
                data-testid="register-clinic-name"
                required
                value={clinicName}
                onChange={(e) => {
                  setClinicName(e.target.value);
                  if (!slugTouched) setClinicSlug(slugify(e.target.value));
                }}
                className={INPUT_CLASS}
                placeholder="Bright Smile Dental"
              />
            </div>
            {fieldError("clinic_name")}
          </div>

          <div>
            <label htmlFor="clinic-slug" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
              Clinic slug
            </label>
            <div className="relative">
              <span className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground text-sm">/book/</span>
              <input
                id="clinic-slug"
                data-testid="register-clinic-slug"
                required
                pattern="[-a-zA-Z0-9_]+"
                value={clinicSlug}
                onChange={(e) => {
                  setSlugTouched(true);
                  setClinicSlug(e.target.value);
                }}
                className={`${INPUT_CLASS} pl-[3.75rem]`}
                placeholder="bright-smile"
              />
            </div>
            {fieldError("clinic_slug")}
          </div>

          <div>
            <label htmlFor="full-name" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
              Your full name
            </label>
            <div className="relative">
              <User size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input
                id="full-name"
                data-testid="register-full-name"
                required
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                className={INPUT_CLASS}
                placeholder="Dr Jane Doe"
              />
            </div>
            {fieldError("full_name")}
          </div>

          <div>
            <label htmlFor="reg-email" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
              Email
            </label>
            <div className="relative">
              <Mail size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input
                id="reg-email"
                data-testid="register-email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className={INPUT_CLASS}
                placeholder="you@clinic.com"
              />
            </div>
            {fieldError("email")}
          </div>

          <div>
            <label htmlFor="reg-password" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
              Password
            </label>
            <div className="relative">
              <Lock size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input
                id="reg-password"
                data-testid="register-password"
                type="password"
                autoComplete="new-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={INPUT_CLASS}
                placeholder="••••••••"
              />
            </div>
            {fieldError("password")}
          </div>

          <button
            type="submit"
            data-testid="register-submit"
            disabled={submitting}
            className="w-full bg-gradient-to-br from-primary to-primary-dim text-white py-3.5 rounded-xl font-bold text-sm shadow-lg shadow-primary/20 hover:opacity-90 active:scale-[0.99] transition-all disabled:opacity-60"
          >
            {submitting ? "Creating clinic…" : "Create clinic"}
          </button>
        </form>

        <p className="text-xs text-muted-foreground mt-6 text-center">
          Already have an account?{" "}
          <Link href="/login" className="text-primary font-bold hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
