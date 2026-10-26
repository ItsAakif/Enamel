import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "wouter";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { Mail, Phone, PlusCircle, Search, UserPlus, X } from "lucide-react";
import {
  patientsCreate,
  patientsList,
  type PatientList,
  type PatientsListParams,
  type PatientWrite,
} from "@workspace/api-client-react";
import AppLayout from "@/layouts/AppLayout";
import { DataState } from "@/components/common/DataState";
import { useToast } from "@/hooks/use-toast";
import { apiErrorFieldErrors, apiErrorMessage, toPage } from "@/lib/api";
import { formatDate } from "@/lib/format";

const PAGE_SIZE = 20;
const INPUT_CLASS =
  "w-full border border-border rounded-xl px-4 py-3 text-sm font-medium bg-white/70 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all";

const STATUS_OPTIONS = ["", "active", "inactive", "archived", "deceased"];
const ORDERING_OPTIONS = [
  { value: "last_name", label: "Name (A–Z)" },
  { value: "-created_at", label: "Newest first" },
  { value: "number", label: "Patient number" },
];

function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

interface PatientFormState {
  first_name: string;
  last_name: string;
  preferred_name: string;
  email: string;
  phone: string;
  date_of_birth: string;
  sex_at_birth: string;
  preferred_contact: string;
  notes: string;
}

const EMPTY_FORM: PatientFormState = {
  first_name: "",
  last_name: "",
  preferred_name: "",
  email: "",
  phone: "",
  date_of_birth: "",
  sex_at_birth: "unknown",
  preferred_contact: "phone",
  notes: "",
};

export default function PatientsPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [ordering, setOrdering] = useState("last_name");
  const [page, setPage] = useState(1);
  const debouncedSearch = useDebounced(search);

  const [showCreate, setShowCreate] = useState(() =>
    // The dashboard's "New Patient" arrives as /app/patients?new=1; open the
    // create slide-over once and remove the parameter so a refresh does not
    // reopen it.
    new URLSearchParams(window.location.search).get("new") === "1",
  );
  const [form, setForm] = useState<PatientFormState>(EMPTY_FORM);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.get("new") === "1") {
      url.searchParams.delete("new");
      window.history.replaceState(null, "", url.toString());
    }
  }, []);

  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, status, ordering]);

  const params = useMemo(
    () =>
      ({
        search: debouncedSearch.trim() || undefined,
        status: status || undefined,
        ordering,
        page,
        page_size: PAGE_SIZE,
      }) as PatientsListParams,
    [debouncedSearch, status, ordering, page],
  );

  const listQuery = useQuery({
    queryKey: ["patients", "list", params],
    queryFn: async () => toPage<PatientList>(await patientsList(params)),
  });

  const createMutation = useMutation({
    mutationFn: (payload: PatientWrite) => patientsCreate(payload),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["patients"] });
      toast({ title: "Patient created", description: created.full_name || "The patient record was saved." });
      setShowCreate(false);
      setForm(EMPTY_FORM);
      setFieldErrors({});
    },
    onError: (error) => {
      setFieldErrors(apiErrorFieldErrors(error));
      setFormError(apiErrorMessage(error));
    },
  });

  const patients = listQuery.data?.results ?? [];
  const total = listQuery.data?.count ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    setFieldErrors({});
    const payload: PatientWrite = {
      first_name: form.first_name.trim(),
      last_name: form.last_name.trim(),
      preferred_name: form.preferred_name.trim() || undefined,
      email: form.email.trim() || undefined,
      phone: form.phone.trim() || undefined,
      date_of_birth: form.date_of_birth || null,
      sex_at_birth: form.sex_at_birth as PatientWrite["sex_at_birth"],
      preferred_contact: form.preferred_contact as PatientWrite["preferred_contact"],
      notes: form.notes.trim() || undefined,
      source: "staff",
    };
    createMutation.mutate(payload);
  }

  function fieldError(name: string) {
    const messages = fieldErrors[name];
    if (!messages || messages.length === 0) return null;
    return <p className="text-xs text-destructive mt-1">{messages.join(" ")}</p>;
  }

  return (
    <AppLayout>
      <div className="max-w-screen-2xl mx-auto">
        <header className="flex flex-wrap gap-4 justify-between items-end mb-8">
          <div>
            <nav className="flex gap-2 text-[10px] font-bold uppercase tracking-widest text-muted-foreground mb-2">
              <span>Patients</span>
              <span>/</span>
              <span className="text-primary">Records</span>
            </nav>
            <h2 className="text-4xl font-extrabold tracking-tighter text-foreground font-display">Patient Records</h2>
            <p className="text-sm text-muted-foreground mt-2">
              {listQuery.isSuccess ? `${total} patient${total === 1 ? "" : "s"} in this clinic` : "Loading directory…"}
            </p>
          </div>
          <button
            type="button"
            data-testid="new-patient-btn"
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-2 bg-gradient-to-br from-primary to-primary-dim text-white px-7 py-3 rounded-full font-display font-bold text-sm shadow-lg shadow-primary/20 hover:opacity-90 active:scale-95 transition-all"
          >
            <PlusCircle size={16} />
            New Patient
          </button>
        </header>

        <div className="glass-card rounded-[2rem] p-6 md:p-8 shadow-sm">
          <div className="flex flex-wrap gap-3 mb-6">
            <div className="relative flex-1 min-w-[220px]">
              <Search size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input
                data-testid="patient-search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search by name, email or number…"
                className={`${INPUT_CLASS} pl-11`}
                aria-label="Search patients"
              />
            </div>
            <select
              data-testid="patient-status-filter"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              className={`${INPUT_CLASS} w-auto`}
              aria-label="Filter by status"
            >
              {STATUS_OPTIONS.map((value) => (
                <option key={value || "all"} value={value}>
                  {value === "" ? "All statuses" : value}
                </option>
              ))}
            </select>
            <select
              data-testid="patient-ordering"
              value={ordering}
              onChange={(e) => setOrdering(e.target.value)}
              className={`${INPUT_CLASS} w-auto`}
              aria-label="Order patients"
            >
              {ORDERING_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <DataState
            loading={listQuery.isPending}
            error={listQuery.error}
            empty={patients.length === 0}
            emptyTitle="No patients found"
            emptyMessage={
              debouncedSearch || status
                ? "No patient matches the current search or filter."
                : "This clinic has no patient records yet. Create the first one."
            }
            onRetry={() => void listQuery.refetch()}
          >
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead>
                  <tr className="text-[10px] uppercase tracking-widest text-muted-foreground border-b border-border">
                    <th className="py-3 pr-4 font-bold">Patient</th>
                    <th className="py-3 pr-4 font-bold">Number</th>
                    <th className="py-3 pr-4 font-bold">Contact</th>
                    <th className="py-3 pr-4 font-bold">Status</th>
                    <th className="py-3 pr-4 font-bold">Location</th>
                    <th className="py-3 pr-4 font-bold">Created</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/60">
                  {patients.map((patient) => (
                    <tr key={patient.id} className="hover:bg-muted/40 transition-colors">
                      <td className="py-3 pr-4">
                        <Link
                          href={`/app/patients/${patient.id}`}
                          className="font-bold text-foreground font-display hover:text-primary transition-colors"
                        >
                          {patient.full_name || `${patient.first_name} ${patient.last_name}`}
                        </Link>
                        {patient.preferred_name ? (
                          <p className="text-[11px] text-muted-foreground">“{patient.preferred_name}”</p>
                        ) : null}
                      </td>
                      <td className="py-3 pr-4 text-sm text-muted-foreground font-mono">{patient.number}</td>
                      <td className="py-3 pr-4">
                        <div className="flex flex-col gap-1 text-xs text-muted-foreground">
                          {patient.email ? (
                            <span className="inline-flex items-center gap-1.5">
                              <Mail size={12} /> {patient.email}
                            </span>
                          ) : null}
                          {patient.phone_e164 ? (
                            <span className="inline-flex items-center gap-1.5">
                              <Phone size={12} /> {patient.phone_e164}
                            </span>
                          ) : null}
                        </div>
                      </td>
                      <td className="py-3 pr-4">
                        <span className="text-[10px] font-bold px-2.5 py-1 rounded-full uppercase tracking-wider bg-muted text-muted-foreground">
                          {patient.status}
                        </span>
                      </td>
                      <td className="py-3 pr-4 text-sm text-muted-foreground">
                        {patient.primary_location?.name ?? "—"}
                      </td>
                      <td className="py-3 pr-4 text-sm text-muted-foreground">
                        {formatDate(patient.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="flex items-center justify-between mt-6">
              <span className="text-xs text-muted-foreground">
                Page {page} of {totalPages}
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  className="px-4 py-2 rounded-full border border-border text-xs font-bold disabled:opacity-40 hover:bg-muted transition-colors"
                >
                  Previous
                </button>
                <button
                  type="button"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  className="px-4 py-2 rounded-full border border-border text-xs font-bold disabled:opacity-40 hover:bg-muted transition-colors"
                >
                  Next
                </button>
              </div>
            </div>
          </DataState>
        </div>
      </div>

      <AnimatePresence>
        {showCreate && (
          <>
            <motion.div
              key="overlay"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm"
              onClick={() => setShowCreate(false)}
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
              aria-label="New patient"
            >
              <div className="flex items-center justify-between p-6 border-b border-white/20">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-xl bg-primary/10 flex items-center justify-center">
                    <UserPlus size={16} className="text-primary" />
                  </div>
                  <h3 className="text-lg font-bold font-display">New Patient</h3>
                </div>
                <button
                  type="button"
                  onClick={() => setShowCreate(false)}
                  className="p-2 text-muted-foreground hover:bg-muted rounded-lg transition-colors"
                  aria-label="Close"
                >
                  <X size={18} />
                </button>
              </div>

              <form onSubmit={submitCreate} className="flex-1 overflow-y-auto p-6 space-y-5">
                {formError ? (
                  <div role="alert" className="rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    {formError}
                  </div>
                ) : null}

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                      First name
                    </label>
                    <input
                      data-testid="patient-first-name"
                      required
                      value={form.first_name}
                      onChange={(e) => setForm((s) => ({ ...s, first_name: e.target.value }))}
                      className={INPUT_CLASS}
                    />
                    {fieldError("first_name")}
                  </div>
                  <div>
                    <label className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                      Last name
                    </label>
                    <input
                      data-testid="patient-last-name"
                      required
                      value={form.last_name}
                      onChange={(e) => setForm((s) => ({ ...s, last_name: e.target.value }))}
                      className={INPUT_CLASS}
                    />
                    {fieldError("last_name")}
                  </div>
                </div>

                <div>
                  <label className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                    Preferred name
                  </label>
                  <input
                    value={form.preferred_name}
                    onChange={(e) => setForm((s) => ({ ...s, preferred_name: e.target.value }))}
                    className={INPUT_CLASS}
                  />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                      Email
                    </label>
                    <input
                      type="email"
                      value={form.email}
                      onChange={(e) => setForm((s) => ({ ...s, email: e.target.value }))}
                      className={INPUT_CLASS}
                    />
                    {fieldError("email")}
                  </div>
                  <div>
                    <label className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                      Phone
                    </label>
                    <input
                      value={form.phone}
                      onChange={(e) => setForm((s) => ({ ...s, phone: e.target.value }))}
                      className={INPUT_CLASS}
                    />
                    {fieldError("phone")}
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                      Date of birth
                    </label>
                    <input
                      type="date"
                      value={form.date_of_birth}
                      onChange={(e) => setForm((s) => ({ ...s, date_of_birth: e.target.value }))}
                      className={INPUT_CLASS}
                    />
                    {fieldError("date_of_birth")}
                  </div>
                  <div>
                    <label className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                      Sex at birth
                    </label>
                    <select
                      value={form.sex_at_birth}
                      onChange={(e) => setForm((s) => ({ ...s, sex_at_birth: e.target.value }))}
                      className={INPUT_CLASS}
                    >
                      {["female", "male", "intersex", "unknown"].map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <div>
                  <label className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                    Preferred contact
                  </label>
                  <select
                    value={form.preferred_contact}
                    onChange={(e) => setForm((s) => ({ ...s, preferred_contact: e.target.value }))}
                    className={INPUT_CLASS}
                  >
                    {["phone", "email", "sms", "whatsapp"].map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                    Notes
                  </label>
                  <textarea
                    rows={3}
                    value={form.notes}
                    onChange={(e) => setForm((s) => ({ ...s, notes: e.target.value }))}
                    className={`${INPUT_CLASS} resize-none`}
                  />
                </div>

                <div className="flex gap-3 pt-2">
                  <button
                    type="button"
                    onClick={() => setShowCreate(false)}
                    className="flex-1 border border-border py-3 rounded-xl font-bold text-sm text-muted-foreground hover:bg-muted transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    data-testid="patient-submit"
                    disabled={createMutation.isPending}
                    className="flex-1 bg-gradient-to-br from-primary to-primary-dim text-white py-3 rounded-xl font-bold text-sm hover:opacity-90 transition-all shadow-lg shadow-primary/20 disabled:opacity-60"
                  >
                    {createMutation.isPending ? "Saving…" : "Create patient"}
                  </button>
                </div>
              </form>
            </motion.aside>
          </>
        )}
      </AnimatePresence>
    </AppLayout>
  );
}
