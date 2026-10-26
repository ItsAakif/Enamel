import { useQuery } from "@tanstack/react-query";
import { CalendarClock, Info } from "lucide-react";
import {
  clinicsRetrieve,
  getClinicsRetrieveQueryKey,
  publicClinicsRetrieve,
} from "@workspace/api-client-react";
import { useAuth } from "@/hooks/use-auth";
import { readBoolean, readNumber, readString } from "@/lib/api";
import { DataState } from "@/components/common/DataState";
import { Pill, SectionCard } from "@/components/settings/settings-ui";

/**
 * The effective public-booking policy, read-only.
 *
 * There is no authenticated read/update endpoint for ``BookingPolicy`` (it is
 * only exposed as the ``policy`` block of the public clinic payload). Until a
 * settings endpoint exists the UI shows the effective values the public flow
 * enforces and says so, rather than inventing an editor that cannot persist.
 */

const POLICY_ROWS: { key: string; label: string; suffix?: string }[] = [
  { key: "lead_time_hours", label: "Minimum lead time", suffix: "h" },
  { key: "window_days", label: "Booking window", suffix: "days" },
  { key: "hold_minutes", label: "Slot hold duration", suffix: "min" },
  { key: "cancel_cutoff_hours", label: "Cancel cutoff", suffix: "h" },
  { key: "reschedule_cutoff_hours", label: "Reschedule cutoff", suffix: "h" },
  { key: "max_future_bookings", label: "Max future bookings" },
];

export function BookingPolicySection() {
  const { activeClinicId } = useAuth();

  const clinicQuery = useQuery({
    queryKey: getClinicsRetrieveQueryKey(activeClinicId ?? ""),
    queryFn: () => clinicsRetrieve(activeClinicId as string),
    enabled: activeClinicId !== null,
    retry: false,
  });

  const slug = clinicQuery.data?.slug ?? "";
  const policyQuery = useQuery({
    queryKey: ["public-clinic-policy", slug],
    queryFn: () => publicClinicsRetrieve(slug),
    enabled: slug !== "",
    retry: false,
  });

  const policy = policyQuery.data?.policy ?? null;

  return (
    <SectionCard
      icon={CalendarClock}
      title="Booking Policy"
      description="The rules the public booking flow and patient self-service enforce."
    >
      <div className="mb-5 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800">
        <Info size={14} className="shrink-0 mt-0.5" />
        <span>
          Read-only. The API does not expose an authenticated endpoint to edit the booking policy, so these are the
          effective values reported by the public clinic payload.
        </span>
      </div>

      <DataState
        loading={clinicQuery.isPending || policyQuery.isPending}
        error={clinicQuery.error ?? policyQuery.error}
        empty={policy === null}
        emptyTitle="No policy"
        emptyMessage="The public clinic payload did not include a policy block."
        onRetry={() => void policyQuery.refetch()}
      >
        {policy ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {POLICY_ROWS.map((row) => {
              const value = readNumber(policy, row.key);
              return (
                <div key={row.key} className="flex items-center justify-between p-4 bg-muted/50 rounded-xl">
                  <span className="text-xs font-bold uppercase tracking-wider text-muted-foreground">{row.label}</span>
                  <span className="text-sm font-semibold text-foreground">
                    {value === null ? "—" : `${value}${row.suffix ? ` ${row.suffix}` : ""}`}
                  </span>
                </div>
              );
            })}
            <div className="flex items-center justify-between p-4 bg-muted/50 rounded-xl">
              <span className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                Online rescheduling
              </span>
              <Pill tone={readBoolean(policy, "allow_online_reschedule") ? "success" : "neutral"}>
                {readBoolean(policy, "allow_online_reschedule") ? "allowed" : "disabled"}
              </Pill>
            </div>
            <div className="flex items-center justify-between p-4 bg-muted/50 rounded-xl">
              <span className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                Email verification
              </span>
              <Pill tone={readBoolean(policy, "require_email_verification") ? "warning" : "neutral"}>
                {readBoolean(policy, "require_email_verification") ? "required" : "not required"}
              </Pill>
            </div>
            <div className="flex items-center justify-between p-4 bg-muted/50 rounded-xl">
              <span className="text-xs font-bold uppercase tracking-wider text-muted-foreground">Public booking</span>
              <Pill tone={readBoolean(policy, "is_public_enabled") ? "success" : "danger"}>
                {readBoolean(policy, "is_public_enabled") ? "enabled" : "disabled"}
              </Pill>
            </div>
            <div className="flex items-center justify-between p-4 bg-muted/50 rounded-xl">
              <span className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                Consent wording
              </span>
              <span className="text-sm font-semibold text-foreground">
                {readString(policy, "consent_wording_version") || "—"}
              </span>
            </div>
          </div>
        ) : null}
      </DataState>
    </SectionCard>
  );
}
