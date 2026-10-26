import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ScrollText } from "lucide-react";
import {
  clinicsAuditList,
  getClinicsAuditListQueryKey,
  type AuditEvent,
} from "@workspace/api-client-react";
import { useAuth } from "@/hooks/use-auth";
import { formatDateTime } from "@/lib/format";
import { DataState } from "@/components/common/DataState";
import {
  Field,
  PrimaryButton,
  SectionCard,
  SecondaryButton,
  TextInput,
} from "@/components/settings/settings-ui";

/** Pull the opaque ``cursor`` out of a cursor-pagination ``next``/``previous`` link. */
function cursorFromLink(link: string | null | undefined): string | null {
  if (!link) return null;
  try {
    return new URL(link, window.location.origin).searchParams.get("cursor");
  } catch {
    return null;
  }
}

export function AuditLogSection() {
  const { activeClinicId, memberships } = useAuth();
  const clinicId = activeClinicId ?? "";
  const membership = memberships.find((m) => m.clinic_id === activeClinicId) ?? memberships[0] ?? null;
  const isOwner = membership?.role === "owner";

  const [entityType, setEntityType] = useState("");
  const [entityId, setEntityId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);

  const params = {
    ...(entityType.trim() ? { entity_type: entityType.trim() } : {}),
    ...(entityId.trim() ? { entity_id: entityId.trim() } : {}),
    ...(from ? { from } : {}),
    ...(to ? { to } : {}),
    ...(cursor ? { cursor } : {}),
  };

  const auditQuery = useQuery({
    queryKey: getClinicsAuditListQueryKey(clinicId, params),
    queryFn: () => clinicsAuditList(clinicId, params),
    enabled: activeClinicId !== null && isOwner,
    retry: false,
  });

  if (!isOwner) {
    return (
      <SectionCard icon={ScrollText} title="Audit Log" description="Append-only record of every change in this clinic.">
        <p className="text-sm text-muted-foreground">
          The audit log is available to clinic owners only. Your role is{" "}
          <span className="font-bold text-foreground">{membership?.role ?? "unknown"}</span>.
        </p>
      </SectionCard>
    );
  }

  const events: AuditEvent[] = auditQuery.data?.results ?? [];
  const nextCursor = cursorFromLink(auditQuery.data?.next ?? null);

  function applyFilters() {
    setCursor(null);
  }

  return (
    <SectionCard icon={ScrollText} title="Audit Log" description="Append-only record of every change in this clinic.">
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-3 mb-5">
        <Field label="Entity type" htmlFor="audit-entity-type" hint="e.g. clinics.membership">
          <TextInput
            id="audit-entity-type"
            data-testid="audit-entity-type"
            value={entityType}
            onChange={(e) => setEntityType(e.target.value)}
          />
        </Field>
        <Field label="Entity id" htmlFor="audit-entity-id">
          <TextInput id="audit-entity-id" value={entityId} onChange={(e) => setEntityId(e.target.value)} />
        </Field>
        <Field label="From" htmlFor="audit-from">
          <TextInput id="audit-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </Field>
        <Field label="To" htmlFor="audit-to" hint="Both dates are required for a range.">
          <TextInput id="audit-to" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </Field>
      </div>
      <div className="flex items-center gap-3 mb-5">
        <PrimaryButton data-testid="audit-apply" onClick={applyFilters}>
          Apply filters
        </PrimaryButton>
        <SecondaryButton
          onClick={() => {
            setEntityType("");
            setEntityId("");
            setFrom("");
            setTo("");
            setCursor(null);
          }}
        >
          Clear
        </SecondaryButton>
      </div>

      <DataState
        loading={auditQuery.isPending}
        error={auditQuery.error}
        empty={events.length === 0}
        emptyTitle="No audit events"
        emptyMessage="No events match the current filters."
        onRetry={() => void auditQuery.refetch()}
      >
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm" data-testid="audit-table">
            <thead>
              <tr className="text-[10px] uppercase tracking-widest text-muted-foreground border-b border-border/60">
                <th className="py-3 pr-4 font-bold">When</th>
                <th className="py-3 pr-4 font-bold">Action</th>
                <th className="py-3 pr-4 font-bold">Entity</th>
                <th className="py-3 pr-4 font-bold">Changes</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.id} className="border-b border-border/40 align-top">
                  <td className="py-3 pr-4 whitespace-nowrap text-muted-foreground">{formatDateTime(event.created_at)}</td>
                  <td className="py-3 pr-4 font-semibold text-foreground">{event.action}</td>
                  <td className="py-3 pr-4 text-xs text-muted-foreground">
                    <code className="font-mono">{event.entity_type}</code>
                    <br />
                    <span className="font-mono">{event.entity_id}</span>
                  </td>
                  <td className="py-3 pr-4 text-xs">
                    {event.before ? (
                      <div className="text-muted-foreground">
                        before <code className="font-mono break-all">{JSON.stringify(event.before)}</code>
                      </div>
                    ) : null}
                    {event.after ? (
                      <div className="text-foreground">
                        after <code className="font-mono break-all">{JSON.stringify(event.after)}</code>
                      </div>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </DataState>

      <div className="flex items-center justify-between mt-5">
        <span className="text-xs text-muted-foreground">{events.length} events on this page</span>
        <div className="flex items-center gap-2">
          <SecondaryButton
            disabled={!auditQuery.data?.previous}
            onClick={() => setCursor(cursorFromLink(auditQuery.data?.previous ?? null))}
          >
            Previous
          </SecondaryButton>
          <SecondaryButton disabled={nextCursor === null} onClick={() => setCursor(nextCursor)}>
            Next
          </SecondaryButton>
        </div>
      </div>
    </SectionCard>
  );
}
