import { useState } from "react";
import { Link, useLocation, useParams } from "wouter";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { Activity, Archive, ArrowLeft, Mail, Phone } from "lucide-react";
import {
  patientsAllergiesList,
  patientsDestroy,
  patientsEncountersList,
  patientsMedicalHistoryList,
  patientsRetrieve,
  patientsTimelineRetrieve,
  type Allergy,
  type Encounter,
  type MedicalHistory,
  type TimelineEvent,
} from "@workspace/api-client-react";
import AppLayout from "@/layouts/AppLayout";
import { DataState } from "@/components/common/DataState";
import { useToast } from "@/hooks/use-toast";
import { apiErrorMessage, toPage } from "@/lib/api";
import { formatDate, formatDateTime, initials } from "@/lib/format";

function Section({
  title,
  children,
  action,
}: {
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="glass-card rounded-[2rem] p-8 shadow-sm">
      <div className="flex justify-between items-center mb-6">
        <h3 className="text-xl font-bold tracking-tight font-display">{title}</h3>
        {action}
      </div>
      {children}
    </motion.div>
  );
}

export default function PatientDetailPage() {
  const params = useParams<{ id: string }>();
  const patientId = params.id ?? "";
  const [, navigate] = useLocation();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [archiveError, setArchiveError] = useState<string | null>(null);

  const patientQuery = useQuery({
    queryKey: ["patients", "detail", patientId],
    queryFn: () => patientsRetrieve(patientId),
    enabled: patientId !== "",
  });

  const allergiesQuery = useQuery({
    queryKey: ["patients", patientId, "allergies"],
    queryFn: async () => toPage<Allergy>(await patientsAllergiesList(patientId)),
    enabled: patientId !== "",
  });

  const historyQuery = useQuery({
    queryKey: ["patients", patientId, "medical-history"],
    queryFn: async () => toPage<MedicalHistory>(await patientsMedicalHistoryList(patientId)),
    enabled: patientId !== "",
  });

  const encountersQuery = useQuery({
    queryKey: ["patients", patientId, "encounters"],
    queryFn: async () => toPage<Encounter>(await patientsEncountersList(patientId)),
    enabled: patientId !== "",
  });

  const timelineQuery = useQuery({
    queryKey: ["patients", patientId, "timeline"],
    queryFn: () => patientsTimelineRetrieve(patientId, { page_size: 25 }),
    enabled: patientId !== "",
  });

  const archiveMutation = useMutation({
    mutationFn: () => patientsDestroy(patientId),
    onSuccess: () => {
      toast({ title: "Patient archived", description: "The record is now read-only." });
      void queryClient.invalidateQueries({ queryKey: ["patients"] });
      navigate("/app/patients");
    },
    onError: (error) => setArchiveError(apiErrorMessage(error)),
  });

  const patient = patientQuery.data;
  const displayName = patient?.full_name || `${patient?.first_name ?? ""} ${patient?.last_name ?? ""}`.trim();

  return (
    <AppLayout>
      <div className="max-w-screen-2xl mx-auto">
        <header className="flex flex-wrap gap-4 justify-between items-end mb-8">
          <div>
            <Link href="/app/patients" className="inline-flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest text-muted-foreground hover:text-primary transition-colors mb-2">
              <ArrowLeft size={12} /> Patients
            </Link>
            <h2 className="text-4xl font-extrabold tracking-tighter text-foreground font-display">
              {displayName || "Patient record"}
            </h2>
            {patient ? (
              <p className="text-primary font-bold text-sm mt-1">
                #{patient.number} · <span className="uppercase">{patient.status}</span>
              </p>
            ) : null}
          </div>
          <button
            type="button"
            data-testid="archive-patient-btn"
            disabled={archiveMutation.isPending || patient?.status === "archived"}
            onClick={() => {
              if (window.confirm("Archive this patient record? The chart becomes read-only.")) {
                archiveMutation.mutate();
              }
            }}
            className="flex items-center gap-2 px-6 py-3 rounded-full border border-destructive/30 text-destructive font-bold text-xs hover:bg-destructive/10 transition-colors disabled:opacity-40"
          >
            <Archive size={14} />
            {archiveMutation.isPending ? "Archiving…" : "Archive patient"}
          </button>
        </header>

        {archiveError ? (
          <div role="alert" className="mb-6 rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {archiveError}
          </div>
        ) : null}

        <DataState
          loading={patientQuery.isPending}
          error={patientQuery.error}
          onRetry={() => void patientQuery.refetch()}
        >
          {patient ? (
            <div className="grid grid-cols-1 xl:grid-cols-12 gap-8">
              <div className="xl:col-span-4 space-y-8">
                <Section title="Demographics">
                  <div className="flex items-center gap-6 mb-8">
                    <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-primary/20 to-primary-dim/10 flex items-center justify-center text-primary text-2xl font-black font-display shadow-sm">
                      {initials(displayName)}
                    </div>
                    <div className="min-w-0">
                      <h3 className="text-2xl font-bold tracking-tight font-display text-foreground truncate">{displayName}</h3>
                      {patient.preferred_name ? (
                        <p className="text-primary font-bold text-sm">“{patient.preferred_name}”</p>
                      ) : null}
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <p className="text-[10px] uppercase font-bold text-muted-foreground mb-1">Date of birth</p>
                      <p className="text-foreground font-bold text-sm">{formatDate(patient.date_of_birth)}</p>
                    </div>
                    <div>
                      <p className="text-[10px] uppercase font-bold text-muted-foreground mb-1">Sex at birth</p>
                      <p className="text-foreground font-bold text-sm">{patient.sex_at_birth}</p>
                    </div>
                    <div>
                      <p className="text-[10px] uppercase font-bold text-muted-foreground mb-1">Preferred contact</p>
                      <p className="text-foreground font-bold text-sm">{patient.preferred_contact}</p>
                    </div>
                    <div>
                      <p className="text-[10px] uppercase font-bold text-muted-foreground mb-1">Primary location</p>
                      <p className="text-foreground font-bold text-sm">{patient.primary_location?.name ?? "—"}</p>
                    </div>
                  </div>
                  <div className="pt-6 mt-6 border-t border-border space-y-3">
                    {patient.email ? (
                      <div className="flex items-center gap-3 text-sm text-foreground font-medium">
                        <Mail size={16} className="text-muted-foreground" /> {patient.email}
                      </div>
                    ) : null}
                    {patient.phone_e164 ? (
                      <div className="flex items-center gap-3 text-sm text-foreground font-medium">
                        <Phone size={16} className="text-muted-foreground" /> {patient.phone_e164}
                      </div>
                    ) : null}
                  </div>
                  {patient.notes ? (
                    <div className="pt-6 mt-6 border-t border-border">
                      <p className="text-[10px] uppercase font-bold text-muted-foreground mb-2">Notes</p>
                      <p className="text-sm text-muted-foreground whitespace-pre-wrap">{patient.notes}</p>
                    </div>
                  ) : null}
                </Section>

                <Section title="Allergies">
                  <DataState
                    loading={allergiesQuery.isPending}
                    error={allergiesQuery.error}
                    empty={(allergiesQuery.data?.results ?? []).length === 0}
                    emptyTitle="No allergies recorded"
                    emptyMessage="No allergy has been entered for this patient."
                    onRetry={() => void allergiesQuery.refetch()}
                  >
                    <div className="flex flex-wrap gap-2">
                      {(allergiesQuery.data?.results ?? []).map((allergy) => (
                        <span
                          key={allergy.id}
                          className={`px-3 py-1.5 rounded-lg text-xs font-bold border ${
                            allergy.severity === "severe" || allergy.severity === "anaphylaxis"
                              ? "bg-destructive/10 text-destructive border-destructive/20"
                              : "bg-muted text-foreground border-transparent"
                          }`}
                          title={allergy.reaction ?? undefined}
                        >
                          {allergy.substance}
                          {allergy.severity ? ` · ${allergy.severity}` : ""}
                        </span>
                      ))}
                    </div>
                  </DataState>
                </Section>

                <Section title="Medical history">
                  <DataState
                    loading={historyQuery.isPending}
                    error={historyQuery.error}
                    empty={(historyQuery.data?.results ?? []).length === 0}
                    emptyTitle="No medical history"
                    emptyMessage="No condition, surgery or medication is recorded."
                    onRetry={() => void historyQuery.refetch()}
                  >
                    <div className="space-y-3">
                      {(historyQuery.data?.results ?? []).map((entry) => (
                        <div key={entry.id} className="p-3 bg-muted/50 rounded-xl">
                          <p className="text-sm font-bold text-foreground">{entry.label}</p>
                          <p className="text-[11px] text-muted-foreground">
                            {entry.kind}
                            {entry.since ? ` · since ${formatDate(entry.since)}` : ""}
                          </p>
                          {entry.notes ? <p className="text-xs text-muted-foreground mt-1">{entry.notes}</p> : null}
                        </div>
                      ))}
                    </div>
                  </DataState>
                </Section>
              </div>

              <div className="xl:col-span-8 space-y-8">
                <Section title="Encounters">
                  <DataState
                    loading={encountersQuery.isPending}
                    error={encountersQuery.error}
                    empty={(encountersQuery.data?.results ?? []).length === 0}
                    emptyTitle="No encounters"
                    emptyMessage="No clinical encounter has been recorded for this patient."
                    onRetry={() => void encountersQuery.refetch()}
                  >
                    <div className="space-y-3">
                      {(encountersQuery.data?.results ?? []).map((encounter) => (
                        <div key={encounter.id} className="flex items-start gap-4 p-4 bg-background border border-border rounded-xl">
                          <div className="w-10 h-10 rounded-xl bg-primary/10 text-primary flex items-center justify-center shrink-0">
                            <Activity size={16} />
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="flex justify-between items-center gap-3">
                              <p className="text-sm font-bold text-foreground truncate">
                                {encounter.chief_complaint || encounter.kind || "Encounter"}
                              </p>
                              <span className="text-xs font-bold text-muted-foreground shrink-0">
                                {formatDate(encounter.occurred_at)}
                              </span>
                            </div>
                            <p className="text-[11px] text-muted-foreground mt-1 uppercase tracking-wide">
                              {encounter.kind} · {encounter.status}
                            </p>
                            {encounter.notes ? (
                              <p className="text-xs text-muted-foreground mt-2 whitespace-pre-wrap">{encounter.notes}</p>
                            ) : null}
                          </div>
                        </div>
                      ))}
                    </div>
                  </DataState>
                </Section>

                <Section title="Clinical timeline">
                  <DataState
                    loading={timelineQuery.isPending}
                    error={timelineQuery.error}
                    empty={(timelineQuery.data?.results ?? []).length === 0}
                    emptyTitle="No timeline events"
                    emptyMessage="Nothing has happened on this record yet."
                    onRetry={() => void timelineQuery.refetch()}
                  >
                    <div className="space-y-6 relative before:absolute before:inset-0 before:ml-5 before:-translate-x-px before:w-0.5 before:bg-border">
                      {(timelineQuery.data?.results ?? []).map((event: TimelineEvent) => (
                        <div key={`${event.type}-${event.entity_id}-${event.at}`} className="relative flex items-center gap-6">
                          <div className="w-10 h-10 rounded-full flex items-center justify-center border z-10 text-foreground bg-muted border-border shrink-0">
                            <Activity size={18} />
                          </div>
                          <div className="flex-1 bg-background border border-border p-5 rounded-xl">
                            <div className="flex justify-between items-center gap-3 mb-2">
                              <h4 className="font-bold text-foreground">{event.title}</h4>
                              <span className="text-xs font-bold text-muted-foreground shrink-0">
                                {formatDateTime(event.at)}
                              </span>
                            </div>
                            <p className="text-sm text-muted-foreground">{event.summary}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </DataState>
                </Section>
              </div>
            </div>
          ) : null}
        </DataState>
      </div>
    </AppLayout>
  );
}
