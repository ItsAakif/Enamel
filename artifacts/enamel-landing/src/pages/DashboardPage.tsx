import { useMemo } from "react";
import { useLocation } from "wouter";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Calendar,
  ChevronRight,
  Receipt,
  TrendingUp,
  UserPlus,
  Users,
  type LucideIcon,
} from "lucide-react";
import { motion } from "framer-motion";
import {
  analyticsOverviewRetrieve,
  appointmentsList,
  patientsList,
  providersList,
  appointmentTypesList,
  type Appointment,
  type AppointmentType,
  type Metric,
  type PatientList,
  type PatientsListParams,
  type Provider,
} from "@workspace/api-client-react";
import AppLayout from "@/layouts/AppLayout";
import { DataState } from "@/components/common/DataState";
import { useAuth } from "@/hooks/use-auth";
import { toPage } from "@/lib/api";
import { formatMetricValue, formatTime, isoDate } from "@/lib/format";

function iconForMetric(metric: Metric): LucideIcon {
  switch (metric.unit) {
    case "ratio":
    case "percent":
      return TrendingUp;
    case "currency":
      return Receipt;
    case "minutes":
      return Calendar;
    case "count":
      return Users;
    default:
      return Activity;
  }
}

const COLOR_BY_UNIT: Record<string, string> = {
  ratio: "text-emerald-600 bg-emerald-50",
  percent: "text-emerald-600 bg-emerald-50",
  currency: "text-primary bg-primary/10",
  minutes: "text-amber-600 bg-amber-50",
  count: "text-indigo-500 bg-indigo-50",
};

function useDashboardData() {
  const now = new Date();
  const from = isoDate(new Date(now.getFullYear(), now.getMonth(), 1));
  const to = isoDate(now);
  const today = isoDate(now);

  const overview = useQuery({
    queryKey: ["analytics", "overview", from, to],
    queryFn: () => analyticsOverviewRetrieve({ from, to }),
  });

  const schedule = useQuery({
    queryKey: ["appointments", "today", today],
    queryFn: async () => toPage<Appointment>(await appointmentsList({ from: today, to: today, ordering: "start_at" })),
  });

  const patients = useQuery({
    queryKey: ["patients", "lookup"],
    queryFn: async () =>
      toPage<PatientList>(
        await patientsList({ page_size: 100 } as PatientsListParams),
      ).results,
  });

  const providers = useQuery({
    queryKey: ["providers", "lookup"],
    queryFn: async () => toPage<Provider>(await providersList()).results,
  });

  const types = useQuery({
    queryKey: ["appointment-types", "lookup"],
    queryFn: async () => toPage<AppointmentType>(await appointmentTypesList()).results,
  });

  return { from, to, overview, schedule, patients, providers, types };
}

export default function DashboardPage() {
  const { user, currentClinic } = useAuth();
  const [, navigate] = useLocation();
  const { from, to, overview, schedule, patients, providers, types } = useDashboardData();

  const patientNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const patient of patients.data ?? []) {
      map.set(patient.id, patient.full_name || `${patient.first_name} ${patient.last_name}`.trim());
    }
    return map;
  }, [patients.data]);

  const providerNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const provider of providers.data ?? []) map.set(provider.id, provider.display_name);
    return map;
  }, [providers.data]);

  const typeNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const type of types.data ?? []) map.set(type.id, type.name);
    return map;
  }, [types.data]);

  const metrics = overview.data?.metrics ?? [];
  const firstName = (user?.full_name ?? "").split(/\s+/).filter(Boolean)[0] ?? "there";

  return (
    <AppLayout>
      <div className="max-w-screen-2xl mx-auto space-y-8">
        <header className="flex flex-wrap gap-4 justify-between items-end">
          <div>
            <span className="text-[10px] font-bold tracking-[0.2em] text-primary uppercase mb-2 block font-sans">
              Clinical Overview
            </span>
            <h2 className="text-4xl font-extrabold text-foreground font-display leading-none">
              Welcome back, {firstName}.
            </h2>
            <p className="text-muted-foreground mt-2 font-sans text-sm">
              {currentClinic?.name ?? "Workspace"} · {from} to {to}
            </p>
          </div>
          <button
            type="button"
            data-testid="new-patient-btn"
            onClick={() => navigate("/app/patients?new=1")}
            className="flex items-center gap-2 bg-gradient-to-br from-primary to-primary-dim text-white px-8 py-3 rounded-full font-display font-bold text-sm shadow-lg shadow-primary/20 hover:opacity-90 active:scale-95 transition-all"
          >
            <UserPlus size={18} />
            New Patient
          </button>
        </header>

        <DataState
          loading={overview.isPending}
          error={overview.error}
          empty={metrics.length === 0}
          emptyTitle="No analytics for this period"
          emptyMessage="The API returned no metrics for this clinic and date range."
          onRetry={() => void overview.refetch()}
        >
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-5">
            {metrics.map((metric, index) => {
              const Icon = iconForMetric(metric);
              const color = COLOR_BY_UNIT[metric.unit] ?? "text-primary bg-primary/10";
              return (
                <motion.div
                  key={metric.key}
                  initial={{ opacity: 0, y: 16 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: index * 0.05 }}
                  className="glass-card rounded-[1.5rem] p-6 shadow-sm"
                  data-testid={`metric-${metric.key}`}
                  title={metric.definition}
                >
                  <div className="flex items-start justify-between mb-5">
                    <div className={`p-2.5 rounded-xl ${color}`}>
                      <Icon size={20} />
                    </div>
                    <span className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                      {metric.unit}
                    </span>
                  </div>
                  <p className="text-3xl font-black font-display text-foreground">
                    {formatMetricValue(metric)}
                  </p>
                  <p className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground mt-2">
                    {metric.label}
                  </p>
                  <p className="text-xs text-muted-foreground mt-2 leading-snug" title={metric.definition}>
                    {metric.definition}
                  </p>
                  <p className="text-[10px] text-muted-foreground/80 mt-2 font-medium">
                    {metric.numerator} / {metric.denominator}
                  </p>
                </motion.div>
              );
            })}
          </div>
        </DataState>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15 }}
          className="glass-card rounded-[2rem] p-8 shadow-sm"
        >
          <div className="flex justify-between items-center mb-6">
            <h3 className="text-xl font-bold font-display">Today's Schedule</h3>
            <span className="text-xs font-bold text-muted-foreground">{isoDate(new Date())}</span>
          </div>
          <DataState
            loading={schedule.isPending}
            error={schedule.error}
            empty={(schedule.data?.results ?? []).length === 0}
            emptyTitle="No appointments today"
            emptyMessage="Nothing is booked for today at this clinic."
            onRetry={() => void schedule.refetch()}
          >
            <div className="divide-y divide-border">
              {(schedule.data?.results ?? []).map((appointment) => (
                <div
                  key={appointment.id}
                  className="flex items-center gap-6 py-4 group hover:bg-muted/30 -mx-2 px-2 rounded-xl transition-colors"
                >
                  <span className="text-sm font-bold text-muted-foreground font-sans w-16 shrink-0">
                    {formatTime(appointment.start_at)}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-bold text-foreground font-display truncate">
                      {patientNames.get(appointment.patient) ?? (appointment.reason || "Patient")}
                    </p>
                    <p className="text-xs text-muted-foreground truncate">
                      {typeNames.get(appointment.appointment_type) ?? (appointment.reason || "Appointment")}
                      {providerNames.get(appointment.provider) ? ` · ${providerNames.get(appointment.provider)}` : ""}
                    </p>
                  </div>
                  <span className="text-[10px] font-bold px-3 py-1.5 rounded-full uppercase tracking-wider shrink-0 bg-muted text-muted-foreground">
                    {appointment.status.replace(/_/g, " ")}
                  </span>
                  <ChevronRight size={16} className="text-muted-foreground group-hover:text-primary transition-colors shrink-0" />
                </div>
              ))}
            </div>
          </DataState>
        </motion.div>
      </div>
    </AppLayout>
  );
}
