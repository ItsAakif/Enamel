import type { Dispatch, SetStateAction } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Calendar, X } from "lucide-react";

export interface AppointmentFormState {
  patient: string;
  provider: string;
  appointmentType: string;
  location: string;
  operatory: string;
  startAt: string;
  reason: string;
  notes: string;
}

export interface PickerOption {
  id: string;
  label: string;
}

export interface AppointmentFormOptions {
  patients: PickerOption[];
  providers: PickerOption[];
  appointmentTypes: PickerOption[];
  locations: PickerOption[];
  operatories: PickerOption[];
}

const INPUT_CLASS =
  "w-full border border-white/30 rounded-xl px-4 py-3 text-sm font-medium bg-white/30 backdrop-blur-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all";

interface NewAppointmentSlideOverProps {
  open: boolean;
  mode: "create" | "reschedule";
  onClose: () => void;
  formState: AppointmentFormState;
  setFormState: Dispatch<SetStateAction<AppointmentFormState>>;
  options: AppointmentFormOptions;
  onSubmit: () => void;
  submitting: boolean;
  error: string | null;
}

/**
 * Create / reschedule slide-over. Every picker is populated from the API; no
 * procedure or provider list is hard-coded.
 */
export function NewAppointmentSlideOver({
  open,
  mode,
  onClose,
  formState,
  setFormState,
  options,
  onSubmit,
  submitting,
  error,
}: NewAppointmentSlideOverProps) {
  const isReschedule = mode === "reschedule";

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            key="overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm"
            onClick={onClose}
          />
          <motion.aside
            key="panel"
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", damping: 28, stiffness: 280 }}
            className="fixed right-0 top-0 h-screen w-full max-w-md glass-sidebar border-l border-white/30 shadow-2xl z-50 flex flex-col"
            role="dialog"
            aria-modal="true"
            aria-label={isReschedule ? "Reschedule appointment" : "New appointment"}
          >
            <div className="flex items-center justify-between p-6 border-b border-white/20">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-xl bg-primary/10 flex items-center justify-center">
                  <Calendar size={16} className="text-primary" />
                </div>
                <h3 className="text-lg font-bold font-display">
                  {isReschedule ? "Reschedule Appointment" : "New Appointment"}
                </h3>
              </div>
              <button
                type="button"
                onClick={onClose}
                className="p-2 text-muted-foreground hover:bg-muted rounded-lg transition-colors"
                aria-label="Close"
              >
                <X size={18} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-6 space-y-5">
              {error ? (
                <div role="alert" className="rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                  {error}
                </div>
              ) : null}

              {!isReschedule ? (
                <>
                  <div>
                    <label htmlFor="appt-patient" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                      Patient
                    </label>
                    <select
                      id="appt-patient"
                      data-testid="appt-patient"
                      value={formState.patient}
                      onChange={(e) => setFormState((s) => ({ ...s, patient: e.target.value }))}
                      className={INPUT_CLASS}
                    >
                      <option value="">Select patient</option>
                      {options.patients.map((option) => (
                        <option key={option.id} value={option.id}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label htmlFor="appt-provider" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                      Provider
                    </label>
                    <select
                      id="appt-provider"
                      data-testid="appt-provider"
                      value={formState.provider}
                      onChange={(e) => setFormState((s) => ({ ...s, provider: e.target.value }))}
                      className={INPUT_CLASS}
                    >
                      <option value="">Select provider</option>
                      {options.providers.map((option) => (
                        <option key={option.id} value={option.id}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label htmlFor="appt-type" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                      Appointment type
                    </label>
                    <select
                      id="appt-type"
                      data-testid="appt-type"
                      value={formState.appointmentType}
                      onChange={(e) => setFormState((s) => ({ ...s, appointmentType: e.target.value }))}
                      className={INPUT_CLASS}
                    >
                      <option value="">Select type</option>
                      {options.appointmentTypes.map((option) => (
                        <option key={option.id} value={option.id}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label htmlFor="appt-location" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                      Location
                    </label>
                    <select
                      id="appt-location"
                      data-testid="appt-location"
                      value={formState.location}
                      onChange={(e) => setFormState((s) => ({ ...s, location: e.target.value }))}
                      className={INPUT_CLASS}
                    >
                      <option value="">Select location</option>
                      {options.locations.map((option) => (
                        <option key={option.id} value={option.id}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </>
              ) : null}

              <div>
                <label htmlFor="appt-start" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                  Start
                </label>
                <input
                  id="appt-start"
                  data-testid="appt-start"
                  type="datetime-local"
                  value={formState.startAt}
                  onChange={(e) => setFormState((s) => ({ ...s, startAt: e.target.value }))}
                  className={INPUT_CLASS}
                />
              </div>

              <div>
                <label htmlFor="appt-operatory" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                  Operatory (optional)
                </label>
                <select
                  id="appt-operatory"
                  data-testid="appt-operatory"
                  value={formState.operatory}
                  onChange={(e) => setFormState((s) => ({ ...s, operatory: e.target.value }))}
                  className={INPUT_CLASS}
                >
                  <option value="">No operatory</option>
                  {options.operatories.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label htmlFor="appt-reason" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                  Reason
                </label>
                <input
                  id="appt-reason"
                  data-testid="appt-reason"
                  value={formState.reason}
                  onChange={(e) => setFormState((s) => ({ ...s, reason: e.target.value }))}
                  className={INPUT_CLASS}
                  placeholder="Chief complaint or reason"
                />
              </div>

              <div>
                <label htmlFor="appt-notes" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                  Notes
                </label>
                <textarea
                  id="appt-notes"
                  data-testid="appt-notes"
                  rows={3}
                  value={formState.notes}
                  onChange={(e) => setFormState((s) => ({ ...s, notes: e.target.value }))}
                  placeholder="Clinical notes, allergies, special instructions…"
                  className={`${INPUT_CLASS} resize-none`}
                />
              </div>
            </div>

            <div className="p-6 border-t border-white/20 flex gap-3">
              <button
                type="button"
                onClick={onClose}
                className="flex-1 border border-border py-3 rounded-xl font-bold text-sm text-muted-foreground hover:bg-muted transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid="appt-submit"
                disabled={submitting}
                onClick={onSubmit}
                className="flex-1 bg-gradient-to-br from-primary to-primary-dim text-white py-3 rounded-xl font-bold text-sm hover:opacity-90 transition-all shadow-lg shadow-primary/20 disabled:opacity-60"
              >
                {submitting ? "Saving…" : isReschedule ? "Reschedule" : "Book appointment"}
              </button>
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
