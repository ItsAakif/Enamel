import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ClipboardList, HeartPulse, Stethoscope } from "lucide-react";
import { portalRecordsRetrieve } from "@workspace/api-client-react";
import PortalLayout from "@/layouts/PortalLayout";
import { PORTAL_REQUEST } from "@/hooks/use-portal-auth";
import { readBoolean, readRecordArray, readString } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { DataState } from "@/components/common/DataState";
import { Pill } from "@/components/settings/settings-ui";

interface AllergyRow {
  id: string;
  substance: string;
  severity: string;
  is_active: boolean;
}

interface HistoryRow {
  id: string;
  kind: string;
  label: string;
  since: string;
  is_active: boolean;
}

interface EncounterRow {
  id: string;
  kind: string;
  occurred_at: string;
  status: string;
  chief_complaint: string;
}

function readArray(source: unknown, key: string): Record<string, unknown>[] {
  return readRecordArray(source, key);
}

function readObject(source: unknown, key: string): Record<string, unknown> | null {
  if (source !== null && typeof source === "object") {
    const value = (source as Record<string, unknown>)[key];
    if (value !== null && typeof value === "object" && !Array.isArray(value)) {
      return value as Record<string, unknown>;
    }
  }
  return null;
}

export default function PortalRecordsPage() {
  const recordsQuery = useQuery({
    queryKey: ["portal", "records"],
    queryFn: () => portalRecordsRetrieve(PORTAL_REQUEST),
    retry: false,
  });

  const summary: unknown = recordsQuery.data;
  const patient = readObject(summary, "patient");
  const allergies: AllergyRow[] = readArray(summary, "allergies").map((row) => ({
    id: readString(row, "id"),
    substance: readString(row, "substance"),
    severity: readString(row, "severity"),
    is_active: readBoolean(row, "is_active"),
  }));
  const history: HistoryRow[] = readArray(summary, "medical_history").map((row) => ({
    id: readString(row, "id"),
    kind: readString(row, "kind"),
    label: readString(row, "label"),
    since: readString(row, "since"),
    is_active: readBoolean(row, "is_active"),
  }));
  const encounters: EncounterRow[] = readArray(summary, "encounters").map((row) => ({
    id: readString(row, "id"),
    kind: readString(row, "kind"),
    occurred_at: readString(row, "occurred_at"),
    status: readString(row, "status"),
    chief_complaint: readString(row, "chief_complaint"),
  }));

  return (
    <PortalLayout>
      <div className="space-y-6">
        <header>
          <h1 className="text-3xl font-extrabold text-foreground font-display leading-tight">My Records</h1>
          <p className="text-sm text-muted-foreground mt-2">
            A summary of the information your clinic has shared with you.
          </p>
        </header>

        <DataState
          loading={recordsQuery.isPending}
          error={recordsQuery.error}
          empty={summary === null || summary === undefined}
          emptyTitle="No record summary"
          emptyMessage="Your clinic has not published a record summary yet."
          onRetry={() => void recordsQuery.refetch()}
        >
          <div className="space-y-6">
            <section aria-label="Patient details" className="bg-card border border-border rounded-[2rem] p-6 shadow-sm">
              <h2 className="text-xs font-bold uppercase tracking-widest text-muted-foreground mb-4 flex items-center gap-2">
                <HeartPulse size={14} /> Patient details
              </h2>
              {patient ? (
                <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <dt className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Name</dt>
                    <dd className="text-sm font-semibold text-foreground">
                      {`${readString(patient, "first_name")} ${readString(patient, "last_name")}`.trim() || "—"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Date of birth</dt>
                    <dd className="text-sm font-semibold text-foreground">
                      {formatDate(readString(patient, "date_of_birth"))}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Email</dt>
                    <dd className="text-sm font-semibold text-foreground break-all">{readString(patient, "email") || "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Phone</dt>
                    <dd className="text-sm font-semibold text-foreground">{readString(patient, "phone_e164") || "—"}</dd>
                  </div>
                </dl>
              ) : (
                <p className="text-sm text-muted-foreground">No patient details returned.</p>
              )}
            </section>

            <section aria-label="Allergies" className="bg-card border border-border rounded-[2rem] p-6 shadow-sm">
              <h2 className="text-xs font-bold uppercase tracking-widest text-muted-foreground mb-4 flex items-center gap-2">
                <AlertTriangle size={14} /> Allergies ({allergies.length})
              </h2>
              {allergies.length === 0 ? (
                <p className="text-sm text-muted-foreground">No allergies recorded.</p>
              ) : (
                <ul className="space-y-2">
                  {allergies.map((allergy) => (
                    <li key={allergy.id} className="flex items-center justify-between p-3 bg-muted/50 rounded-xl">
                      <span className="text-sm font-semibold text-foreground">{allergy.substance}</span>
                      <Pill tone={allergy.is_active ? "warning" : "neutral"}>
                        {allergy.severity || "unknown"}
                      </Pill>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section aria-label="Medical history" className="bg-card border border-border rounded-[2rem] p-6 shadow-sm">
              <h2 className="text-xs font-bold uppercase tracking-widest text-muted-foreground mb-4 flex items-center gap-2">
                <ClipboardList size={14} /> Medical history ({history.length})
              </h2>
              {history.length === 0 ? (
                <p className="text-sm text-muted-foreground">No medical history recorded.</p>
              ) : (
                <ul className="space-y-2">
                  {history.map((row) => (
                    <li key={row.id} className="flex items-center justify-between p-3 bg-muted/50 rounded-xl">
                      <div>
                        <span className="text-sm font-semibold text-foreground">{row.label}</span>
                        <span className="text-xs text-muted-foreground ml-2">{row.kind}</span>
                      </div>
                      <Pill tone={row.is_active ? "primary" : "neutral"}>{row.is_active ? "active" : "inactive"}</Pill>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section aria-label="Encounters" className="bg-card border border-border rounded-[2rem] p-6 shadow-sm">
              <h2 className="text-xs font-bold uppercase tracking-widest text-muted-foreground mb-4 flex items-center gap-2">
                <Stethoscope size={14} /> Visits ({encounters.length})
              </h2>
              {encounters.length === 0 ? (
                <p className="text-sm text-muted-foreground">No visits recorded.</p>
              ) : (
                <ul className="space-y-2">
                  {encounters.map((encounter) => (
                    <li key={encounter.id} className="p-3 bg-muted/50 rounded-xl">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-sm font-semibold text-foreground">{encounter.kind || "Visit"}</span>
                        <span className="text-xs text-muted-foreground">{formatDate(encounter.occurred_at)}</span>
                      </div>
                      {encounter.chief_complaint ? (
                        <p className="text-xs text-muted-foreground mt-1">{encounter.chief_complaint}</p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </DataState>
      </div>
    </PortalLayout>
  );
}
