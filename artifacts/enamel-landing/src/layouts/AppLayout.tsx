import { useState, type ReactNode } from "react";
import { Link, Redirect, useLocation } from "wouter";
import {
  LayoutDashboard,
  Users,
  Calendar,
  Activity,
  Mic,
  Microscope,
  LineChart,
  Receipt,
  Zap,
  Settings,
  LifeBuoy,
  LogOut,
  Menu,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { useAuth } from "@/hooks/use-auth";
import { initials } from "@/lib/format";

const NAV_ITEMS = [
  { label: "Dashboard", path: "/app", icon: LayoutDashboard, exact: true },
  { label: "Scheduling", path: "/app/scheduling", icon: Calendar, exact: false },
  { label: "Patients", path: "/app/patients", icon: Users, exact: false },
  { label: "Diagnostics", path: "/app/diagnostics", icon: Microscope, exact: false },
  { label: "Voice AI", path: "/app/voice-ai", icon: Mic, exact: false },
  { label: "Charting", path: "/app/charting", icon: Activity, exact: false },
  { label: "Billing", path: "/app/billing", icon: Receipt, exact: false },
  { label: "Analytics", path: "/app/analytics", icon: LineChart, exact: false },
  { label: "Automations", path: "/app/automations", icon: Zap, exact: false },
  { label: "Settings", path: "/app/settings", icon: Settings, exact: false },
];

function isActivePath(location: string, path: string, exact: boolean): boolean {
  if (exact) return location === path;
  return location === path || location.startsWith(`${path}/`);
}

function SidebarContent({ onNavClick }: { onNavClick?: () => void }) {
  const [location] = useLocation();
  const { user, memberships, currentClinic, activeClinicId, logout, selectClinic } = useAuth();
  const [loggingOut, setLoggingOut] = useState(false);

  const membership = memberships.find((m) => m.clinic_id === activeClinicId) ?? memberships[0] ?? null;
  const displayName = user?.full_name || user?.email || "Signed in";
  const roleLabel = membership ? membership.role.charAt(0).toUpperCase() + membership.role.slice(1) : "Member";

  return (
    <>
      <div className="mb-10">
        <h1 className="text-xl font-bold text-zinc-900 dark:text-zinc-100 font-display">Enamel Admin</h1>
        <p
          data-testid="current-clinic"
          className="text-[10px] uppercase tracking-widest text-zinc-500 font-sans font-medium mt-1"
        >
          {currentClinic?.name ?? "Select a clinic"}
        </p>
      </div>

      <nav className="flex-1 space-y-1 overflow-y-auto pb-4" style={{ scrollbarWidth: "none" }}>
        {NAV_ITEMS.map((item) => {
          const isActive = isActivePath(location, item.path, item.exact);
          const Icon = item.icon;
          return (
            <Link
              key={item.path}
              href={item.path}
              onClick={onNavClick}
              className={`flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-200 text-sm font-medium ${
                isActive
                  ? "bg-white dark:bg-zinc-800 text-primary shadow-sm translate-x-1"
                  : "text-zinc-500 dark:text-zinc-400 hover:bg-zinc-200/50 dark:hover:bg-zinc-800/50"
              }`}
            >
              <Icon size={18} className={isActive ? "text-primary" : ""} />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto pt-6 border-t border-border/50 space-y-4">
        <Link
          href="/guide"
          data-testid="sidebar-guide-link"
          onClick={onNavClick}
          className="flex items-center gap-3 px-4 py-2.5 rounded-xl text-xs font-bold text-zinc-500 dark:text-zinc-400 hover:bg-zinc-200/50 dark:hover:bg-zinc-800/50 hover:text-foreground transition-all"
        >
          <LifeBuoy size={14} />
          Help &amp; guide
        </Link>

        {memberships.length > 1 ? (
          <div>
            <label
              htmlFor="clinic-switcher"
              className="text-[10px] uppercase font-bold text-muted-foreground block mb-2 px-2"
            >
              Clinic
            </label>
            <select
              id="clinic-switcher"
              data-testid="clinic-switcher"
              value={activeClinicId ?? ""}
              onChange={(event) => selectClinic(event.target.value)}
              className="w-full bg-background border border-border px-3 py-2.5 rounded-xl text-xs font-bold text-foreground focus:ring-2 focus:ring-primary focus:outline-none"
            >
              {memberships.map((m) => (
                <option key={m.clinic_id} value={m.clinic_id}>
                  {m.clinic.name} ({m.role})
                </option>
              ))}
            </select>
          </div>
        ) : null}

        <div className="flex items-center gap-3 px-2">
          <div className="w-10 h-10 rounded-full bg-gradient-to-br from-primary to-primary-dim flex items-center justify-center text-white text-xs font-black shrink-0">
            {initials(displayName)}
          </div>
          <div className="min-w-0">
            <p className="text-xs font-bold text-foreground truncate">{displayName}</p>
            <p className="text-[10px] text-muted-foreground truncate">{roleLabel}</p>
          </div>
        </div>
        <button
          type="button"
          data-testid="logout-btn"
          disabled={loggingOut}
          onClick={() => {
            setLoggingOut(true);
            void logout().finally(() => setLoggingOut(false));
          }}
          className="w-full flex items-center justify-center gap-2 bg-gradient-to-br from-primary to-primary-dim text-primary-foreground py-3 px-4 rounded-xl font-bold text-xs shadow-lg shadow-primary/20 hover:opacity-90 transition-all active:scale-95 disabled:opacity-60"
        >
          <LogOut size={14} />
          {loggingOut ? "Signing out…" : "Sign out"}
        </button>
      </div>
    </>
  );
}

function FullScreenLoader() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background" role="status" aria-busy="true">
      <div className="flex flex-col items-center gap-3">
        <div className="w-10 h-10 rounded-full border-4 border-primary/20 border-t-primary animate-spin" />
        <span className="text-xs font-bold uppercase tracking-widest text-muted-foreground">
          Loading workspace
        </span>
      </div>
    </div>
  );
}

export default function AppLayout({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) return <FullScreenLoader />;

  if (!isAuthenticated) {
    const next = encodeURIComponent(location);
    return <Redirect to={`/login?next=${next}`} />;
  }

  return (
    <div className="min-h-screen flex relative" style={{ background: "linear-gradient(135deg, #f3f0ff 0%, #f9f6f5 40%, #f0f4ff 70%, #fdf4fb 100%)" }}>
      {/* Soft background orbs for glass depth */}
      <div className="fixed inset-0 pointer-events-none z-0 overflow-hidden">
        <div className="depth-blob w-[600px] h-[600px] top-[-100px] left-[-200px] opacity-40" style={{ background: "radial-gradient(circle, rgba(70,71,211,0.18) 0%, rgba(94,74,179,0.08) 50%, transparent 70%)" }} />
        <div className="depth-blob w-[500px] h-[500px] bottom-[-80px] right-[-120px] opacity-30" style={{ background: "radial-gradient(circle, rgba(150,55,118,0.14) 0%, rgba(70,71,211,0.06) 50%, transparent 70%)" }} />
        <div className="depth-blob w-[400px] h-[400px] top-[40%] right-[20%] opacity-20" style={{ background: "radial-gradient(circle, rgba(94,74,179,0.12) 0%, transparent 70%)" }} />
      </div>

      <aside className="w-64 fixed left-0 top-0 h-screen glass-sidebar flex-col p-6 z-50 hidden md:flex">
        <SidebarContent />
      </aside>

      <AnimatePresence>
        {mobileOpen && (
          <>
            <motion.div
              key="overlay"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-40 bg-black/50 md:hidden"
              onClick={() => setMobileOpen(false)}
            />
            <motion.aside
              key="drawer"
              initial={{ x: -280 }}
              animate={{ x: 0 }}
              exit={{ x: -280 }}
              transition={{ type: "spring", damping: 28, stiffness: 300 }}
              className="fixed left-0 top-0 h-screen w-64 glass-sidebar flex flex-col p-6 z-50 md:hidden"
            >
              <SidebarContent onNavClick={() => setMobileOpen(false)} />
            </motion.aside>
          </>
        )}
      </AnimatePresence>

      <main className="flex-1 md:ml-64 min-h-screen pb-12 flex flex-col relative z-10">
        <header className="sticky top-0 w-full z-40 glass-header shadow-[0_2px_20px_rgba(70,71,211,0.04)]">
          <div className="flex justify-between items-center px-4 md:px-8 py-4">
            <div className="flex items-center gap-4 md:gap-8">
              <button
                data-testid="mobile-menu-btn"
                onClick={() => setMobileOpen(o => !o)}
                className="md:hidden p-2 text-muted-foreground hover:bg-muted rounded-lg transition-colors"
                aria-label="Open navigation"
              >
                <Menu size={20} />
              </button>
              <Link
                href="/app"
                data-testid="app-wordmark"
                className="text-2xl font-black uppercase tracking-tighter text-foreground font-display hover:opacity-80 transition-opacity"
              >
                ENAMEL
              </Link>
              <nav className="hidden md:flex">
                <Link
                  href="/app"
                  className="text-primary font-bold border-b-2 border-primary pb-1 font-display tracking-tight text-sm"
                >
                  Workspace
                </Link>
              </nav>
            </div>
            {/* The right side of the staff header holds nothing: the
                workspace is the only destination, and notifications live in
                the Automations page. */}
            <div />
          </div>
        </header>

        <motion.div
          key={location}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
          className="flex-1 p-4 md:p-8"
        >
          {children}
        </motion.div>
      </main>
    </div>
  );
}
