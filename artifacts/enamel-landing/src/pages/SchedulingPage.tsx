import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence } from "framer-motion";
import { ChevronLeft, ChevronRight, PlusCircle, X } from "lucide-react";
import { addDays, format, startOfWeek } from "date-fns";
import {
  appointmentTypesList,
  appointmentsActionCreate,
  appointmentsCreate,
  appointmentsList,
  appointmentsRescheduleCreate,
  availabilityRetrieve,
  clinicsLocationsList,
  operatoriesList,
  patientsList,
  providersList,
  schedulingMetricsRetrieve,
  type Appointment,
  type AppointmentType,
  type AvailabilitySlot,
  type Location,
  type Operatory,
  type PatientList,
  type PatientsListParams,
  type Provider,
} from "@workspace/api-client-react";
import AppLayout from "@/layouts/AppLayout";
import { DataState } from "@/components/common/DataState";
import { CalendarGrid } from "@/components/scheduling/CalendarGrid";
import { SchedulingInsights } from "@/components/scheduling/SchedulingInsights";
import {
  NewAppointmentSlideOver,
  type AppointmentFormOptions,
  type AppointmentFormState,
} from "@/components/scheduling/NewAppointmentSlideOver";
import { useAuth } from "@/hooks/use-auth";
import { apiErrorCode, apiErrorDetails, apiErrorMessage, toPage } from "@/lib/api";
import { formatTime, isoDate, isoLocalDateTime } from "@/lib/format";

const HOURS = Array.from({ length: 11 }, (_, index) => `${String(index + 8).padStart(2, "0")}:00`);

const EMPTY_FORM: AppointmentFormState = {
  patient: "",
  provider: "",
  appointmentType: "",
  location: "",
  operatory: "",
  startAt: "",
  reason: "",
  notes: "",
};

function dayKey(date: Date): string {
  return format(date, "yyyy-MM-dd");
}

export default function SchedulingPage() {
  const { activeClinicId } = useAuth();
  const queryClient = useQueryClient();

  const [viewMode, setViewMode] = useState<"weekly" | "daily">("weekly");
  const [anchor, setAnchor] = useState<Date>(() => new Date());
  const [selectedAppointmentId, setSelectedAppointmentId] = useState<string | null>(null);
  const [slideOverOpen, setSlideOverOpen] = useState(false);
  const [slideOverMode, setSlideOverMode] = useState<"create" | "reschedule">("create");
  const [form, setForm] = useState<AppointmentFormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);

  const rangeStart = viewMode === "weekly" ? startOfWeek(anchor, { weekStartsOn: 1 }) : anchor;
  const days = useMemo(
    () => (viewMode === "weekly" ? Array.from({ length: 7 }, (_, i) => addDays(rangeStart, i)) : [anchor]),
    [viewMode, rangeStart, anchor],
  );
  const from = isoDate(days[0]);
  const to = isoDate(days[days.length - 1]);

  const appointmentsQuery = useQuery({
    queryKey: ["appointments", from, to],
    // The board renders one day at a time, and the API's default page size is
    // 25. A day with more appointments than that would silently lose the tail
    // of the schedule, so the board asks for the maximum page size.
    queryFn: async () =>
      toPage<Appointment>(
        await appointmentsList({ from, to, ordering: "start_at", page_size: 100 }),
      ),
  });

  const metricsQuery = useQuery({
    queryKey: ["scheduling", "metrics", from, to],
    queryFn: () => schedulingMetricsRetrieve({ from, to }),
  });

  const patientsQuery = useQuery({
    queryKey: ["patients", "lookup"],
    queryFn: async () =>
      toPage<PatientList>(await patientsList({ page_size: 100 } as PatientsListParams)).results,
  });

  const providersQuery = useQuery({
    queryKey: ["providers", "lookup"],
    queryFn: async () => toPage<Provider>(await providersList()).results,
  });

  const typesQuery = useQuery({
    queryKey: ["appointment-types", "lookup"],
    queryFn: async () => toPage<AppointmentType>(await appointmentTypesList()).results,
  });

  const operatoriesQuery = useQuery({
    queryKey: ["operatories", "lookup"],
    queryFn: async () => toPage<Operatory>(await operatoriesList()).results,
  });

  const locationsQuery = useQuery({
    queryKey: ["locations", activeClinicId],
    queryFn: async () => toPage<Location>(await clinicsLocationsList(activeClinicId as string)).results,
    enabled: Boolean(activeClinicId),
  });

  const types = typesQuery.data ?? [];
  const defaultTypeId = types[0]?.id ?? "";

  const availabilityQuery = useQuery({
    queryKey: ["availability", defaultTypeId, from, to],
    queryFn: () => availabilityRetrieve({ appointment_type: defaultTypeId, from, to }),
    enabled: defaultTypeId !== "",
  });

  // Prefill the create form from the first available lookups.
  useEffect(() => {
    if (slideOverOpen && slideOverMode === "create") {
      setForm((current) => ({
        ...current,
        appointmentType: current.appointmentType || defaultTypeId,
        location: current.location || (locationsQuery.data?.[0]?.id ?? ""),
        provider: current.provider || (providersQuery.data?.[0]?.id ?? ""),
      }));
    }
  }, [slideOverOpen, slideOverMode, defaultTypeId, locationsQuery.data, providersQuery.data]);

  const patientNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const patient of patientsQuery.data ?? []) {
      map.set(patient.id, patient.full_name || `${patient.first_name} ${patient.last_name}`.trim());
    }
    return map;
  }, [patientsQuery.data]);

  const providerNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const provider of providersQuery.data ?? []) map.set(provider.id, provider.display_name);
    return map;
  }, [providersQuery.data]);

  const typeNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const type of types) map.set(type.id, type.name);
    return map;
  }, [types]);

  const appointments = appointmentsQuery.data?.results ?? [];

  const appointmentsByCell = useMemo(() => {
    const map = new Map<string, Appointment[]>();
    for (const appointment of appointments) {
      const start = new Date(appointment.start_at);
      const key = `${dayKey(start)}:${start.getHours()}`;
      const bucket = map.get(key) ?? [];
      bucket.push(appointment);
      map.set(key, bucket);
    }
    return map;
  }, [appointments]);

  const slots: AvailabilitySlot[] = availabilityQuery.data?.slots ?? [];

  const createMutation = useMutation({
    mutationFn: () => {
      const start = new Date(form.startAt);
      return appointmentsCreate({
        patient: form.patient,
        provider: form.provider,
        appointment_type: form.appointmentType,
        location: form.location,
        operatory: form.operatory || null,
        start_at: start.toISOString(),
        reason: form.reason,
        notes: form.notes,
        source: "staff",
      });
    },
  });

  const rescheduleMutation = useMutation({
    mutationFn: () => {
      const start = new Date(form.startAt);
      return appointmentsRescheduleCreate(selectedAppointmentId as string, {
        start_at: start.toISOString(),
        operatory: form.operatory || null,
        reason: form.reason,
      });
    },
  });

  const cancelMutation = useMutation({
    mutationFn: (appointmentId: string) =>
      appointmentsActionCreate(appointmentId, "cancel", { reason: "Cancelled from scheduling board" }),
  });

  function describeConflict(error: unknown): string {
    if (apiErrorCode(error) === "slot_conflict") {
      const details = apiErrorDetails(error);
      const conflictStart = typeof details.start_at === "string" ? formatTime(details.start_at) : null;
      const conflictEnd = typeof details.end_at === "string" ? formatTime(details.end_at) : null;
      const window = conflictStart && conflictEnd ? ` (${conflictStart}–${conflictEnd})` : "";
      return `That slot is no longer available${window}. Pick another time.`;
    }
    if (apiErrorCode(error) === "hold_expired") {
      return "The previous hold on this slot expired. Pick another time.";
    }
    return apiErrorMessage(error);
  }

  async function refreshSchedule() {
    await queryClient.invalidateQueries({ queryKey: ["appointments"] });
    await appointmentsQuery.refetch();
    await metricsQuery.refetch();
  }

  async function submitSlideOver() {
    setFormError(null);
    try {
      if (slideOverMode === "create") {
        await createMutation.mutateAsync();
      } else {
        await rescheduleMutation.mutateAsync();
      }
      setSlideOverOpen(false);
      setForm(EMPTY_FORM);
      setSelectedAppointmentId(null);
      await refreshSchedule();
    } catch (error) {
      setFormError(describeConflict(error));
      if (apiErrorCode(error) === "slot_conflict" || apiErrorCode(error) === "hold_expired") {
        await refreshSchedule();
      }
    }
  }

  async function cancelAppointment(appointmentId: string) {
    setFormError(null);
    try {
      await cancelMutation.mutateAsync(appointmentId);
      setSelectedAppointmentId(null);
      await refreshSchedule();
    } catch (error) {
      setFormError(apiErrorMessage(error));
    }
  }

  function openCreate(date: Date, hour: number) {
    const start = new Date(date);
    start.setHours(hour, 0, 0, 0);
    setForm({ ...EMPTY_FORM, startAt: isoLocalDateTime(start) });
    setSlideOverMode("create");
    setFormError(null);
    setSlideOverOpen(true);
  }

  function openReschedule(appointment: Appointment) {
    const start = new Date(appointment.start_at);
    setForm({
      ...EMPTY_FORM,
      patient: appointment.patient,
      provider: appointment.provider,
      appointmentType: appointment.appointment_type,
      location: appointment.location,
      operatory: appointment.operatory ?? "",
      startAt: isoLocalDateTime(start),
      reason: appointment.reason,
      notes: appointment.notes,
    });
    setSlideOverMode("reschedule");
    setFormError(null);
    setSlideOverOpen(true);
  }

  const formOptions: AppointmentFormOptions = {
    patients: (patientsQuery.data ?? []).map((patient) => ({
      id: patient.id,
      label: patient.full_name || `${patient.first_name} ${patient.last_name}`.trim(),
    })),
    providers: (providersQuery.data ?? []).map((provider) => ({ id: provider.id, label: provider.display_name })),
    appointmentTypes: types.map((type) => ({
      id: type.id,
      label: `${type.name} (${type.duration_min ?? 30} min)`,
    })),
    locations: (locationsQuery.data ?? []).map((location) => ({ id: location.id, label: location.name })),
    operatories: (operatoriesQuery.data ?? []).map((operatory) => ({ id: operatory.id, label: operatory.name })),
  };

  const selectedAppointment = appointments.find((appointment) => appointment.id === selectedAppointmentId) ?? null;

  const shift = (direction: number) => {
    setAnchor((current) => addDays(current, direction * (viewMode === "weekly" ? 7 : 1)));
    setSelectedAppointmentId(null);
  };

  return (
    <AppLayout>
      <div className="max-w-screen-2xl mx-auto space-y-6">
        <header className="flex flex-wrap gap-4 justify-between items-end">
          <div>
            <span className="text-[10px] font-bold tracking-[0.2em] text-primary uppercase mb-2 block font-sans">
              Clinical Workflow
            </span>
            <h2 className="text-4xl font-extrabold text-foreground font-display leading-none">Scheduling Center</h2>
          </div>
          <div className="flex gap-3 items-center flex-wrap">
            <div className="flex bg-muted p-1 rounded-full">
              <button
                type="button"
                data-testid="view-daily"
                onClick={() => setViewMode("daily")}
                className={`px-5 py-2 rounded-full text-xs font-bold transition-all ${
                  viewMode === "daily" ? "bg-background shadow-sm text-primary" : "text-muted-foreground hover:text-foreground"
                }`}
              >
                Daily
              </button>
              <button
                type="button"
                data-testid="view-weekly"
                onClick={() => setViewMode("weekly")}
                className={`px-5 py-2 rounded-full text-xs font-bold transition-all ${
                  viewMode === "weekly" ? "bg-background shadow-sm text-primary" : "text-muted-foreground hover:text-foreground"
                }`}
              >
                Weekly
              </button>
            </div>
            <div className="flex items-center gap-2 border border-border rounded-full px-4 py-2">
              <button type="button" data-testid="prev-range" onClick={() => shift(-1)} className="hover:text-primary transition-colors" aria-label="Previous">
                <ChevronLeft size={16} />
              </button>
              <span className="text-xs font-bold text-foreground font-sans">
                {viewMode === "weekly"
                  ? `${format(days[0], "d MMM")} – ${format(days[days.length - 1], "d MMM")}`
                  : format(anchor, "EEE, d MMM")}
              </span>
              <button type="button" data-testid="next-range" onClick={() => shift(1)} className="hover:text-primary transition-colors" aria-label="Next">
                <ChevronRight size={16} />
              </button>
            </div>
            <button
              type="button"
              data-testid="new-appointment-btn"
              onClick={() => openCreate(anchor, 9)}
              className="flex items-center gap-2 bg-gradient-to-br from-primary to-primary-dim text-white px-7 py-3 rounded-full font-display font-bold text-sm shadow-lg shadow-primary/20 hover:opacity-90 active:scale-95 transition-all"
            >
              <PlusCircle size={16} />
              New Appointment
            </button>
          </div>
        </header>

        <div className="flex gap-4 flex-wrap">
          {(providersQuery.data ?? []).map((provider) => (
            <div key={provider.id} className="flex items-center gap-2 glass-chip rounded-full px-4 py-2 shadow-sm">
              <div
                className="w-2 h-2 rounded-full"
                style={{ background: provider.color || "#4647d3" }}
                aria-hidden="true"
              />
              <span className="text-xs font-bold text-foreground">{provider.display_name}</span>
              {provider.specialty ? (
                <span className="text-[10px] text-muted-foreground font-medium">{provider.specialty}</span>
              ) : null}
            </div>
          ))}
        </div>

        {formError ? (
          <div role="alert" className="rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {formError}
          </div>
        ) : null}

        <DataState
          loading={appointmentsQuery.isPending}
          error={appointmentsQuery.error}
          onRetry={() => void appointmentsQuery.refetch()}
        >
          <AnimatePresence mode="wait">
            {viewMode === "weekly" ? (
              <CalendarGrid
                key="weekly"
                columns="grid-cols-[72px_repeat(7,minmax(0,1fr))]"
                rowMinHeight="min-h-[64px]"
                gutterClassName="p-3 text-right"
                hours={HOURS}
                header={days.map((day) => {
                  const isToday = dayKey(day) === dayKey(new Date());
                  return (
                    <div key={dayKey(day)} className={`p-3 text-center border-l border-border ${isToday ? "bg-primary/5" : ""}`}>
                      <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground">
                        {format(day, "EEE")}
                      </p>
                      <p className={`text-lg font-black font-display mt-0.5 ${isToday ? "text-primary" : "text-foreground"}`}>
                        {format(day, "d")}
                      </p>
                    </div>
                  );
                })}
                renderCells={(rowIdx) =>
                  days.map((day) => {
                    const hour = rowIdx + 8;
                    const cellAppointments = appointmentsByCell.get(`${dayKey(day)}:${hour}`) ?? [];
                    return (
                      <div key={`${dayKey(day)}-${hour}`} className="border-l border-border/50 relative p-1.5">
                        <button
                          type="button"
                          onClick={() => openCreate(day, hour)}
                          aria-label={`Add appointment ${format(day, "d MMM")} ${hour}:00`}
                          className="absolute inset-0 opacity-0 hover:opacity-100 focus:opacity-100 transition-opacity flex items-center justify-center"
                        >
                          <span className="text-[10px] font-bold text-muted-foreground">+ Add</span>
                        </button>
                        {cellAppointments.map((appointment) => (
                          <button
                            key={appointment.id}
                            type="button"
                            onClick={() => setSelectedAppointmentId(appointment.id)}
                            className="relative z-10 w-full text-left rounded-lg px-2.5 py-1.5 bg-primary/10 border-l-4 border-primary text-primary hover:brightness-95 transition-all"
                          >
                            <p className="text-[11px] font-bold truncate">
                              {patientNames.get(appointment.patient) ?? "Patient"}
                            </p>
                            <p className="text-[10px] opacity-70 truncate">
                              {typeNames.get(appointment.appointment_type) ?? (appointment.reason || "Appointment")}
                            </p>
                          </button>
                        ))}
                      </div>
                    );
                  })
                }
              />
            ) : (
              <CalendarGrid
                key="daily"
                columns="grid-cols-[72px_1fr]"
                rowMinHeight="min-h-[72px]"
                gutterClassName="p-3 text-right border-r border-border/50"
                hours={HOURS}
                header={
                  <div className="p-4 text-center border-l border-border bg-primary/5">
                    <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground">
                      {format(anchor, "EEEE")}
                    </p>
                    <p className="text-xl font-black font-display mt-0.5 text-primary">{format(anchor, "d")}</p>
                    <p className="text-[10px] text-muted-foreground mt-0.5">
                      {appointments.length} appointment{appointments.length === 1 ? "" : "s"}
                    </p>
                  </div>
                }
                renderCells={(rowIdx) => {
                  const hour = rowIdx + 8;
                  const cellAppointments = appointmentsByCell.get(`${dayKey(anchor)}:${hour}`) ?? [];
                  return (
                    <div className="relative p-2">
                      <button
                        type="button"
                        onClick={() => openCreate(anchor, hour)}
                        aria-label={`Add appointment at ${hour}:00`}
                        className="absolute inset-0 opacity-0 hover:opacity-100 focus:opacity-100 transition-opacity flex items-center justify-center"
                      >
                        <span className="text-[10px] font-bold text-muted-foreground">+ Add appointment</span>
                      </button>
                      {cellAppointments.map((appointment) => (
                        <button
                          key={appointment.id}
                          type="button"
                          onClick={() => setSelectedAppointmentId(appointment.id)}
                          className="relative z-10 w-full text-left rounded-xl px-4 py-3 bg-primary/10 border-l-4 border-primary text-primary hover:brightness-95 transition-all"
                        >
                          <p className="text-sm font-bold">
                            {patientNames.get(appointment.patient) ?? "Patient"}
                          </p>
                          <p className="text-xs opacity-70 mt-0.5">
                            {typeNames.get(appointment.appointment_type) ?? (appointment.reason || "Appointment")}
                          </p>
                          <p className="text-[10px] opacity-60 mt-1 font-bold">
                            {providerNames.get(appointment.provider) ?? "Unassigned"} ·{" "}
                            {formatTime(appointment.start_at)}–{formatTime(appointment.end_at)}
                          </p>
                        </button>
                      ))}
                    </div>
                  );
                }}
              />
            )}
          </AnimatePresence>
        </DataState>

        {viewMode === "daily" ? (
          <div className="glass-card rounded-2xl p-6 shadow-sm">
            <div className="flex justify-between items-center mb-4">
              <h4 className="text-sm font-bold font-display">Open slots</h4>
              <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground">
                {types[0]?.name ?? "No bookable type"}
              </span>
            </div>
            <DataState
              loading={availabilityQuery.isPending && defaultTypeId !== ""}
              error={availabilityQuery.error}
              empty={slots.length === 0}
              emptyTitle="No availability"
              emptyMessage="No open slots for the selected appointment type in this window."
              onRetry={() => void availabilityQuery.refetch()}
            >
              <div className="flex flex-wrap gap-2">
                {slots.slice(0, 24).map((slot) => (
                  <span key={`${slot.start}-${slot.provider ?? ""}`} className="text-xs font-bold text-foreground bg-muted px-3 py-2 rounded-xl">
                    {formatTime(slot.start)}–{formatTime(slot.end)}
                    {slot.provider_name ? ` · ${slot.provider_name}` : ""}
                  </span>
                ))}
              </div>
            </DataState>
          </div>
        ) : null}

        <AnimatePresence>
          {selectedAppointment ? (
            <div className="glass-card rounded-2xl p-6 shadow-sm" data-testid="appointment-detail">
              <div className="flex justify-between items-start">
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground mb-1">
                    Appointment
                  </p>
                  <h4 className="text-lg font-bold font-display">
                    {patientNames.get(selectedAppointment.patient) ?? "Patient"}
                  </h4>
                  <p className="text-xs text-muted-foreground mt-1">
                    {typeNames.get(selectedAppointment.appointment_type) ?? "Appointment"} ·{" "}
                    {providerNames.get(selectedAppointment.provider) ?? "Unassigned"}
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    {formatTime(selectedAppointment.start_at)}–{formatTime(selectedAppointment.end_at)} ·{" "}
                    <span className="uppercase font-bold">{selectedAppointment.status.replace(/_/g, " ")}</span>
                  </p>
                  {selectedAppointment.reason ? (
                    <p className="text-xs text-muted-foreground mt-2">{selectedAppointment.reason}</p>
                  ) : null}
                </div>
                <button
                  type="button"
                  onClick={() => setSelectedAppointmentId(null)}
                  className="p-2 text-muted-foreground hover:bg-muted rounded-lg transition-colors"
                  aria-label="Close appointment detail"
                >
                  <X size={16} />
                </button>
              </div>
              <div className="flex gap-3 mt-5">
                <button
                  type="button"
                  onClick={() => openReschedule(selectedAppointment)}
                  className="px-5 py-2.5 rounded-full bg-foreground text-background font-bold text-xs hover:opacity-90 transition-opacity"
                >
                  Reschedule
                </button>
                <button
                  type="button"
                  disabled={cancelMutation.isPending || selectedAppointment.status === "cancelled"}
                  onClick={() => void cancelAppointment(selectedAppointment.id)}
                  className="px-5 py-2.5 rounded-full border border-destructive/30 text-destructive font-bold text-xs hover:bg-destructive/10 transition-colors disabled:opacity-40"
                >
                  {cancelMutation.isPending ? "Cancelling…" : "Cancel appointment"}
                </button>
              </div>
            </div>
          ) : null}
        </AnimatePresence>

        <SchedulingInsights
          metrics={metricsQuery.data}
          loading={metricsQuery.isPending}
          error={metricsQuery.error}
          onRetry={() => void metricsQuery.refetch()}
        />
      </div>

      <NewAppointmentSlideOver
        open={slideOverOpen}
        mode={slideOverMode}
        onClose={() => {
          setSlideOverOpen(false);
          setFormError(null);
        }}
        formState={form}
        setFormState={setForm}
        options={formOptions}
        onSubmit={() => void submitSlideOver()}
        submitting={createMutation.isPending || rescheduleMutation.isPending}
        error={formError}
      />
    </AppLayout>
  );
}
