import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import {
  BadgeCheck,
  Banknote,
  Download,
  FilePlus2,
  RefreshCcw,
  Receipt,
  Search,
  Undo2,
  X,
} from "lucide-react";
import {
  billingReconciliationList,
  invoicesFromPlanCreate,
  invoicesList,
  invoicesPaymentsCreate,
  invoicesPdfRetrieve,
  invoicesRetrieve,
  patientsList,
  paymentsRefundCreate,
  treatmentPlansList,
  type BillingReconciliationListParams,
  type Invoice,
  type InvoicesListParams,
  type Payment,
  type PaymentCreate,
  type ReconciliationItem,
  type Refund,
  type TreatmentPlan,
  type TreatmentPlansListParams,
} from "@workspace/api-client-react";
import AppLayout from "@/layouts/AppLayout";
import { DataState } from "@/components/common/DataState";
import { useToast } from "@/hooks/use-toast";
import {
  apiErrorFieldErrors,
  apiErrorMessage,
  newIdempotencyKey,
  toPage,
} from "@/lib/api";
import { downloadBlob, isBlob } from "@/lib/download";
import { formatDate, formatDateTime, formatMinorUnits } from "@/lib/format";

const PAGE_SIZE = 25;
const INPUT_CLASS =
  "w-full border border-border rounded-xl px-4 py-3 text-sm font-medium bg-white/70 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all";

/** The invoice list view also accepts `patient`/`plan`; only pagination is documented. */
type InvoiceListParams = InvoicesListParams & { status?: string; patient?: string };
type PlanListParams = TreatmentPlansListParams & { status?: string };

const INVOICE_STATUSES = ["", "draft", "issued", "partially_paid", "paid", "void"] as const;

const STATUS_BADGE: Record<string, string> = {
  draft: "bg-muted text-muted-foreground",
  issued: "bg-sky-100 text-sky-700 dark:bg-sky-900/30",
  partially_paid: "bg-amber-100 text-amber-700 dark:bg-amber-900/30",
  paid: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30",
  void: "bg-destructive/10 text-destructive",
};

const PAYMENT_METHODS = ["cash", "card", "upi", "bank", "other"] as const;

type BalanceFilter = "all" | "outstanding" | "settled";

function statusLabel(status: string): string {
  return status.replace(/_/g, " ");
}

export default function BillingPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [tab, setTab] = useState<"invoices" | "reconciliation">("invoices");
  const [status, setStatus] = useState<string>("");
  const [balance, setBalance] = useState<BalanceFilter>("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showNewInvoice, setShowNewInvoice] = useState(false);

  // Payments recorded during this session, keyed by invoice id (the API has no
  // payment-list endpoint, so freshly recorded payments are shown inline).
  const [recordedPayments, setRecordedPayments] = useState<Record<string, Payment[]>>({});
  const [paymentAmount, setPaymentAmount] = useState("");
  const [paymentMethod, setPaymentMethod] = useState<PaymentCreate["method"]>("cash");
  const [refundTarget, setRefundTarget] = useState<Payment | null>(null);
  const [refundAmount, setRefundAmount] = useState("");

  useEffect(() => {
    setPage(1);
  }, [status, balance, search]);

  const listParams = useMemo<InvoiceListParams>(
    () => ({
      status: status || undefined,
      page,
      page_size: PAGE_SIZE,
    }),
    [status, page],
  );

  const invoicesQuery = useQuery({
    queryKey: ["billing", "invoices", listParams],
    queryFn: async () => toPage<Invoice>(await invoicesList(listParams)),
  });

  const patientsQuery = useQuery({
    queryKey: ["billing", "patient-names"],
    queryFn: async () => toPage<{ id: string; full_name: string; first_name: string; last_name: string }>(
      await patientsList({ page_size: 100 }),
    ),
    staleTime: 60_000,
  });

  const reconciliationQuery = useQuery({
    queryKey: ["billing", "reconciliation", { page }],
    queryFn: async () => {
      const params: BillingReconciliationListParams = { page, page_size: PAGE_SIZE };
      return toPage<ReconciliationItem>(await billingReconciliationList(params));
    },
    enabled: tab === "reconciliation",
  });

  const plansQuery = useQuery({
    queryKey: ["billing", "accepted-plans"],
    queryFn: async () => {
      const params: PlanListParams = { status: "accepted", page_size: 50 };
      return toPage<TreatmentPlan>(await treatmentPlansList(params));
    },
    enabled: showNewInvoice,
  });

  const detailQuery = useQuery({
    queryKey: ["billing", "invoice", selectedId],
    queryFn: () => invoicesRetrieve(selectedId as string),
    enabled: selectedId !== null,
  });

  const patientNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const patient of patientsQuery.data?.results ?? []) {
      map.set(
        patient.id,
        patient.full_name || `${patient.first_name} ${patient.last_name}`.trim(),
      );
    }
    return map;
  }, [patientsQuery.data]);

  const patientLabel = (id: string) => patientNames.get(id) ?? `${id.slice(0, 8)}…`;

  const fromPlanMutation = useMutation({
    mutationFn: (planId: string) => invoicesFromPlanCreate(planId),
    onSuccess: (invoice) => {
      void queryClient.invalidateQueries({ queryKey: ["billing", "invoices"] });
      toast({ title: "Invoice created", description: `${invoice.number} raised from the treatment plan.` });
      setShowNewInvoice(false);
      setSelectedId(invoice.id);
    },
    onError: (error) => {
      toast({ title: "Could not create invoice", description: apiErrorMessage(error), variant: "destructive" });
    },
  });

  const paymentMutation = useMutation({
    mutationFn: ({ invoiceId, data }: { invoiceId: string; data: PaymentCreate }) =>
      invoicesPaymentsCreate(invoiceId, data, { headers: { "Idempotency-Key": newIdempotencyKey() } }),
    onSuccess: (payment) => {
      setRecordedPayments((current) => ({
        ...current,
        [payment.invoice]: [...(current[payment.invoice] ?? []), payment],
      }));
      setPaymentAmount("");
      void queryClient.invalidateQueries({ queryKey: ["billing", "invoice", payment.invoice] });
      void queryClient.invalidateQueries({ queryKey: ["billing", "invoices"] });
      toast({
        title: "Payment recorded",
        description: `${formatMinorUnits(payment.amount_minor, payment.currency)} · ${payment.status}.`,
      });
    },
    onError: (error) => {
      toast({ title: "Payment failed", description: apiErrorMessage(error), variant: "destructive" });
    },
  });

  const refundMutation = useMutation({
    mutationFn: ({ paymentId, amountMinor, reason }: { paymentId: string; amountMinor: number; reason: string }) =>
      paymentsRefundCreate(
        paymentId,
        { amount_minor: amountMinor, reason },
        { headers: { "Idempotency-Key": newIdempotencyKey() } },
      ),
    onSuccess: (refund: Refund) => {
      toast({
        title: "Refund issued",
        description: `${formatMinorUnits(refund.amount_minor, "INR")} · ${refund.status}.`,
      });
      setRefundTarget(null);
      setRefundAmount("");
    },
    onError: (error) => {
      toast({ title: "Refund failed", description: apiErrorMessage(error), variant: "destructive" });
    },
  });

  const [downloadError, setDownloadError] = useState<string | null>(null);
  async function downloadReceipt(invoice: Invoice) {
    setDownloadError(null);
    try {
      const result: unknown = await invoicesPdfRetrieve(invoice.id);
      if (isBlob(result)) {
        downloadBlob(result, `receipt-${invoice.number}.pdf`);
      } else {
        setDownloadError("The receipt response was not a PDF document.");
      }
    } catch (error) {
      setDownloadError(apiErrorMessage(error));
    }
  }

  const invoices = invoicesQuery.data?.results ?? [];
  const filtered =
    balance === "all"
      ? invoices
      : invoices.filter((invoice) =>
          balance === "outstanding" ? invoice.balance_minor > 0 : invoice.balance_minor === 0,
        );
  const total = balance === "all" ? invoicesQuery.data?.count ?? 0 : filtered.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const searchTerm = search.trim().toLowerCase();
  const visible = searchTerm
    ? filtered.filter(
        (invoice) =>
          invoice.number.toLowerCase().includes(searchTerm) ||
          patientLabel(invoice.patient).toLowerCase().includes(searchTerm),
      )
    : filtered;

  const detail = detailQuery.data;
  const detailPayments = detail ? recordedPayments[detail.id] ?? [] : [];

  function submitPayment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail) return;
    const major = Number.parseFloat(paymentAmount);
    if (!Number.isFinite(major) || major <= 0) {
      toast({ title: "Enter an amount", description: "Payment amount must be greater than zero.", variant: "destructive" });
      return;
    }
    paymentMutation.mutate({
      invoiceId: detail.id,
      data: { amount_minor: Math.round(major * 100), method: paymentMethod },
    });
  }

  return (
    <AppLayout>
      <div className="max-w-screen-2xl mx-auto">
        <header className="flex flex-wrap gap-4 justify-between items-end mb-8">
          <div>
            <span className="text-xs font-bold uppercase tracking-[0.2em] text-primary mb-2 block font-sans">
              Financials
            </span>
            <h2 className="text-4xl font-extrabold text-foreground font-display leading-none">
              Billing &amp; Payments
            </h2>
          </div>
          <button
            type="button"
            data-testid="new-invoice-btn"
            onClick={() => setShowNewInvoice(true)}
            className="flex items-center gap-2 bg-gradient-to-br from-primary to-primary-dim text-white px-6 py-2.5 rounded-full font-bold text-sm shadow-lg shadow-primary/20 hover:opacity-90 transition-all"
          >
            <FilePlus2 size={16} /> Invoice from plan
          </button>
        </header>

        <div className="flex gap-2 mb-6">
          {(["invoices", "reconciliation"] as const).map((value) => (
            <button
              key={value}
              type="button"
              data-testid={`billing-tab-${value}`}
              onClick={() => setTab(value)}
              className={`px-5 py-2.5 rounded-full text-xs font-bold uppercase tracking-wider transition-colors ${
                tab === value ? "bg-primary text-white shadow-md" : "bg-background border border-border text-muted-foreground hover:bg-muted"
              }`}
            >
              {value === "invoices" ? "Invoices" : "Reconciliation"}
            </button>
          ))}
        </div>

        {tab === "invoices" ? (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="glass-card rounded-[2rem] shadow-sm overflow-hidden"
          >
            <div className="p-6 border-b border-white/30 flex flex-wrap items-center gap-3 bg-white/20">
              <div className="relative flex-1 min-w-[220px]">
                <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground" size={18} />
                <input
                  data-testid="invoice-search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search by invoice number or patient…"
                  className={`${INPUT_CLASS} pl-12`}
                  aria-label="Search invoices"
                />
              </div>
              <select
                data-testid="invoice-status-filter"
                value={status}
                onChange={(event) => setStatus(event.target.value)}
                className={`${INPUT_CLASS} w-auto`}
                aria-label="Filter by status"
              >
                {INVOICE_STATUSES.map((value) => (
                  <option key={value || "all"} value={value}>
                    {value === "" ? "All statuses" : statusLabel(value)}
                  </option>
                ))}
              </select>
              <select
                data-testid="invoice-balance-filter"
                value={balance}
                onChange={(event) => setBalance(event.target.value as BalanceFilter)}
                className={`${INPUT_CLASS} w-auto`}
                aria-label="Filter by balance"
              >
                <option value="all">All balances</option>
                <option value="outstanding">Outstanding</option>
                <option value="settled">Settled</option>
              </select>
            </div>

            <DataState
              loading={invoicesQuery.isPending}
              error={invoicesQuery.error}
              empty={visible.length === 0}
              emptyTitle="No invoices"
              emptyMessage={
                status || balance !== "all" || searchTerm
                  ? "No invoice matches the current filters."
                  : "This clinic has no invoices yet. Raise one from an accepted treatment plan."
              }
              onRetry={() => void invoicesQuery.refetch()}
            >
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-border text-xs uppercase tracking-widest text-muted-foreground font-bold">
                      <th className="p-6 font-semibold">Invoice</th>
                      <th className="p-6 font-semibold">Patient</th>
                      <th className="p-6 font-semibold">Issued</th>
                      <th className="p-6 font-semibold">Total</th>
                      <th className="p-6 font-semibold">Balance</th>
                      <th className="p-6 font-semibold">Status</th>
                      <th className="p-6 font-semibold text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="text-sm font-medium">
                    {visible.map((invoice) => (
                      <tr key={invoice.id} className="border-b border-border hover:bg-muted/30 transition-colors">
                        <td className="p-6 font-bold text-foreground font-mono">{invoice.number}</td>
                        <td className="p-6">{patientLabel(invoice.patient)}</td>
                        <td className="p-6 text-muted-foreground">{formatDate(invoice.issued_at ?? invoice.created_at)}</td>
                        <td className="p-6 font-bold">{formatMinorUnits(invoice.total_minor, invoice.currency)}</td>
                        <td className="p-6">{formatMinorUnits(invoice.balance_minor, invoice.currency)}</td>
                        <td className="p-6">
                          <span className={`px-3 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider ${STATUS_BADGE[invoice.status] ?? STATUS_BADGE.draft}`}>
                            {statusLabel(invoice.status)}
                          </span>
                        </td>
                        <td className="p-6 text-right">
                          <button
                            type="button"
                            onClick={() => {
                              setSelectedId(invoice.id);
                              setPaymentAmount("");
                            }}
                            className="text-primary font-bold hover:underline"
                          >
                            View
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="flex items-center justify-between p-6">
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
          </motion.div>
        ) : (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="glass-card rounded-[2rem] p-8 shadow-sm"
          >
            <div className="flex items-center justify-between mb-6">
              <div>
                <h3 className="text-xl font-bold font-display">Gateway reconciliation</h3>
                <p className="text-sm text-muted-foreground mt-1">
                  Unmatched gateway events awaiting a payment match.
                </p>
              </div>
              <button
                type="button"
                onClick={() => void reconciliationQuery.refetch()}
                className="inline-flex items-center gap-2 text-xs font-bold text-primary hover:underline"
              >
                <RefreshCcw size={14} /> Refresh
              </button>
            </div>
            <DataState
              loading={reconciliationQuery.isPending}
              error={reconciliationQuery.error}
              empty={(reconciliationQuery.data?.results ?? []).length === 0}
              emptyTitle="Nothing to reconcile"
              emptyMessage="Every gateway event is matched to a payment."
              onRetry={() => void reconciliationQuery.refetch()}
            >
              <table className="w-full text-left">
                <thead>
                  <tr className="text-[10px] uppercase tracking-widest text-muted-foreground border-b border-border">
                    <th className="py-3 pr-4 font-bold">Gateway</th>
                    <th className="py-3 pr-4 font-bold">Event</th>
                    <th className="py-3 pr-4 font-bold">Reason</th>
                    <th className="py-3 pr-4 font-bold">Amount</th>
                    <th className="py-3 pr-4 font-bold">Status</th>
                    <th className="py-3 pr-4 font-bold">Created</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/60 text-sm">
                  {(reconciliationQuery.data?.results ?? []).map((item) => (
                    <tr key={item.id} className="hover:bg-muted/40 transition-colors">
                      <td className="py-3 pr-4 font-bold">{item.gateway || "—"}</td>
                      <td className="py-3 pr-4 font-mono text-xs text-muted-foreground">{item.event_id}</td>
                      <td className="py-3 pr-4">{item.reason}</td>
                      <td className="py-3 pr-4">{formatMinorUnits(item.amount_minor, null)}</td>
                      <td className="py-3 pr-4">
                        <span className={`px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider ${item.status === "resolved" ? STATUS_BADGE.paid : STATUS_BADGE.partially_paid}`}>
                          {item.status}
                        </span>
                      </td>
                      <td className="py-3 pr-4 text-muted-foreground">{formatDateTime(item.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </DataState>
          </motion.div>
        )}
      </div>

      <AnimatePresence>
        {showNewInvoice ? (
          <>
            <motion.div
              key="plan-overlay"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm"
              onClick={() => setShowNewInvoice(false)}
            />
            <motion.aside
              key="plan-panel"
              initial={{ x: "100%" }}
              animate={{ x: 0 }}
              exit={{ x: "100%" }}
              transition={{ type: "spring", damping: 28, stiffness: 280 }}
              className="fixed right-0 top-0 h-screen w-full max-w-lg glass-sidebar border-l border-white/30 shadow-2xl z-50 flex flex-col"
              role="dialog"
              aria-modal="true"
              aria-label="Create invoice from an accepted treatment plan"
            >
              <div className="flex items-center justify-between p-6 border-b border-white/20">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-xl bg-primary/10 flex items-center justify-center">
                    <Receipt size={16} className="text-primary" />
                  </div>
                  <h3 className="text-lg font-bold font-display">Invoice from treatment plan</h3>
                </div>
                <button
                  type="button"
                  onClick={() => setShowNewInvoice(false)}
                  className="p-2 text-muted-foreground hover:bg-muted rounded-lg transition-colors"
                  aria-label="Close"
                >
                  <X size={18} />
                </button>
              </div>
              <div className="flex-1 overflow-y-auto p-6">
                <DataState
                  loading={plansQuery.isPending}
                  error={plansQuery.error}
                  empty={(plansQuery.data?.results ?? []).length === 0}
                  emptyTitle="No accepted plans"
                  emptyMessage="Accept a treatment plan before raising an invoice from it."
                  onRetry={() => void plansQuery.refetch()}
                >
                  <div className="space-y-3">
                    {(plansQuery.data?.results ?? []).map((plan) => (
                      <div key={plan.id} className="border border-border rounded-2xl p-4 flex items-center justify-between gap-4">
                        <div>
                          <p className="font-bold text-foreground">{plan.title || "Treatment plan"}</p>
                          <p className="text-xs text-muted-foreground mt-1">
                            {patientLabel(plan.patient)} · {formatMinorUnits(plan.total_minor, plan.currency)} · {plan.items.length} item{plan.items.length === 1 ? "" : "s"}
                          </p>
                        </div>
                        <button
                          type="button"
                          disabled={fromPlanMutation.isPending}
                          onClick={() => fromPlanMutation.mutate(plan.id)}
                          className="shrink-0 bg-gradient-to-br from-primary to-primary-dim text-white px-5 py-2.5 rounded-full text-xs font-bold hover:opacity-90 transition-all disabled:opacity-60"
                        >
                          Raise invoice
                        </button>
                      </div>
                    ))}
                  </div>
                </DataState>
              </div>
            </motion.aside>
          </>
        ) : null}
      </AnimatePresence>

      <AnimatePresence>
        {selectedId ? (
          <>
            <motion.div
              key="detail-overlay"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm"
              onClick={() => setSelectedId(null)}
            />
            <motion.aside
              key="detail-panel"
              initial={{ x: "100%" }}
              animate={{ x: 0 }}
              exit={{ x: "100%" }}
              transition={{ type: "spring", damping: 28, stiffness: 280 }}
              className="fixed right-0 top-0 h-screen w-full max-w-xl glass-sidebar border-l border-white/30 shadow-2xl z-50 flex flex-col"
              role="dialog"
              aria-modal="true"
              aria-label="Invoice detail"
            >
              <div className="flex items-center justify-between p-6 border-b border-white/20">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-xl bg-primary/10 flex items-center justify-center">
                    <BadgeCheck size={16} className="text-primary" />
                  </div>
                  <h3 className="text-lg font-bold font-display">
                    {detail ? detail.number : "Invoice"}
                  </h3>
                </div>
                <button
                  type="button"
                  onClick={() => setSelectedId(null)}
                  className="p-2 text-muted-foreground hover:bg-muted rounded-lg transition-colors"
                  aria-label="Close"
                >
                  <X size={18} />
                </button>
              </div>

              <div className="flex-1 overflow-y-auto p-6 space-y-6">
                <DataState
                  loading={detailQuery.isPending}
                  error={detailQuery.error}
                  onRetry={() => void detailQuery.refetch()}
                >
                  {detail ? (
                    <>
                      <div className="flex items-center justify-between">
                        <div>
                          <p className="text-xs text-muted-foreground">Patient</p>
                          <p className="font-bold text-foreground">{patientLabel(detail.patient)}</p>
                          <p className="text-xs text-muted-foreground mt-2">
                            Issued {formatDate(detail.issued_at ?? detail.created_at)} · Due {formatDate(detail.due_at)}
                          </p>
                        </div>
                        <span className={`px-3 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider ${STATUS_BADGE[detail.status] ?? STATUS_BADGE.draft}`}>
                          {statusLabel(detail.status)}
                        </span>
                      </div>

                      <table className="w-full text-left text-sm">
                        <thead>
                          <tr className="text-[10px] uppercase tracking-widest text-muted-foreground border-b border-border">
                            <th className="py-2 font-bold">Description</th>
                            <th className="py-2 font-bold text-right">Qty</th>
                            <th className="py-2 font-bold text-right">Unit</th>
                            <th className="py-2 font-bold text-right">Total</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-border/60">
                          {detail.lines.map((line) => (
                            <tr key={line.id}>
                              <td className="py-2">
                                {line.description || line.procedure_code || "Line item"}
                                {line.tooth ? <span className="text-muted-foreground"> · tooth {line.tooth}</span> : null}
                              </td>
                              <td className="py-2 text-right">{line.quantity}</td>
                              <td className="py-2 text-right">{formatMinorUnits(line.unit_price_minor, detail.currency)}</td>
                              <td className="py-2 text-right font-bold">{formatMinorUnits(line.line_total_minor, detail.currency)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>

                      <div className="bg-muted/40 rounded-2xl p-4 space-y-1 text-sm">
                        <Row label="Subtotal" value={formatMinorUnits(detail.subtotal_minor, detail.currency)} />
                        <Row label="Discount" value={formatMinorUnits(detail.discount_minor, detail.currency)} />
                        <Row label="Tax" value={formatMinorUnits(detail.tax_minor, detail.currency)} />
                        <Row label="Total" value={formatMinorUnits(detail.total_minor, detail.currency)} bold />
                        <Row label="Paid" value={formatMinorUnits(detail.paid_minor, detail.currency)} />
                        <Row label="Balance" value={formatMinorUnits(detail.balance_minor, detail.currency)} bold />
                      </div>

                      {detailPayments.length > 0 ? (
                        <div>
                          <h4 className="text-xs font-bold uppercase tracking-widest text-muted-foreground mb-3">
                            Payments recorded this session
                          </h4>
                          <div className="space-y-2">
                            {detailPayments.map((payment) => (
                              <div key={payment.id} className="flex items-center justify-between border border-border rounded-xl px-4 py-3 text-sm">
                                <div>
                                  <p className="font-bold">{formatMinorUnits(payment.amount_minor, payment.currency)}</p>
                                  <p className="text-xs text-muted-foreground">
                                    {payment.method} · {payment.status}
                                    {payment.receipt_number ? ` · ${payment.receipt_number}` : ""}
                                  </p>
                                </div>
                                <button
                                  type="button"
                                  onClick={() => {
                                    setRefundTarget(payment);
                                    setRefundAmount((payment.amount_minor / 100).toFixed(2));
                                  }}
                                  className="inline-flex items-center gap-1.5 text-xs font-bold text-primary hover:underline"
                                >
                                  <Undo2 size={12} /> Refund
                                </button>
                              </div>
                            ))}
                          </div>
                        </div>
                      ) : null}

                      <form onSubmit={submitPayment} className="border-t border-border pt-6 space-y-4">
                        <h4 className="text-xs font-bold uppercase tracking-widest text-muted-foreground flex items-center gap-2">
                          <Banknote size={14} /> Record payment (sandbox)
                        </h4>
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <label className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                              Amount ({detail.currency})
                            </label>
                            <input
                              data-testid="payment-amount"
                              type="number"
                              min="0"
                              step="0.01"
                              value={paymentAmount}
                              onChange={(event) => setPaymentAmount(event.target.value)}
                              className={INPUT_CLASS}
                            />
                          </div>
                          <div>
                            <label className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                              Method
                            </label>
                            <select
                              data-testid="payment-method"
                              value={paymentMethod}
                              onChange={(event) => setPaymentMethod(event.target.value as PaymentCreate["method"])}
                              className={INPUT_CLASS}
                            >
                              {PAYMENT_METHODS.map((method) => (
                                <option key={method} value={method}>
                                  {method}
                                </option>
                              ))}
                            </select>
                          </div>
                        </div>
                        <button
                          type="submit"
                          data-testid="payment-submit"
                          disabled={paymentMutation.isPending}
                          className="w-full bg-gradient-to-br from-primary to-primary-dim text-white py-3 rounded-xl font-bold text-sm hover:opacity-90 transition-all shadow-lg shadow-primary/20 disabled:opacity-60"
                        >
                          {paymentMutation.isPending ? "Recording…" : "Record payment"}
                        </button>
                      </form>

                      <div>
                        <button
                          type="button"
                          data-testid="receipt-download"
                          onClick={() => void downloadReceipt(detail)}
                          className="inline-flex items-center gap-2 border border-border px-5 py-2.5 rounded-full text-sm font-bold hover:bg-muted transition-colors"
                        >
                          <Download size={16} /> Download receipt PDF
                        </button>
                        {downloadError ? (
                          <p className="text-xs text-destructive mt-2" role="alert">
                            {downloadError}
                          </p>
                        ) : null}
                      </div>
                    </>
                  ) : null}
                </DataState>
              </div>
            </motion.aside>
          </>
        ) : null}
      </AnimatePresence>

      <AnimatePresence>
        {refundTarget ? (
          <>
            <motion.div
              key="refund-overlay"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-[60] bg-black/50 backdrop-blur-sm"
              onClick={() => setRefundTarget(null)}
            />
            <motion.div
              key="refund-dialog"
              initial={{ opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.96 }}
              className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-[60] w-full max-w-sm glass-card rounded-[2rem] p-6 shadow-2xl"
              role="dialog"
              aria-modal="true"
              aria-label="Refund payment"
            >
              <h3 className="text-lg font-bold font-display mb-4">Refund payment</h3>
              <label className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                Amount ({refundTarget.currency})
              </label>
              <input
                data-testid="refund-amount"
                type="number"
                min="0"
                step="0.01"
                value={refundAmount}
                onChange={(event) => setRefundAmount(event.target.value)}
                className={INPUT_CLASS}
              />
              <div className="flex gap-3 mt-6">
                <button
                  type="button"
                  onClick={() => setRefundTarget(null)}
                  className="flex-1 border border-border py-3 rounded-xl font-bold text-sm text-muted-foreground hover:bg-muted transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  data-testid="refund-submit"
                  disabled={refundMutation.isPending}
                  onClick={() => {
                    const major = Number.parseFloat(refundAmount);
                    if (!Number.isFinite(major) || major <= 0) {
                      toast({ title: "Enter an amount", description: "Refund amount must be greater than zero.", variant: "destructive" });
                      return;
                    }
                    refundMutation.mutate({
                      paymentId: refundTarget.id,
                      amountMinor: Math.round(major * 100),
                      reason: "staff refund",
                    });
                  }}
                  className="flex-1 bg-gradient-to-br from-primary to-primary-dim text-white py-3 rounded-xl font-bold text-sm hover:opacity-90 transition-all disabled:opacity-60"
                >
                  {refundMutation.isPending ? "Refunding…" : "Refund"}
                </button>
              </div>
            </motion.div>
          </>
        ) : null}
      </AnimatePresence>
    </AppLayout>
  );
}

function Row({ label, value, bold = false }: { label: string; value: string; bold?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className={bold ? "font-bold text-foreground" : "text-muted-foreground"}>{label}</span>
      <span className={bold ? "font-bold text-foreground" : "text-foreground"}>{value}</span>
    </div>
  );
}
