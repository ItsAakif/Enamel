import { useMemo } from "react";
import { Link } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { CalendarDays, ClipboardList, FileText, HeartPulse, Receipt } from "lucide-react";
import { portalAppointmentsList, portalInvoicesList } from "@workspace/api-client-react";
import PortalLayout from "@/layouts/PortalLayout";
import { usePortalAuth, PORTAL_REQUEST } from "@/hooks/use-portal-auth";
import { apiErrorMessage, toPage } from "@/lib/api";
import { formatDateTime, formatMinorUnits } from "@/lib/format";
import { DataState } from "@/components/common/DataState";

const TERMINAL_STATUSES = new Set(["completed", "cancelled", "no_show"]);

interface AppointmentRow {
  id: string;
  start_at: string;
  end_at: string;
  status: string;
  appointment_type_name: string;
  provider_name: string;
  location_name: string;
}

interface InvoiceRow {
  id: string;
  number: string;
  status: string;
  currency: string;
  total_minor: number;
  paid_minor: number;
  balance_minor: number;
}

export default function PortalHomePage() {
  const { patient } = usePortalAuth();

  const appointmentsQuery = useQuery({
    queryKey: ["portal", "appointments", "home"],
    queryFn: async () => toPage<AppointmentRow>(await portalAppointmentsList({ page_size: 100 }, PORTAL_REQUEST)).results,
    retry: false,
  });

  const invoicesQuery = useQuery({
    queryKey: ["portal", "invoices", "home"],
    queryFn: async () => toPage<InvoiceRow>(await portalInvoicesList({ page_size: 100 }, PORTAL_REQUEST)).results,
    retry: false,
  });

  const upcoming = useMemo(() => {
    const now = Date.now();
    return (appointmentsQuery.data ?? [])
      .filter((item) => !TERMINAL_STATUSES.has(item.status) && new Date(item.start_at).getTime() >= now)
      .sort((a, b) => new Date(a.start_at).getTime() - new Date(b.start_at).getTime());
  }, [appointmentsQuery.data]);

  const nextAppointment = upcoming[0] ?? null;

  const balances = useMemo(() => {
    const invoices = invoicesQuery.data ?? [];
    const currency = invoices[0]?.currency ?? "INR";
    const outstanding = invoices.reduce((sum, invoice) => sum + Math.max(0, invoice.balance_minor), 0);
    return { count: invoices.length, outstanding, currency };
  }, [invoicesQuery.data]);

  const displayName = patient
    ? patient.preferred_name || `${patient.first_name} ${patient.last_name}`.trim()
    : "there";

  return (
    <PortalLayout>
      <div className="space-y-6">
        <header>
          <span className="text-[10px] font-bold tracking-[0.2em] text-primary uppercase mb-2 block font-sans">
            Patient Portal
          </span>
          <h1 className="text-3xl font-extrabold text-foreground font-display leading-tight">
            Welcome back, {displayName}
          </h1>
        </header>

        <section aria-label="Next appointment" className="bg-card border border-border rounded-[2rem] p-6 shadow-sm">
          <h2 className="text-xs font-bold uppercase tracking-widest text-muted-foreground mb-4">Next appointment</h2>
          <DataState
            loading={appointmentsQuery.isPending}
            error={appointmentsQuery.error}
            onRetry={() => void appointmentsQuery.refetch()}
          >
            {nextAppointment ? (
              <div>
                <p className="text-xl font-extrabold font-display text-foreground">
                  {formatDateTime(nextAppointment.start_at)}
                </p>
                <p className="text-sm text-muted-foreground mt-1">
                  {nextAppointment.appointment_type_name || "Appointment"}
                  {nextAppointment.provider_name ? ` · ${nextAppointment.provider_name}` : ""}
                  {nextAppointment.location_name ? ` · ${nextAppointment.location_name}` : ""}
                </p>
                <Link
                  href="/portal/appointments"
                  className="mt-4 inline-flex items-center gap-2 bg-gradient-to-br from-primary to-primary-dim text-white px-5 py-3 rounded-full font-bold text-xs shadow-lg shadow-primary/20 hover:opacity-90 active:scale-95 transition-all min-h-[44px]"
                >
                  <CalendarDays size={14} /> Manage appointments
                </Link>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                No upcoming appointments.{" "}
                <Link href="/portal/appointments" className="text-primary font-bold hover:underline">
                  View history
                </Link>
              </p>
            )}
          </DataState>
        </section>

        <section aria-label="Balances" className="bg-card border border-border rounded-[2rem] p-6 shadow-sm">
          <h2 className="text-xs font-bold uppercase tracking-widest text-muted-foreground mb-4">Balances</h2>
          <DataState
            loading={invoicesQuery.isPending}
            error={invoicesQuery.error}
            onRetry={() => void invoicesQuery.refetch()}
          >
            <p className="text-3xl font-extrabold font-display text-foreground">
              {formatMinorUnits(balances.outstanding, balances.currency)}
            </p>
            <p className="text-sm text-muted-foreground mt-1">
              outstanding across {balances.count} {balances.count === 1 ? "invoice" : "invoices"}
            </p>
            <Link
              href="/portal/invoices"
              className="mt-4 inline-flex items-center gap-2 border border-border px-5 py-3 rounded-full font-bold text-xs hover:bg-muted transition-colors min-h-[44px]"
            >
              <Receipt size={14} /> View invoices
            </Link>
          </DataState>
        </section>

        <nav aria-label="Portal quick links" className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { href: "/portal/records", label: "My records", icon: HeartPulse },
            { href: "/portal/plans", label: "Treatment plans", icon: ClipboardList },
            { href: "/portal/invoices", label: "Invoices", icon: Receipt },
            { href: "/portal/documents", label: "Documents", icon: FileText },
          ].map((item) => {
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className="flex flex-col items-start gap-2 bg-card border border-border rounded-2xl p-4 shadow-sm hover:bg-muted/60 transition-colors min-h-[88px]"
              >
                <Icon size={18} className="text-primary" />
                <span className="text-xs font-bold text-foreground">{item.label}</span>
              </Link>
            );
          })}
        </nav>

        {appointmentsQuery.error ? (
          <p className="text-xs text-destructive">{apiErrorMessage(appointmentsQuery.error)}</p>
        ) : null}
      </div>
    </PortalLayout>
  );
}
