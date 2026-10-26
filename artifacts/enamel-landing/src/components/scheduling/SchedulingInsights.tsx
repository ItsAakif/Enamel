import type { SchedulingMetrics } from "@workspace/api-client-react";
import { DataState } from "@/components/common/DataState";

interface SchedulingInsightsProps {
  metrics: SchedulingMetrics | undefined;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="glass-card rounded-2xl p-6 shadow-sm">
      <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground mb-2">{label}</p>
      <p className="text-2xl font-black font-display text-foreground">{value}</p>
      {hint ? <p className="text-[11px] text-muted-foreground mt-1">{hint}</p> : null}
    </div>
  );
}

/**
 * Metrics strip backed by `GET /scheduling/metrics/`. The `definition` block the
 * API returns is surfaced as a tooltip so each number is explainable.
 */
export function SchedulingInsights({ metrics, loading, error, onRetry }: SchedulingInsightsProps) {
  const definition = metrics ? JSON.stringify(metrics.definition) : undefined;

  return (
    <DataState
      loading={loading}
      error={error}
      empty={!metrics}
      emptyTitle="No scheduling metrics"
      emptyMessage="The API returned no metrics for this window."
      onRetry={onRetry}
    >
      {metrics ? (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-5" title={definition}>
          <Stat
            label="Completed"
            value={String(metrics.completed)}
            hint={`${(metrics.completion_rate * 100).toFixed(1)}% completion`}
          />
          <Stat
            label="Cancelled"
            value={String(metrics.cancelled)}
            hint={`${(metrics.cancellation_rate * 100).toFixed(1)}% cancellation`}
          />
          <Stat
            label="No-shows"
            value={String(metrics.no_show)}
            hint={`${(metrics.no_show_rate * 100).toFixed(1)}% no-show`}
          />
          <Stat
            label="Utilisation"
            value={`${(metrics.utilisation * 100).toFixed(1)}%`}
            hint={`${metrics.booked_minutes} / ${metrics.available_minutes} booked minutes`}
          />
        </div>
      ) : null}
    </DataState>
  );
}
