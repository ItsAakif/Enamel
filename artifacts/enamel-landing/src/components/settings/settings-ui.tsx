import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import type { LucideIcon } from "lucide-react";
import { AlertTriangle, Loader2 } from "lucide-react";

/**
 * Small presentational primitives shared by every Settings section.
 *
 * They reuse the existing visual tokens (rounded-[2rem] cards, pill buttons,
 * the primary gradient) so the Settings rewrite keeps the original identity.
 */

export const INPUT_CLASS =
  "w-full border border-border rounded-xl px-4 py-3 text-sm font-medium bg-white/70 text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all disabled:opacity-60";

export const LABEL_CLASS =
  "text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2";

const PRIMARY_BUTTON =
  "inline-flex items-center justify-center gap-2 bg-gradient-to-br from-primary to-primary-dim text-white px-6 py-3 rounded-full font-bold text-xs shadow-lg shadow-primary/20 hover:opacity-90 active:scale-95 transition-all disabled:opacity-60 disabled:cursor-not-allowed";

const SECONDARY_BUTTON =
  "inline-flex items-center justify-center gap-2 border border-border px-6 py-3 rounded-full font-bold text-xs text-foreground hover:bg-muted transition-colors disabled:opacity-60 disabled:cursor-not-allowed";

const DANGER_BUTTON =
  "inline-flex items-center justify-center gap-2 border border-destructive/30 text-destructive px-5 py-2.5 rounded-full font-bold text-xs hover:bg-destructive/10 transition-colors disabled:opacity-60 disabled:cursor-not-allowed";

export function PrimaryButton({
  busy = false,
  children,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { busy?: boolean }) {
  return (
    <button type="button" {...props} className={`${PRIMARY_BUTTON} ${className}`} disabled={props.disabled || busy}>
      {busy ? <Loader2 size={14} className="animate-spin" /> : null}
      {children}
    </button>
  );
}

export function SecondaryButton({
  busy = false,
  children,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { busy?: boolean }) {
  return (
    <button type="button" {...props} className={`${SECONDARY_BUTTON} ${className}`} disabled={props.disabled || busy}>
      {busy ? <Loader2 size={14} className="animate-spin" /> : null}
      {children}
    </button>
  );
}

export function DangerButton({
  busy = false,
  children,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { busy?: boolean }) {
  return (
    <button type="button" {...props} className={`${DANGER_BUTTON} ${className}`} disabled={props.disabled || busy}>
      {busy ? <Loader2 size={14} className="animate-spin" /> : null}
      {children}
    </button>
  );
}

export function TextInput({ className = "", ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${INPUT_CLASS} ${className}`} />;
}

export function SelectInput({ className = "", children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...props} className={`${INPUT_CLASS} ${className}`}>
      {children}
    </select>
  );
}

export function Field({
  label,
  htmlFor,
  error,
  hint,
  children,
}: {
  label: string;
  htmlFor?: string;
  error?: string | string[] | null;
  hint?: ReactNode;
  children: ReactNode;
}) {
  const message = Array.isArray(error) ? error.join(" ") : error;
  return (
    <div>
      <label htmlFor={htmlFor} className={LABEL_CLASS}>
        {label}
      </label>
      {children}
      {message ? <p className="text-xs text-destructive mt-1">{message}</p> : null}
      {hint ? <p className="text-xs text-muted-foreground mt-1">{hint}</p> : null}
    </div>
  );
}

export function ErrorBanner({ children }: { children: ReactNode }) {
  return (
    <div
      role="alert"
      className="rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive font-medium flex items-start gap-2"
    >
      <AlertTriangle size={16} className="shrink-0 mt-0.5" />
      <span>{children}</span>
    </div>
  );
}

export function SectionCard({
  icon: Icon,
  title,
  description,
  actions,
  children,
  className = "",
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`bg-card border border-border rounded-[2rem] p-8 shadow-sm ${className}`}>
      <div className="flex items-start justify-between gap-4 mb-6">
        <div className="flex items-center gap-3">
          <div className="p-2.5 bg-primary/10 text-primary rounded-xl">
            <Icon size={20} />
          </div>
          <div>
            <h3 className="text-lg font-bold font-display">{title}</h3>
            <p className="text-xs text-muted-foreground">{description}</p>
          </div>
        </div>
        {actions ? <div className="flex items-center gap-2 shrink-0">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

const PILL_TONES: Record<string, string> = {
  success: "bg-emerald-100 text-emerald-700",
  warning: "bg-amber-100 text-amber-700",
  danger: "bg-rose-100 text-rose-700",
  neutral: "bg-muted text-muted-foreground",
  primary: "bg-primary/10 text-primary",
};

export function Pill({
  children,
  tone = "neutral",
  className = "",
}: {
  children: ReactNode;
  tone?: keyof typeof PILL_TONES;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center text-[10px] font-bold px-3 py-1.5 rounded-full uppercase tracking-wide ${PILL_TONES[tone]} ${className}`}
    >
      {children}
    </span>
  );
}
