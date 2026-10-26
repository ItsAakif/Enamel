import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { History, Save, Trash2 } from "lucide-react";
import {
  patientsChartBatchCreate,
  patientsChartHistoryRetrieve,
  patientsChartRetrieve,
  patientsList,
  type ChartBatchChange,
  type ChartHistoryEvent,
  type ChartTooth,
  type ConditionLegend,
  type PatientList,
  type PatientsChartRetrieveParams,
  type PatientsListParams,
  type ToothState,
} from "@workspace/api-client-react";
import AppLayout from "@/layouts/AppLayout";
import { DataState } from "@/components/common/DataState";
import { Odontogram } from "@/components/charting/Odontogram";
import { useToast } from "@/hooks/use-toast";
import {
  apiErrorCode,
  apiErrorFieldErrors,
  apiErrorMessage,
  newIdempotencyKey,
  toPage,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";

const STATUS_VALUES = ["existing", "planned", "referred", "completed"] as const;
const ANTERIOR_SURFACES = ["M", "D", "B", "L", "I"] as const;
const POSTERIOR_SURFACES = ["M", "O", "D", "B", "L"] as const;

const INPUT_CLASS =
  "w-full border border-border rounded-xl px-4 py-3 text-sm font-medium bg-white/70 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all";

interface ToothForm {
  stateId: string | null;
  version: number | null;
  conditionCode: string;
  status: string;
  surfaces: string[];
  severity: string;
  note: string;
}

const EMPTY_FORM: ToothForm = {
  stateId: null,
  version: null,
  conditionCode: "",
  status: "existing",
  surfaces: [],
  severity: "",
  note: "",
};

function stateChipClass(state: ToothState): string {
  if (state.status === "planned") return "bg-primary/10 text-primary border-primary/20";
  if (state.status === "completed") return "bg-emerald-50 text-emerald-700 border-emerald-200";
  if (state.status === "referred") return "bg-amber-50 text-amber-700 border-amber-200";
  return "bg-muted text-foreground border-transparent";
}

export default function ChartingPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [patientId, setPatientId] = useState<string | null>(null);
  const [dentition, setDentition] = useState("");
  const [selectedTooth, setSelectedTooth] = useState<number | null>(null);
  const [form, setForm] = useState<ToothForm>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});

  const patientsQuery = useQuery({
    queryKey: ["patients", "chart-picker"],
    queryFn: async () =>
      toPage<PatientList>(await patientsList({ page_size: 100 } as PatientsListParams)).results,
  });

  useEffect(() => {
    if (patientId === null && patientsQuery.data && patientsQuery.data.length > 0) {
      setPatientId(patientsQuery.data[0].id);
    }
  }, [patientId, patientsQuery.data]);

  const chartParams: PatientsChartRetrieveParams | undefined = dentition
    ? { dentition: dentition as PatientsChartRetrieveParams["dentition"] }
    : undefined;

  const chartQuery = useQuery({
    queryKey: ["patients", patientId, "chart", dentition],
    queryFn: () => patientsChartRetrieve(patientId as string, chartParams),
    enabled: Boolean(patientId),
  });

  const historyQuery = useQuery({
    queryKey: ["patients", patientId, "chart-history", selectedTooth],
    queryFn: () =>
      patientsChartHistoryRetrieve(patientId as string, {
        tooth: selectedTooth ?? undefined,
        page_size: 20,
      }),
    enabled: Boolean(patientId),
  });

  const batchMutation = useMutation({
    mutationFn: (change: ChartBatchChange) =>
      patientsChartBatchCreate(patientId as string, {
        changes: [change],
        idempotency_key: newIdempotencyKey(),
        dentition: dentition ? (dentition as "mixed" | "permanent" | "primary") : undefined,
        force: true,
      }),
  });

  const teeth: ChartTooth[] = chartQuery.data?.teeth ?? [];
  const legend: ConditionLegend[] = chartQuery.data?.legend ?? [];
  const counts = chartQuery.data?.counts;

  const selectedToothData = useMemo(
    () => teeth.find((tooth) => tooth.tooth === selectedTooth) ?? null,
    [teeth, selectedTooth],
  );

  const surfaceOptions = selectedToothData?.info.is_anterior ? ANTERIOR_SURFACES : POSTERIOR_SURFACES;

  const history: ChartHistoryEvent[] = historyQuery.data?.results ?? [];

  function resetForm() {
    setForm(EMPTY_FORM);
    setFieldErrors({});
    setFormError(null);
  }

  function selectTooth(tooth: number) {
    setSelectedTooth(tooth);
    resetForm();
  }

  function editState(state: ToothState) {
    setForm({
      stateId: state.id,
      version: state.version,
      conditionCode: state.condition_code,
      status: state.status,
      surfaces: [...state.surfaces],
      severity: state.severity === null ? "" : String(state.severity),
      note: state.note,
    });
    setFieldErrors({});
    setFormError(null);
  }

  async function saveChange() {
    if (!patientId || selectedTooth === null) return;
    setFormError(null);
    setFieldErrors({});
    const change: ChartBatchChange = {
      tooth: selectedTooth,
      condition_code: form.conditionCode,
      status: form.status as ChartBatchChange["status"],
      surfaces: form.surfaces,
      note: form.note,
      ...(form.severity.trim() === "" ? {} : { severity: Number(form.severity) }),
      ...(form.version === null ? {} : { version: form.version }),
    };
    try {
      await batchMutation.mutateAsync(change);
      toast({ title: "Chart saved", description: `Tooth ${selectedTooth} updated.` });
      resetForm();
      await Promise.all([chartQuery.refetch(), historyQuery.refetch()]);
    } catch (error) {
      if (apiErrorCode(error) === "version_conflict") {
        setFormError("This tooth changed since you loaded it. The latest state has been reloaded.");
        toast({
          title: "Version conflict",
          description: "The chart was reloaded with the current state. Re-apply your change.",
        });
        resetForm();
        await chartQuery.refetch();
        return;
      }
      setFieldErrors(apiErrorFieldErrors(error));
      setFormError(apiErrorMessage(error));
    }
  }

  async function archiveState(state: ToothState) {
    if (!patientId) return;
    try {
      await batchMutation.mutateAsync({
        tooth: state.tooth,
        condition_code: state.condition_code,
        archive: true,
        version: state.version,
      });
      toast({ title: "State archived", description: `${state.condition_code} removed from tooth ${state.tooth}.` });
      resetForm();
      await Promise.all([chartQuery.refetch(), historyQuery.refetch()]);
    } catch (error) {
      setFormError(apiErrorMessage(error));
    }
  }

  const flattenedFieldErrors = Object.values(fieldErrors).flat();

  return (
    <AppLayout>
      <div className="max-w-screen-2xl mx-auto">
        <header className="flex flex-wrap gap-4 justify-between items-end mb-8">
          <div>
            <span className="text-xs font-bold uppercase tracking-[0.2em] text-primary mb-2 block font-sans">
              Clinical Input
            </span>
            <h2 className="text-4xl font-extrabold text-foreground font-display leading-none">Odontogram</h2>
          </div>
          <div className="flex flex-wrap gap-3">
            <label className="sr-only" htmlFor="chart-patient">
              Patient
            </label>
            <select
              id="chart-patient"
              data-testid="chart-patient"
              value={patientId ?? ""}
              onChange={(event) => {
                setPatientId(event.target.value);
                setSelectedTooth(null);
                resetForm();
              }}
              className={`${INPUT_CLASS} w-auto`}
            >
              <option value="" disabled>
                Select patient
              </option>
              {(patientsQuery.data ?? []).map((patient) => (
                <option key={patient.id} value={patient.id}>
                  {patient.full_name || `${patient.first_name} ${patient.last_name}`}
                </option>
              ))}
            </select>
            <label className="sr-only" htmlFor="chart-dentition">
              Dentition
            </label>
            <select
              id="chart-dentition"
              data-testid="chart-dentition"
              value={dentition}
              onChange={(event) => setDentition(event.target.value)}
              className={`${INPUT_CLASS} w-auto`}
            >
              <option value="">Derived dentition</option>
              <option value="permanent">Permanent (32)</option>
              <option value="mixed">Mixed (all 52)</option>
              <option value="primary">Primary (20)</option>
            </select>
          </div>
        </header>

        {patientsQuery.isSuccess && patientsQuery.data.length === 0 ? (
          <DataState
            empty
            emptyTitle="No patients to chart"
            emptyMessage="Create a patient before opening an odontogram."
          >
            <div />
          </DataState>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
            <div className="lg:col-span-2 space-y-6">
              <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="glass-card rounded-[2rem] p-10 shadow-sm">
                <div className="flex justify-between items-center mb-8">
                  <h3 className="text-xl font-bold font-display">Interactive Chart</h3>
                  {chartQuery.data ? (
                    <span className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                      {chartQuery.data.dentition} · {chartQuery.data.dentition_source}
                    </span>
                  ) : null}
                </div>

                <DataState
                  loading={chartQuery.isPending && Boolean(patientId)}
                  error={chartQuery.error}
                  onRetry={() => void chartQuery.refetch()}
                >
                  <Odontogram teeth={teeth} selectedTooth={selectedTooth} onSelect={selectTooth} />
                </DataState>

                <div className="mt-10 pt-8 border-t border-border">
                  <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground mb-4">Legend</p>
                  <div className="flex flex-wrap gap-x-6 gap-y-2">
                    {legend.map((entry) => (
                      <span key={entry.code} className="text-xs font-bold text-muted-foreground" title={entry.category}>
                        {entry.label}
                      </span>
                    ))}
                  </div>
                </div>
              </motion.div>
            </div>

            <div className="lg:col-span-1 space-y-6">
              <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} className="glass-card rounded-[2rem] p-8 shadow-sm">
                <h3 className="text-xl font-bold font-display mb-6">Condition Entry</h3>

                {selectedToothData ? (
                  <div className="space-y-5">
                    <div>
                      <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground mb-2">
                        Selected tooth
                      </p>
                      <div className="bg-muted px-4 py-3 rounded-xl font-bold font-display text-sm">
                        #{selectedToothData.tooth} · {selectedToothData.info.label}
                      </div>
                    </div>

                    {selectedToothData.states.length > 0 ? (
                      <div>
                        <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground mb-2">
                          Recorded states
                        </p>
                        <div className="flex flex-wrap gap-2">
                          {selectedToothData.states.map((state) => (
                            <span
                              key={state.id}
                              className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-bold border ${stateChipClass(state)}`}
                            >
                              <button type="button" onClick={() => editState(state)} className="hover:underline">
                                {state.condition_code}
                                {state.surfaces.length > 0 ? ` (${state.surfaces.join("")})` : ""}
                              </button>
                              <button
                                type="button"
                                onClick={() => void archiveState(state)}
                                aria-label={`Archive ${state.condition_code}`}
                                className="text-current/70 hover:text-destructive"
                              >
                                <Trash2 size={12} />
                              </button>
                            </span>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {formError ? (
                      <div role="alert" className="rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-xs text-destructive">
                        {formError}
                      </div>
                    ) : null}
                    {flattenedFieldErrors.length > 0 ? (
                      <ul className="list-disc pl-5 text-xs text-destructive space-y-1">
                        {flattenedFieldErrors.map((message) => (
                          <li key={message}>{message}</li>
                        ))}
                      </ul>
                    ) : null}

                    <div>
                      <label className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground block mb-2">
                        Condition
                      </label>
                      <select
                        data-testid="chart-condition"
                        value={form.conditionCode}
                        onChange={(event) => setForm((s) => ({ ...s, conditionCode: event.target.value }))}
                        className={INPUT_CLASS}
                      >
                        <option value="">Select condition</option>
                        {legend.map((entry) => (
                          <option key={entry.code} value={entry.code}>
                            {entry.label}
                          </option>
                        ))}
                      </select>
                    </div>

                    <div>
                      <label className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground block mb-2">
                        Status
                      </label>
                      <select
                        data-testid="chart-status"
                        value={form.status}
                        onChange={(event) => setForm((s) => ({ ...s, status: event.target.value }))}
                        className={INPUT_CLASS}
                      >
                        {STATUS_VALUES.map((value) => (
                          <option key={value} value={value}>
                            {value}
                          </option>
                        ))}
                      </select>
                    </div>

                    <div>
                      <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground mb-2">
                        Surfaces
                      </p>
                      <div className="flex flex-wrap gap-2">
                        {surfaceOptions.map((surface) => {
                          const active = form.surfaces.includes(surface);
                          return (
                            <button
                              key={surface}
                              type="button"
                              aria-pressed={active}
                              onClick={() =>
                                setForm((s) => ({
                                  ...s,
                                  surfaces: active
                                    ? s.surfaces.filter((value) => value !== surface)
                                    : [...s.surfaces, surface],
                                }))
                              }
                              className={`min-w-[44px] min-h-[44px] rounded-xl text-xs font-bold border transition-colors ${
                                active
                                  ? "bg-primary text-white border-primary"
                                  : "border-border text-foreground hover:border-primary/50"
                              }`}
                            >
                              {surface}
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground block mb-2">
                          Severity (0–9)
                        </label>
                        <input
                          inputMode="numeric"
                          value={form.severity}
                          onChange={(event) => setForm((s) => ({ ...s, severity: event.target.value }))}
                          className={INPUT_CLASS}
                        />
                      </div>
                    </div>

                    <div>
                      <label className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground block mb-2">
                        Notes
                      </label>
                      <textarea
                        rows={3}
                        value={form.note}
                        onChange={(event) => setForm((s) => ({ ...s, note: event.target.value }))}
                        className={`${INPUT_CLASS} resize-none`}
                      />
                    </div>

                    <div className="flex gap-3">
                      <button
                        type="button"
                        onClick={resetForm}
                        className="flex-1 border border-border py-3 rounded-xl font-bold text-xs text-muted-foreground hover:bg-muted transition-colors"
                      >
                        Clear
                      </button>
                      <button
                        type="button"
                        data-testid="chart-save"
                        disabled={form.conditionCode === "" || batchMutation.isPending}
                        onClick={() => void saveChange()}
                        className="flex-1 inline-flex items-center justify-center gap-2 bg-gradient-to-br from-primary to-primary-dim text-white py-3 rounded-xl font-bold text-xs shadow-lg shadow-primary/20 hover:opacity-90 transition-all disabled:opacity-50"
                      >
                        <Save size={14} />
                        {form.stateId ? "Update state" : "Add state"}
                      </button>
                    </div>
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    Select a tooth on the chart to record or edit a condition.
                  </p>
                )}
              </motion.div>

              <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} className="glass-card rounded-[2rem] p-8 shadow-sm">
                <div className="flex items-center gap-2 mb-4">
                  <History size={16} className="text-primary" />
                  <h3 className="text-sm font-bold font-display">History</h3>
                </div>
                {counts ? (
                  <p className="text-[11px] text-muted-foreground mb-4">
                    {counts.total} state{counts.total === 1 ? "" : "s"} across {counts.teeth_with_states} tooth
                    {counts.teeth_with_states === 1 ? "" : "s"}
                  </p>
                ) : null}
                <DataState
                  loading={historyQuery.isPending && Boolean(patientId)}
                  error={historyQuery.error}
                  empty={history.length === 0}
                  emptyTitle="No history"
                  emptyMessage={selectedTooth ? "No events for this tooth." : "No chart events recorded yet."}
                  onRetry={() => void historyQuery.refetch()}
                >
                  <div className="space-y-3">
                    {history.map((event) => (
                      <div key={event.id} className="p-3 bg-muted/50 rounded-xl">
                        <p className="text-xs font-bold text-foreground">
                          {event.event_type}
                          {event.tooth ? ` · #${event.tooth}` : ""}
                        </p>
                        <p className="text-[10px] text-muted-foreground">
                          {formatDateTime(event.occurred_at)} · v{event.version}
                        </p>
                      </div>
                    ))}
                  </div>
                </DataState>
              </motion.div>
            </div>
          </div>
        )}
      </div>
    </AppLayout>
  );
}
