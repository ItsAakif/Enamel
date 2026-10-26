import type { ReactNode } from "react";
import { Link, Redirect, useLocation } from "wouter";
import {
  CalendarDays,
  ClipboardList,
  FileText,
  HeartPulse,
  Home,
  LogOut,
  Receipt,
  ShieldAlert,
  WifiOff,
} from "lucide-react";
import { useAuth } from "@/hooks/use-auth";
import { usePortalAuth } from "@/hooks/use-portal-auth";
import { useOnlineStatus } from "@/lib/pwa";
import { initials } from "@/lib/format";

const PORTAL_NAV = [
  { label: "Home", path: "/portal", icon: Home, exact: true },
  { label: "Appointments", path: "/portal/appointments", icon: CalendarDays, exact: false },
  { label: "Records", path: "/portal/records", icon: HeartPulse, exact: false },
  { label: "Plans", path: "/portal/plans", icon: ClipboardList, exact: false },
  { label: "Invoices", path: "/portal/invoices", icon: Receipt, exact: false },
  { label: "Documents", path: "/portal/documents", icon: FileText, exact: false },
];

function isActivePath(location: string, path: string, exact: boolean): boolean {
  if (exact) return location === path;
  return location === path || location.startsWith(`${path}/`);
}

function PortalLoader() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background" role="status" aria-busy="true">
      <span className="text-xs font-bold uppercase tracking-widest text-muted-foreground">Loading portal…</span>
    </div>
  );
}

export default function PortalLayout({ children }: { children: ReactNode }) {
  const [location, navigate] = useLocation();
  const { isAuthenticated, isLoading, patient, logout } = usePortalAuth();
  const { isAuthenticated: staffSignedIn } = useAuth();
  const online = useOnlineStatus();

  if (isLoading) return <PortalLoader />;

  if (!isAuthenticated) {
    return <Redirect to={`/portal/login?next=${encodeURIComponent(location)}`} />;
  }

  const displayName = patient
    ? patient.preferred_name || `${patient.first_name} ${patient.last_name}`.trim()
    : "Patient";

  return (
    <div
      className="min-h-screen flex flex-col"
      style={{ background: "linear-gradient(135deg, #f3f0ff 0%, #f9f6f5 40%, #f0f4ff 70%, #fdf4fb 100%)" }}
    >
      {!online ? (
        <div
          role="alert"
          className="w-full bg-amber-100 text-amber-900 px-4 py-3 text-sm font-medium flex items-center gap-2"
          data-testid="offline-banner"
        >
          <WifiOff size={16} />
          You are offline. Cached screens can be viewed, but live actions are disabled until the connection returns.
        </div>
      ) : null}

      <header className="sticky top-0 z-40 glass-header shadow-[0_2px_20px_rgba(70,71,211,0.04)]">
        <div className="max-w-5xl mx-auto px-4 py-4 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <span className="text-xl font-black uppercase tracking-tighter text-foreground font-display">ENAMEL</span>
            <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground hidden sm:inline">
              Patient Portal
            </span>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <div className="w-9 h-9 rounded-full bg-gradient-to-br from-primary to-primary-dim flex items-center justify-center text-white text-xs font-black">
                {initials(displayName)}
              </div>
              <span className="text-xs font-bold text-foreground hidden sm:inline max-w-[140px] truncate">
                {displayName}
              </span>
            </div>
            <button
              type="button"
              data-testid="portal-logout"
              onClick={() => {
                void (async () => {
                  await logout();
                  navigate("/portal/login", { replace: true });
                })();
              }}
              className="inline-flex items-center gap-1.5 border border-border px-4 py-2.5 rounded-full font-bold text-xs text-foreground hover:bg-muted transition-colors min-h-[44px]"
            >
              <LogOut size={14} />
              <span className="hidden sm:inline">Sign out</span>
            </button>
          </div>
        </div>

        <nav
          aria-label="Portal sections"
          className="max-w-5xl mx-auto px-4 pb-3 flex gap-2 overflow-x-auto"
          style={{ scrollbarWidth: "none" }}
        >
          {PORTAL_NAV.map((item) => {
            const Icon = item.icon;
            const active = isActivePath(location, item.path, item.exact);
            return (
              <Link
                key={item.path}
                href={item.path}
                aria-current={active ? "page" : undefined}
                className={`inline-flex items-center gap-2 px-4 py-2.5 rounded-full font-bold text-xs whitespace-nowrap min-h-[44px] transition-colors ${
                  active
                    ? "bg-gradient-to-br from-primary to-primary-dim text-white shadow-md shadow-primary/20"
                    : "border border-border text-foreground hover:bg-muted"
                }`}
              >
                <Icon size={14} />
                {item.label}
              </Link>
            );
          })}
        </nav>
      </header>

      <main className="flex-1 w-full max-w-5xl mx-auto px-4 py-6">
        {staffSignedIn ? (
          <div
            className="mb-5 rounded-xl border border-border bg-white/70 px-4 py-3 text-xs text-muted-foreground flex items-start gap-2"
            data-testid="portal-staff-session-note"
          >
            <ShieldAlert size={14} className="shrink-0 mt-0.5" />
            <span>
              You are also signed into the staff workspace in this browser. This portal uses a separate patient session
              (<code className="font-mono">enamel_portal</code>) and never shows staff data.
            </span>
          </div>
        ) : null}
        {children}
      </main>
    </div>
  );
}
