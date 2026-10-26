import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Calendar, Download, Info, Loader2, MapPin } from "lucide-react";
import {
  analyticsAppointmentsRetrieve,
  analyticsExportsCreate,
  analyticsOverviewRetrieve,
  analyticsRecallRetrieve,
  analyticsRevenueRetrieve,
  analyticsWaitlistRetrieve,
  clinicsLocationsList,
  customFetch,
  type AnalyticsAppointmentsRetrieveParams,
  type ExportFormatEnum,
  type ExportReportEnum,
  type Location,
  type Metric,
} from "@workspace/api-client-react";
import AppLayout from "@/layouts/AppLayout";
import { DataState } from "@/components/common/DataState";
import { useAuth } from "@/hooks/use-auth";
import { useToast } from "@/hooks/use-toast";
import { apiErrorMessage, readString } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import { formatMetricValue, isoDate } from "@/lib/format";

const INPUT_CLASS =
  "border border-border rounded-xl px-4 py-2.5 text-sm font-medium bg-white/70 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all";

const REPORTS = ["overview", "appointments", "revenue", "recall", "waitlist"] as const;
type ReportKey = (typeof REPORTS)[number];

function daysAgo(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return isoDate(date);
}

interface SeriesPoint {
  date: string;
  [key: string]: string | number;
}

function toSeries(items: readonly unknown[]): SeriesPoint[] {
  return items.map((item) => {
    const point: SeriesPoint = { date: readString(item, "date") };
    if (item !== null && typeof item === "object") {
      for (const [key, value] of Object.entries(item as Record<string, unknown>)) {
        if (key !== "date" && typeof value === "number") point[key] = value;
      }
    }
    return point;
  });
}

function MetricCard({ metric }: { metric: Metric }) {
  return (
    <div className="glass-card p-6 rounded-2xl shadow-sm" data-testid={`metric-${metric.key}`}>
      <div className="flex items-start justify-between gap-2 mb-2">
        <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">{metric.label}</p>
        <span className="text-muted-foreground" title={metric.definition}>
          <Info size={13} />
        </span>
      </div>
      <h4 className="text-2xl font-black font-display text-foreground">{formatMetricValue(metric)}</h4>
      <p className="text-[11px] text-muted-foreground mt-2 leading-snug">{metric.definition}</p>
      <p className="text-[10px] font-mono text-muted-foreground mt-1">
        {metric.numerator} / {metric.denominator} = {metric.numerator_value} / {metric.denominator_value}
      </p>
    </div>
  );
}

function MetricTable({ metrics }: { metrics: readonly Metric[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="text-[10px] uppercase tracking-widest text-muted-foreground border-b border-border">
            <th className="py-3 pr-4 font-bold">Metric</th>
            <th className="py-3 pr-4 font-bold">Value</th>
            <th className="py-3 pr-4 font-bold">Definition</th>
            <th className="py-3 pr-4 font-bold">Numerator / denominator</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/60">
          {metrics.map((metric) => (
            <tr key={`${metric.key}-${metric.currency ?? ""}`} className="hover:bg-muted/40 transition-colors">
              <td className="py-3 pr-4 font-bold text-foreground">{metric.label}</td>
              <td className="py-3 pr-4 font-black font-display">{formatMetricValue(metric)}</td>
              <td className="py-3 pr-4 text-xs text-muted-foreground max-w-md">{metric.definition}</td>
              <td className="py-3 pr-4 text-xs font-mono text-muted-foreground">
                {metric.numerator} / {metric.denominator} ({metric.numerator_value} / {metric.denominator_value})
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function AnalyticsPage() {
  const { toast } = useToast();
  const { activeClinicId } = useAuth();

  const [from, setFrom] = useState(() => daysAgo(30));
  const [to, setTo] = useState(() => isoDate(new Date()));
  const [location, setLocation] = useState("");
  const [report, setReport] = useState<ReportKey>("overview");
  const [exportReport, setExportReport] = useState<ExportReportEnum>("overview");
  const [exportFormat, setExportFormat] = useState<ExportFormatEnum>("csv");

  const rangeParams = useMemo(
    () => ({ from, to, location: location || undefined }),
    [from, to, location],
  );

  const locationsQuery = useQuery({
    queryKey: ["analytics", "locations", activeClinicId],
    queryFn: () => clinicsLocationsList(activeClinicId as string),
    enabled: activeClinicId !== null,
  });

  const overviewQuery = useQuery({
    queryKey: ["analytics", "overview", rangeParams],
    queryFn: () => analyticsOverviewRetrieve(rangeParams),
  });

  const appointmentsQuery = useQuery({
    queryKey: ["analytics", "appointments", rangeParams],
    queryFn: () => analyticsAppointmentsRetrieve(rangeParams as AnalyticsAppointmentsRetrieveParams),
    enabled: report === "appointments",
  });

  const revenueQuery = useQuery({
    queryKey: ["analytics", "revenue", rangeParams],
    queryFn: () => analyticsRevenueRetrieve(rangeParams),
    enabled: report === "revenue",
  });

  const recallQuery = useQuery({
    queryKey: ["analytics", "recall", rangeParams],
    queryFn: () => analyticsRecallRetrieve(rangeParams),
    enabled: report === "recall",
  });

  const waitlistQuery = useQuery({
    queryKey: ["analytics", "waitlist", rangeParams],
    queryFn: () => analyticsWaitlistRetrieve(rangeParams),
    enabled: report === "waitlist",
  });

  const exportMutation = useMutation({
    mutationFn: () =>
      analyticsExportsCreate({
        report: exportReport,
        format: exportFormat,
        range: { from, to },
        location: location || null,
      }),
    onSuccess: async (job) => {
      if (!job.download_url) {
        toast({ title: "Export not ready", description: "The export completed without a download link.", variant: "destructive" });
        return;
      }
      try {
        const blob = await customFetch<Blob>(job.download_url, { responseType: "blob" });
        downloadBlob(blob, `${job.reference}.${job.format}`);
        toast({ title: "Export downloaded", description: `${job.reference} · ${job.row_count ?? 0} rows.` });
      } catch (error) {
        toast({ title: "Download failed", description: apiErrorMessage(error), variant: "destructive" });
      }
    },
    onError: (error) => toast({ title: "Export failed", description: apiErrorMessage(error), variant: "destructive" }),
  });

  const locations: Location[] = locationsQuery.data ?? [];
  const overview = overviewQuery.data;
  const appointments = appointmentsQuery.data;
  const revenue = revenueQuery.data;
  const recall = recallQuery.data;
  const waitlist = waitlistQuery.data;

  const appointmentsSeries = useMemo(
    () => (appointments ? toSeries(appointments.series) : []),
    [appointments],
  );
  const revenueSeries = useMemo(() => (revenue ? toSeries(revenue.series) : []), [revenue]);

  const activeQuery =
    report === "appointments"
      ? appointmentsQuery
      : report === "revenue"
        ? revenueQuery
        : report === "recall"
          ? recallQuery
          : report === "waitlist"
            ? waitlistQuery
            : overviewQuery;

  const activeMetrics =
    report === "appointments"
      ? appointments?.metrics
      : report === "revenue"
        ? revenue?.metrics
        : report === "recall"
          ? recall?.metrics
          : report === "waitlist"
            ? waitlist?.metrics
            : overview?.metrics;

  return (
    <AppLayout>
      <div className="max-w-screen-2xl mx-auto space-y-8">
        <header className="flex flex-wrap gap-4 justify-between items-end">
          <div>
            <span className="text-[10px] font-bold tracking-[0.2em] text-primary uppercase mb-2 block">
              Enterprise Intelligence
            </span>
            <h3 className="text-4xl font-extrabold font-display tracking-tight text-foreground">Clinic Performance</h3>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground flex items-center gap-1">
                <Calendar size={11} /> From
              </span>
              <input
                data-testid="analytics-from"
                type="date"
                value={from}
                onChange={(event) => setFrom(event.target.value)}
                className={INPUT_CLASS}
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground">To</span>
              <input
                data-testid="analytics-to"
                type="date"
                value={to}
                onChange={(event) => setTo(event.target.value)}
                className={INPUT_CLASS}
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground flex items-center gap-1">
                <MapPin size={11} /> Location
              </span>
              <select
                data-testid="analytics-location"
                value={location}
                onChange={(event) => setLocation(event.target.value)}
                className={INPUT_CLASS}
              >
                <option value="">All locations</option>
                {locations.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </header>

        <section className="glass-card rounded-2xl p-6 shadow-sm">
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground">Export report</span>
              <select
                data-testid="export-report"
                value={exportReport}
                onChange={(event) => setExportReport(event.target.value as ExportReportEnum)}
                className={INPUT_CLASS}
              >
                {REPORTS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground">Format</span>
              <select
                data-testid="export-format"
                value={exportFormat}
                onChange={(event) => setExportFormat(event.target.value as ExportFormatEnum)}
                className={INPUT_CLASS}
              >
                <option value="csv">CSV</option>
                <option value="json">JSON</option>
              </select>
            </label>
            <button
              type="button"
              data-testid="export-btn"
              disabled={exportMutation.isPending}
              onClick={() => exportMutation.mutate()}
              className="inline-flex items-center gap-2 bg-gradient-to-br from-primary to-primary-dim text-white px-6 py-2.5 rounded-xl font-bold text-sm shadow-lg shadow-primary/20 hover:opacity-90 transition-all disabled:opacity-60"
            >
              {exportMutation.isPending ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
              {exportMutation.isPending ? "Exporting…" : "Export report"}
            </button>
          </div>
        </section>

        <DataState
          loading={overviewQuery.isPending}
          error={overviewQuery.error}
          onRetry={() => void overviewQuery.refetch()}
        >
          <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
            {(overview?.metrics ?? []).map((metric) => (
              <MetricCard key={`${metric.key}-${metric.currency ?? ""}`} metric={metric} />
            ))}
          </section>
        </DataState>

        <div className="flex flex-wrap gap-2">
          {REPORTS.map((value) => (
            <button
              key={value}
              type="button"
              data-testid={`analytics-tab-${value}`}
              onClick={() => setReport(value)}
              className={`px-5 py-2.5 rounded-full text-xs font-bold uppercase tracking-wider transition-colors capitalize ${
                report === value ? "bg-primary text-white shadow-md" : "bg-background border border-border text-muted-foreground hover:bg-muted"
              }`}
            >
              {value}
            </button>
          ))}
        </div>

        <section className="glass-card rounded-2xl p-8 shadow-sm space-y-8">
          <DataState
            loading={activeQuery.isPending}
            error={activeQuery.error}
            empty={(activeMetrics ?? []).length === 0}
            emptyTitle="No metrics for this range"
            emptyMessage="The selected range has no activity. Counts render as zero with a denominator of 0."
            onRetry={() => void activeQuery.refetch()}
          >
            <div>
              <h4 className="text-xl font-extrabold font-display text-foreground capitalize mb-1">{report} report</h4>
              <p className="text-sm text-muted-foreground mb-6">
                {from} → {to}
                {location ? ` · location ${locations.find((l) => l.id === location)?.name ?? location}` : ""}
              </p>
              <MetricTable metrics={activeMetrics ?? []} />
            </div>

            {report === "appointments" && appointmentsSeries.length > 0 ? (
              <div>
                <h4 className="text-sm font-bold uppercase tracking-widest text-muted-foreground mb-4">
                  Per-day appointments
                </h4>
                <div className="h-[280px] w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={appointmentsSeries} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="hsl(var(--border))" />
                      <XAxis dataKey="date" axisLine={false} tickLine={false} tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} />
                      <YAxis axisLine={false} tickLine={false} tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} />
                      <Tooltip contentStyle={{ borderRadius: "1rem", border: "none", boxShadow: "0 10px 25px -5px rgba(0,0,0,0.1)" }} />
                      <Legend />
                      <Bar dataKey="total" fill="hsl(var(--primary))" radius={[6, 6, 0, 0]} />
                      <Bar dataKey="completed" fill="#10b981" radius={[6, 6, 0, 0]} />
                      <Bar dataKey="cancelled" fill="#f59e0b" radius={[6, 6, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            ) : null}

            {report === "revenue" && revenueSeries.length > 0 ? (
              <div>
                <h4 className="text-sm font-bold uppercase tracking-widest text-muted-foreground mb-4">
                  Per-day revenue (minor units)
                </h4>
                <div className="h-[280px] w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={revenueSeries} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                      <defs>
                        <linearGradient id="analyticsInvoiced" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
                          <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="hsl(var(--border))" />
                      <XAxis dataKey="date" axisLine={false} tickLine={false} tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} />
                      <YAxis axisLine={false} tickLine={false} tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} />
                      <Tooltip contentStyle={{ borderRadius: "1rem", border: "none", boxShadow: "0 10px 25px -5px rgba(0,0,0,0.1)" }} />
                      <Legend />
                      <Area type="monotone" dataKey="invoiced_minor" stroke="hsl(var(--primary))" strokeWidth={2} fill="url(#analyticsInvoiced)" />
                      <Area type="monotone" dataKey="collected_minor" stroke="#10b981" strokeWidth={2} fillOpacity={0.1} fill="#10b981" />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>
            ) : null}
          </DataState>
        </section>
      </div>
    </AppLayout>
  );
}
