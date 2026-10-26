import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as Dialog from "@radix-ui/react-dialog";
import { CalendarX2, CalendarClock, X } from "lucide-react";
import {
  portalAppointmentsCancelCreate,
  portalAppointmentsList,
  portalAppointmentsRescheduleCreate,
  type PortalAppointment,
} from "@workspace/api-client-react";
import PortalLayout from "@/layouts/PortalLayout";
import { PORTAL_REQUEST } from "@/hooks/use-portal-auth";
import { apiErrorCode, apiErrorMessage, apiErrorDetails, toPage } from "@/lib/api";
import { formatDateTime, isoLocalDateTime } from "@/lib/format";
import { DataState } from "@/components/common/DataState";
import { Pill } from "@/components/settings/settings-ui";

const PAGE_SIZE = 10;
const TERMINAL_STATUSES = new Set(["completed", "cancelled", "no_show"]);

const STATUS_TONE: Record<string, "success" | "warning" | "neutral" | "danger" | "primary"> = {
  scheduled: "primary",
  confirmed: "success",
  held: "warning",
  in_progress: "primary",
  completed: "neutral",
  cancelled: "danger",
  no_show: "danger",
};

function conflictReason(error: unknown): string {
  const code = apiErrorCode(error);
  const details = apiErrorDetails(error);
  const cutoff = typeof details.cutoff_hours === "number" ? details.cutoff_hours : null;
  if (code === "policy_cutoff") {
    return cutoff !== null
      ? `${apiErrorMessage(error)} (the clinic requires ${cutoff}h notice).`
      : apiErrorMessage(error);
  }
  if (code === "slot_conflict") return "That time is no longer available. Pick another slot.";
  if (code === "invalid_transition") return apiErrorMessage(error);
  return apiErrorMessage(error);
}

export default function PortalAppointmentsPage() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [rowError, setRowError] = useState<{ id: string; message: string } | null>(null);
  const [rescheduleId, setRescheduleId] = useState<string | null>(null);
  const [rescheduleValue, setRescheduleValue] = useState("");
  const [dialogError, setDialogError] = useState<string | null>(null);
  /** The button that opened the dialog; it gets the focus back on close. */
  const rescheduleTrigger = useRef<HTMLButtonElement | null>(null);

  const appointmentsQuery = useQuery({
    queryKey: ["portal", "appointments", page],
    queryFn: () => portalAppointmentsList({ page, page_size: PAGE_SIZE }, PORTAL_REQUEST),
    retry: false,
  });

  const pageData = toPage<PortalAppointment>(appointmentsQuery.data);
  const appointments = pageData.results;

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ["portal", "appointments"] });
  }

  const cancelMutation = useMutation({
    mutationFn: (id: string) => portalAppointmentsCancelCreate(id, PORTAL_REQUEST),
    onSuccess: () => {
      setRowError(null);
      invalidate();
    },
    onError: (error, id) => setRowError({ id, message: conflictReason(error) }),
  });

  const rescheduleMutation = useMutation({
    mutationFn: ({ id, startAt }: { id: string; startAt: string }) =>
      portalAppointmentsRescheduleCreate(id, { start_at: startAt }, PORTAL_REQUEST),
    onSuccess: () => {
      setRowError(null);
      closeReschedule();
      invalidate();
    },
    onError: (error) => setDialogError(conflictReason(error)),
  });

  function closeReschedule() {
    setRescheduleId(null);
    setRescheduleValue("");
    setDialogError(null);
  }

  function submitReschedule(id: string) {
    if (rescheduleValue === "") {
      setDialogError("Choose a new date and time.");
      return;
    }
    const iso = new Date(rescheduleValue).toISOString();
    rescheduleMutation.mutate({ id, startAt: iso });
  }

  return (
    <PortalLayout>
      <div className="space-y-6">
        <header>
          <h1 className="text-3xl font-extrabold text-foreground font-display leading-tight">Appointments</h1>
          <p className="text-sm text-muted-foreground mt-2">
            Cancel or reschedule within the clinic's policy window. Actions outside the window explain why.
          </p>
        </header>

        <DataState
          loading={appointmentsQuery.isPending}
          error={appointmentsQuery.error}
          empty={appointments.length === 0}
          emptyTitle="No appointments"
          emptyMessage="You have no appointments on file."
          onRetry={() => void appointmentsQuery.refetch()}
        >
          <ul className="space-y-3" data-testid="portal-appointments">
            {appointments.map((appointment) => {
              const terminal = TERMINAL_STATUSES.has(appointment.status);
              const busyCancel = cancelMutation.isPending && cancelMutation.variables === appointment.id;
              return (
                <li key={appointment.id} className="bg-card border border-border rounded-2xl p-5 shadow-sm">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-base font-bold text-foreground">{formatDateTime(appointment.start_at)}</p>
                      <p className="text-sm text-muted-foreground mt-0.5">
                        {appointment.appointment_type_name || "Appointment"}
                        {appointment.provider_name ? ` · ${appointment.provider_name}` : ""}
                        {appointment.location_name ? ` · ${appointment.location_name}` : ""}
                      </p>
                      {appointment.reason ? (
                        <p className="text-xs text-muted-foreground mt-1">Reason: {appointment.reason}</p>
                      ) : null}
                    </div>
                    <Pill tone={STATUS_TONE[appointment.status] ?? "neutral"}>{appointment.status}</Pill>
                  </div>

                  {!terminal ? (
                    <div className="mt-4 flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        data-testid={`cancel-${appointment.id}`}
                        disabled={busyCancel}
                        onClick={() => {
                          setRowError(null);
                          cancelMutation.mutate(appointment.id);
                        }}
                        className="inline-flex items-center gap-1.5 border border-destructive/30 text-destructive px-4 py-2.5 rounded-full font-bold text-xs hover:bg-destructive/10 transition-colors disabled:opacity-60 min-h-[44px]"
                      >
                        <CalendarX2 size={13} /> Cancel
                      </button>
                      <button
                        type="button"
                        data-testid={`reschedule-${appointment.id}`}
                        onClick={(event) => {
                          setRowError(null);
                          setDialogError(null);
                          rescheduleTrigger.current = event.currentTarget;
                          setRescheduleId(rescheduleId === appointment.id ? null : appointment.id);
                          setRescheduleValue(isoLocalDateTime(new Date(appointment.start_at)));
                        }}
                        className="inline-flex items-center gap-1.5 border border-border px-4 py-2.5 rounded-full font-bold text-xs hover:bg-muted transition-colors min-h-[44px]"
                      >
                        <CalendarClock size={13} /> Reschedule
                      </button>
                    </div>
                  ) : null}

                  {rowError && rowError.id === appointment.id ? (
                    <p role="alert" className="mt-3 text-xs text-destructive font-medium">
                      {rowError.message}
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </DataState>

        {/*
          The reschedule editor is a modal dialog: it traps the focus while it
          is open, closes on Escape, and hands the focus back to the button
          that opened it. The keyboard-only journey is asserted by
          `e2e/portal-keyboard.spec.ts`.
        */}
        <Dialog.Root
          open={rescheduleId !== null}
          onOpenChange={(open) => {
            if (!open) closeReschedule();
          }}
        >
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
            <Dialog.Content
              data-testid="reschedule-dialog"
              onCloseAutoFocus={(event) => {
                // The dialog is opened by a button that is not a
                // ``Dialog.Trigger``, so the focus is handed back explicitly:
                // the keyboard journey has to end on the control that opened
                // the dialog.
                event.preventDefault();
                rescheduleTrigger.current?.focus();
              }}
              className="fixed left-1/2 top-1/2 z-50 w-[calc(100vw-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-[2rem] bg-background p-6 shadow-xl focus:outline-none"
            >
              <Dialog.Title className="text-lg font-bold font-display text-foreground">
                Reschedule appointment
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-muted-foreground">
                Pick a new date and time. The clinic policy decides whether the change is allowed.
              </Dialog.Description>

              {rescheduleId ? (
                <form
                  className="mt-5 space-y-4"
                  onSubmit={(event) => {
                    event.preventDefault();
                    submitReschedule(rescheduleId);
                  }}
                >
                  <div>
                    <label
                      htmlFor={`reschedule-input-${rescheduleId}`}
                      className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2"
                    >
                      New date and time
                    </label>
                    <input
                      id={`reschedule-input-${rescheduleId}`}
                      data-testid="reschedule-input"
                      type="datetime-local"
                      value={rescheduleValue}
                      onChange={(e) => setRescheduleValue(e.target.value)}
                      className="border border-border rounded-xl px-4 py-3 text-sm font-medium bg-white/70 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary min-h-[44px] w-full"
                    />
                  </div>

                  {dialogError ? (
                    <p
                      role="alert"
                      data-testid="reschedule-error"
                      className="text-xs text-destructive font-medium"
                    >
                      {dialogError}
                    </p>
                  ) : null}

                  <div className="flex flex-wrap items-center gap-3">
                    <button
                      type="submit"
                      data-testid="reschedule-confirm"
                      disabled={rescheduleMutation.isPending}
                      className="bg-gradient-to-br from-primary to-primary-dim text-white px-5 py-3 rounded-full font-bold text-xs shadow-lg shadow-primary/20 hover:opacity-90 active:scale-95 transition-all disabled:opacity-60 min-h-[44px]"
                    >
                      {rescheduleMutation.isPending ? "Rescheduling…" : "Confirm"}
                    </button>
                    <Dialog.Close asChild>
                      <button
                        type="button"
                        data-testid="reschedule-cancel"
                        className="inline-flex items-center gap-1.5 border border-border px-4 py-2.5 rounded-full font-bold text-xs hover:bg-muted transition-colors min-h-[44px]"
                      >
                        <X size={13} /> Cancel
                      </button>
                    </Dialog.Close>
                  </div>
                </form>
              ) : null}
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>

        {appointments.length > 0 ? (
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground">{pageData.count} appointments</span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                className="border border-border px-4 py-2.5 rounded-full font-bold text-xs hover:bg-muted transition-colors disabled:opacity-50 min-h-[44px]"
              >
                Previous
              </button>
              <span className="text-xs text-muted-foreground">Page {page}</span>
              <button
                type="button"
                disabled={pageData.next === null}
                onClick={() => setPage((p) => p + 1)}
                className="border border-border px-4 py-2.5 rounded-full font-bold text-xs hover:bg-muted transition-colors disabled:opacity-50 min-h-[44px]"
              >
                Next
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </PortalLayout>
  );
}
