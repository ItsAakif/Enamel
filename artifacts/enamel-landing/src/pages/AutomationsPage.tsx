import { useState, type FormEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Plus,
  RefreshCcw,
  Send,
  Sparkles,
  Trash2,
  XCircle,
} from "lucide-react";
import {
  appointmentTypesList,
  campaignsActivateCreate,
  campaignsApproveCreate,
  campaignsList,
  campaignsMetricsRetrieve,
  campaignsPauseCreate,
  consentsList,
  consentsRevokeCreate,
  messagesList,
  offersClaimCreate,
  offersDeclineCreate,
  recallDueList,
  recallScheduleCreate,
  recallTypesList,
  schedulingGapsList,
  schedulingRecommendationsList,
  segmentsCreate,
  segmentsList,
  segmentsPreviewCreate,
  waitlistCreate,
  waitlistList,
  waitlistOffersCreate,
  type Campaign,
  type CampaignsListParams,
  type CampaignMetrics,
  type AppointmentType,
  type Consent,
  type ConsentsListParams,
  type Gap,
  type Message,
  type MessagesListParams,
  type RecallType,
  type Recommendation,
  type Segment,
  type WaitlistEntry,
  type WaitlistListParams,
} from "@workspace/api-client-react";
import AppLayout from "@/layouts/AppLayout";
import { DataState } from "@/components/common/DataState";
import { useToast } from "@/hooks/use-toast";
import {
  apiErrorFieldErrors,
  apiErrorMessage,
  readBoolean,
  readNumber,
  readRecordArray,
  readString,
  toPage,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";

const PAGE_SIZE = 25;
const INPUT_CLASS =
  "w-full border border-border rounded-xl px-4 py-3 text-sm font-medium bg-white/70 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all";

const MESSAGE_STATUSES = ["", "queued", "sent", "delivered", "failed", "skipped", "dead"] as const;
const CONSENT_STATUSES = ["", "granted", "revoked"] as const;
const WAITLIST_STATUSES = ["", "active", "offered", "claimed", "expired", "cancelled"] as const;

const SEGMENT_FIELDS = ["status", "last_visit_days", "recall_due", "plan_status", "location", "source"] as const;
const SEGMENT_OPS = ["eq", "gte", "lte", "in", "between"] as const;

type Tab = "messages" | "consent" | "recall" | "waitlist" | "marketing";

/** The list views also accept `status`; only pagination is documented. */
type MessageListParams = MessagesListParams & { status?: string };
type ConsentListParams = ConsentsListParams & { status?: string };
type WaitlistListQueryParams = WaitlistListParams & { status?: string };

interface Condition {
  field: string;
  op: string;
  value: string;
}

const EMPTY_CONDITION: Condition = { field: "last_visit_days", op: "gte", value: "180" };

function parseConditionValue(condition: Condition): unknown {
  const raw = condition.value.trim();
  if (condition.op === "in" || condition.op === "between") {
    return raw
      .split(",")
      .map((part) => Number.parseFloat(part.trim()))
      .filter((value) => Number.isFinite(value));
  }
  if (condition.op === "eq") {
    if (raw === "true") return true;
    if (raw === "false") return false;
    const numeric = Number.parseFloat(raw);
    return Number.isFinite(numeric) && String(numeric) === raw ? numeric : raw;
  }
  return Number.parseFloat(raw);
}

function CampaignMetricsPanel({ campaignId }: { campaignId: string }) {
  const metricsQuery = useQuery({
    queryKey: ["automations", "campaign-metrics", campaignId],
    queryFn: () => campaignsMetricsRetrieve(campaignId),
  });
  return (
    <DataState loading={metricsQuery.isPending} error={metricsQuery.error} onRetry={() => void metricsQuery.refetch()}>
      {metricsQuery.data ? <CampaignMetricsBody metrics={metricsQuery.data} /> : null}
    </DataState>
  );
}

function CampaignMetricsBody({ metrics }: { metrics: CampaignMetrics }) {
  const recipients = metrics.recipients;
  const messages = metrics.messages;
  const engagement = metrics.engagement;
  const recipientTotal = readNumber(recipients, "total") ?? 0;
  const engagementNote = readString(engagement, "note");
  const engagementAvailable = readBoolean(engagement, "available");
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4">
      <Stat label="Recipients" value={recipientTotal} />
      <Stat label="Sent" value={readNumber(recipients, "sent") ?? 0} />
      <Stat label="Skipped" value={readNumber(recipients, "skipped") ?? 0} />
      <Stat label="Failed" value={readNumber(recipients, "failed") ?? 0} />
      <Stat label="Messages" value={readNumber(messages, "total") ?? 0} />
      <Stat label="Delivered" value={readNumber(messages, "delivered") ?? 0} />
      <Stat label="Queued" value={readNumber(messages, "queued") ?? 0} />
      <div className="col-span-2 sm:col-span-4 bg-muted/40 rounded-xl px-4 py-3 text-xs text-muted-foreground">
        <span className="font-bold text-foreground">Engagement: </span>
        {engagementAvailable ? "provider-reported" : "not available"} — {engagementNote}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-muted/40 rounded-xl p-3 text-center">
      <p className="text-[10px] uppercase font-bold text-muted-foreground">{label}</p>
      <p className="text-lg font-black font-display text-foreground mt-1">{value}</p>
    </div>
  );
}

export default function AutomationsPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [tab, setTab] = useState<Tab>("messages");

  // Messages
  const [messageStatus, setMessageStatus] = useState("");
  const [messageReason, setMessageReason] = useState("");

  // Consent
  const [consentStatus, setConsentStatus] = useState("");

  // Recall
  const [recallPatient, setRecallPatient] = useState("");
  const [recallType, setRecallType] = useState("");

  // Waitlist
  const [waitlistStatus, setWaitlistStatus] = useState("");
  const [newEntry, setNewEntry] = useState({ patient: "", appointment_type: "", window_start: "", window_end: "", urgency: "3" });
  const [offerEntryId, setOfferEntryId] = useState<string | null>(null);
  const [offerStart, setOfferStart] = useState("");
  const [recDate, setRecDate] = useState("");
  const [recType, setRecType] = useState("");
  const [gapFrom, setGapFrom] = useState("");
  const [gapTo, setGapTo] = useState("");

  // Marketing
  const [segmentName, setSegmentName] = useState("");
  const [logical, setLogical] = useState<"all" | "any">("all");
  const [conditions, setConditions] = useState<Condition[]>([EMPTY_CONDITION]);
  const [openCampaignMetrics, setOpenCampaignMetrics] = useState<string | null>(null);

  const messagesQuery = useQuery({
    queryKey: ["automations", "messages", messageStatus],
    queryFn: async () => {
      const params: MessageListParams = { status: messageStatus || undefined, page_size: PAGE_SIZE };
      return toPage<Message>(await messagesList(params));
    },
    enabled: tab === "messages",
  });

  const consentsQuery = useQuery({
    queryKey: ["automations", "consents", consentStatus],
    queryFn: async () => {
      const params: ConsentListParams = { status: consentStatus || undefined, page_size: PAGE_SIZE };
      return toPage<Consent>(await consentsList(params));
    },
    enabled: tab === "consent",
  });

  const recallTypesQuery = useQuery({
    queryKey: ["automations", "recall-types"],
    queryFn: async () => toPage<RecallType>(await recallTypesList({ page_size: 50 })),
    enabled: tab === "recall",
  });

  const recallDueQuery = useQuery({
    queryKey: ["automations", "recall-due"],
    queryFn: () => recallDueList(),
    enabled: tab === "recall",
  });

  const appointmentTypesQuery = useQuery({
    queryKey: ["automations", "appointment-types"],
    queryFn: async () => toPage<AppointmentType>(await appointmentTypesList({ page_size: 50 })),
    enabled: tab === "waitlist" || tab === "recall",
  });

  const waitlistQuery = useQuery({
    queryKey: ["automations", "waitlist", waitlistStatus],
    queryFn: async () => {
      const params: WaitlistListQueryParams = { status: waitlistStatus || undefined, page_size: PAGE_SIZE };
      return toPage<WaitlistEntry>(await waitlistList(params));
    },
    enabled: tab === "waitlist",
  });

  const recommendationsQuery = useQuery({
    queryKey: ["automations", "recommendations", recDate, recType],
    queryFn: () => schedulingRecommendationsList({ date: recDate, type: recType }),
    enabled: tab === "waitlist" && recDate !== "" && recType !== "",
  });

  const gapsQuery = useQuery({
    queryKey: ["automations", "gaps", gapFrom, gapTo],
    queryFn: () => schedulingGapsList({ from: gapFrom, to: gapTo }),
    enabled: tab === "waitlist" && gapFrom !== "" && gapTo !== "",
  });

  const segmentsQuery = useQuery({
    queryKey: ["automations", "segments"],
    queryFn: async () => toPage<Segment>(await segmentsList({ page_size: 50 })),
    enabled: tab === "marketing",
  });

  const campaignsQuery = useQuery({
    queryKey: ["automations", "campaigns"],
    queryFn: async () => {
      const params: CampaignsListParams = { page_size: 50 };
      return toPage<Campaign>(await campaignsList(params));
    },
    enabled: tab === "marketing",
  });

  const previewMutation = useMutation({
    mutationFn: () =>
      segmentsPreviewCreate({
        predicate_json: { [logical]: conditions.map((condition) => ({ field: condition.field, op: condition.op, value: parseConditionValue(condition) })) },
      }),
    onError: (error) => toast({ title: "Preview failed", description: apiErrorMessage(error), variant: "destructive" }),
  });

  const segmentCreateMutation = useMutation({
    mutationFn: () =>
      segmentsCreate({
        name: segmentName.trim(),
        is_active: true,
        predicate_json: { [logical]: conditions.map((condition) => ({ field: condition.field, op: condition.op, value: parseConditionValue(condition) })) },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["automations", "segments"] });
      setSegmentName("");
      previewMutation.reset();
      toast({ title: "Segment created" });
    },
    onError: (error) => {
      const fieldErrors = apiErrorFieldErrors(error);
      const predicateError = fieldErrors.predicate_json?.[0];
      toast({ title: "Could not create segment", description: predicateError ?? apiErrorMessage(error), variant: "destructive" });
    },
  });

  const campaignAction = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "approve" | "activate" | "pause" }) =>
      action === "approve"
        ? campaignsApproveCreate(id)
        : action === "activate"
          ? campaignsActivateCreate(id)
          : campaignsPauseCreate(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["automations", "campaigns"] });
      toast({ title: "Campaign updated" });
    },
    onError: (error) => toast({ title: "Action failed", description: apiErrorMessage(error), variant: "destructive" }),
  });

  const revokeMutation = useMutation({
    mutationFn: (consentId: string) => consentsRevokeCreate(consentId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["automations", "consents"] });
      toast({ title: "Consent revoked" });
    },
    onError: (error) => toast({ title: "Revoke failed", description: apiErrorMessage(error), variant: "destructive" }),
  });

  const recallScheduleMutation = useMutation({
    mutationFn: () => recallScheduleCreate(recallPatient.trim(), { recall_type: recallType }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["automations", "recall-due"] });
      setRecallPatient("");
      toast({ title: "Recall scheduled" });
    },
    onError: (error) => toast({ title: "Schedule failed", description: apiErrorMessage(error), variant: "destructive" }),
  });

  const entryCreateMutation = useMutation({
    mutationFn: () =>
      waitlistCreate({
        patient: newEntry.patient.trim(),
        appointment_type: newEntry.appointment_type,
        window_start: newEntry.window_start,
        window_end: newEntry.window_end,
        urgency: Number.parseInt(newEntry.urgency, 10) || 3,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["automations", "waitlist"] });
      setNewEntry({ patient: "", appointment_type: "", window_start: "", window_end: "", urgency: "3" });
      toast({ title: "Waitlist entry created" });
    },
    onError: (error) => toast({ title: "Could not add entry", description: apiErrorMessage(error), variant: "destructive" }),
  });

  const offerCreateMutation = useMutation({
    mutationFn: () => waitlistOffersCreate(offerEntryId as string, { start_at: offerStart }),
    onSuccess: (offer) => {
      void queryClient.invalidateQueries({ queryKey: ["automations", "waitlist"] });
      setOfferEntryId(null);
      setOfferStart("");
      toast({ title: "Offer created", description: offer.token ? `Token ${offer.token.slice(0, 8)}…` : "Offer sent." });
    },
    onError: (error) => toast({ title: "Offer failed", description: apiErrorMessage(error), variant: "destructive" }),
  });

  const offerClaimMutation = useMutation({
    mutationFn: (token: string) => offersClaimCreate(token),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["automations", "waitlist"] });
      toast({ title: "Offer claimed" });
    },
    onError: (error) => toast({ title: "Claim failed", description: apiErrorMessage(error), variant: "destructive" }),
  });

  const offerDeclineMutation = useMutation({
    mutationFn: (token: string) => offersDeclineCreate(token),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["automations", "waitlist"] });
      toast({ title: "Offer declined" });
    },
    onError: (error) => toast({ title: "Decline failed", description: apiErrorMessage(error), variant: "destructive" }),
  });

  const appointmentTypes = appointmentTypesQuery.data?.results ?? [];
  const messages = messagesQuery.data?.results ?? [];
  const reasonFilter = messageReason.trim().toLowerCase();
  const visibleMessages = reasonFilter
    ? messages.filter((message) => message.reason.toLowerCase().includes(reasonFilter) || message.status.toLowerCase().includes(reasonFilter))
    : messages;

  const previewCount = previewMutation.data ? readNumber(previewMutation.data, "count") : null;
  const previewSample = previewMutation.data ? readRecordArray(previewMutation.data, "sample") : [];

  function submitSegment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (segmentName.trim() === "") {
      toast({ title: "Name required", description: "Give the segment a name.", variant: "destructive" });
      return;
    }
    segmentCreateMutation.mutate();
  }

  return (
    <AppLayout>
      <div className="max-w-screen-2xl mx-auto space-y-8">
        <header>
          <span className="text-[10px] font-bold tracking-[0.2em] text-primary uppercase mb-2 block font-sans">
            Patient CRM
          </span>
          <h2 className="text-4xl font-extrabold text-foreground font-display leading-none">Automations</h2>
        </header>

        <div className="flex flex-wrap gap-2">
          {(["messages", "consent", "recall", "waitlist", "marketing"] as const).map((value) => (
            <button
              key={value}
              type="button"
              data-testid={`automations-tab-${value}`}
              onClick={() => setTab(value)}
              className={`px-5 py-2.5 rounded-full text-xs font-bold uppercase tracking-wider transition-colors capitalize ${
                tab === value ? "bg-primary text-white shadow-md" : "bg-background border border-border text-muted-foreground hover:bg-muted"
              }`}
            >
              {value}
            </button>
          ))}
        </div>

        {tab === "messages" ? (
          <section className="glass-card rounded-[2rem] p-8 shadow-sm">
            <div className="flex flex-wrap gap-3 mb-6">
              <select
                data-testid="message-status-filter"
                value={messageStatus}
                onChange={(event) => setMessageStatus(event.target.value)}
                className={`${INPUT_CLASS} w-auto`}
                aria-label="Filter messages by status"
              >
                {MESSAGE_STATUSES.map((value) => (
                  <option key={value || "all"} value={value}>
                    {value === "" ? "All statuses" : value}
                  </option>
                ))}
              </select>
              <input
                data-testid="message-reason-filter"
                value={messageReason}
                onChange={(event) => setMessageReason(event.target.value)}
                placeholder="Filter by reason / status text…"
                className={`${INPUT_CLASS} flex-1 min-w-[220px]`}
                aria-label="Filter messages by reason"
              />
            </div>
            <DataState
              loading={messagesQuery.isPending}
              error={messagesQuery.error}
              empty={visibleMessages.length === 0}
              emptyTitle="No messages"
              emptyMessage="No reminders or messages match the current filters."
              onRetry={() => void messagesQuery.refetch()}
            >
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="text-[10px] uppercase tracking-widest text-muted-foreground border-b border-border">
                    <th className="py-3 pr-4 font-bold">Patient</th>
                    <th className="py-3 pr-4 font-bold">Channel</th>
                    <th className="py-3 pr-4 font-bold">Purpose</th>
                    <th className="py-3 pr-4 font-bold">Status</th>
                    <th className="py-3 pr-4 font-bold">Reason</th>
                    <th className="py-3 pr-4 font-bold">Created</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/60">
                  {visibleMessages.map((message) => (
                    <tr key={message.id} className="hover:bg-muted/40 transition-colors">
                      <td className="py-3 pr-4 font-mono text-xs">{message.patient.slice(0, 8)}…</td>
                      <td className="py-3 pr-4">{message.channel}</td>
                      <td className="py-3 pr-4">{message.purpose}</td>
                      <td className="py-3 pr-4">
                        <span className="px-2.5 py-1 rounded-full text-[10px] font-bold uppercase bg-muted text-muted-foreground">
                          {message.status}
                        </span>
                      </td>
                      <td className="py-3 pr-4 text-muted-foreground">{message.reason || "—"}</td>
                      <td className="py-3 pr-4 text-muted-foreground">{formatDateTime(message.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </DataState>
          </section>
        ) : null}

        {tab === "consent" ? (
          <section className="glass-card rounded-[2rem] p-8 shadow-sm">
            <div className="flex items-center justify-between mb-6">
              <div>
                <h3 className="text-xl font-bold font-display">Consent register</h3>
                <p className="text-sm text-muted-foreground mt-1">Channel consent per patient. Revoking is immediate.</p>
              </div>
              <select
                data-testid="consent-status-filter"
                value={consentStatus}
                onChange={(event) => setConsentStatus(event.target.value)}
                className={`${INPUT_CLASS} w-auto`}
                aria-label="Filter consents by status"
              >
                {CONSENT_STATUSES.map((value) => (
                  <option key={value || "all"} value={value}>
                    {value === "" ? "All statuses" : value}
                  </option>
                ))}
              </select>
            </div>
            <DataState
              loading={consentsQuery.isPending}
              error={consentsQuery.error}
              empty={(consentsQuery.data?.results ?? []).length === 0}
              emptyTitle="No consent records"
              emptyMessage="No consent has been captured for this clinic yet."
              onRetry={() => void consentsQuery.refetch()}
            >
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="text-[10px] uppercase tracking-widest text-muted-foreground border-b border-border">
                    <th className="py-3 pr-4 font-bold">Patient</th>
                    <th className="py-3 pr-4 font-bold">Channel</th>
                    <th className="py-3 pr-4 font-bold">Purpose</th>
                    <th className="py-3 pr-4 font-bold">Status</th>
                    <th className="py-3 pr-4 font-bold">Granted</th>
                    <th className="py-3 pr-4 font-bold text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/60">
                  {(consentsQuery.data?.results ?? []).map((consent) => (
                    <tr key={consent.id} className="hover:bg-muted/40 transition-colors">
                      <td className="py-3 pr-4 font-mono text-xs">{consent.patient.slice(0, 8)}…</td>
                      <td className="py-3 pr-4">{consent.channel}</td>
                      <td className="py-3 pr-4">{consent.purpose}</td>
                      <td className="py-3 pr-4">
                        <span className={`px-2.5 py-1 rounded-full text-[10px] font-bold uppercase ${consent.status === "granted" ? "bg-emerald-100 text-emerald-700" : "bg-destructive/10 text-destructive"}`}>
                          {consent.status}
                        </span>
                      </td>
                      <td className="py-3 pr-4 text-muted-foreground">{formatDateTime(consent.granted_at)}</td>
                      <td className="py-3 pr-4 text-right">
                        {consent.status === "granted" ? (
                          <button
                            type="button"
                            disabled={revokeMutation.isPending}
                            onClick={() => revokeMutation.mutate(consent.id)}
                            className="text-destructive font-bold hover:underline disabled:opacity-50"
                          >
                            Revoke
                          </button>
                        ) : (
                          <span className="text-muted-foreground text-xs">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </DataState>
          </section>
        ) : null}

        {tab === "recall" ? (
          <section className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 glass-card rounded-[2rem] p-8 shadow-sm">
              <h3 className="text-xl font-bold font-display mb-4">Recall due</h3>
              <DataState
                loading={recallDueQuery.isPending}
                error={recallDueQuery.error}
                empty={(recallDueQuery.data ?? []).length === 0}
                emptyTitle="No recalls due"
                emptyMessage="No patient has a recall due right now."
                onRetry={() => void recallDueQuery.refetch()}
              >
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="text-[10px] uppercase tracking-widest text-muted-foreground border-b border-border">
                      <th className="py-3 pr-4 font-bold">Patient</th>
                      <th className="py-3 pr-4 font-bold">Type</th>
                      <th className="py-3 pr-4 font-bold">Channel</th>
                      <th className="py-3 pr-4 font-bold">Due</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/60">
                    {(recallDueQuery.data ?? []).map((due) => (
                      <tr key={due.id} className="hover:bg-muted/40 transition-colors">
                        <td className="py-3 pr-4 font-mono text-xs">{due.patient_id.slice(0, 8)}…</td>
                        <td className="py-3 pr-4">{due.recall_type_name}</td>
                        <td className="py-3 pr-4">{due.channel}</td>
                        <td className="py-3 pr-4 text-muted-foreground">{formatDateTime(due.due_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </DataState>
            </div>
            <div className="glass-card rounded-[2rem] p-8 shadow-sm">
              <h3 className="text-xl font-bold font-display mb-4">Schedule a recall</h3>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  recallScheduleMutation.mutate();
                }}
                className="space-y-4"
              >
                <input
                  data-testid="recall-patient"
                  value={recallPatient}
                  onChange={(event) => setRecallPatient(event.target.value)}
                  placeholder="Patient id (UUID)"
                  className={INPUT_CLASS}
                />
                <select
                  data-testid="recall-type"
                  value={recallType}
                  onChange={(event) => setRecallType(event.target.value)}
                  className={INPUT_CLASS}
                >
                  <option value="">Select recall type…</option>
                  {(recallTypesQuery.data?.results ?? []).map((type) => (
                    <option key={type.id} value={type.id}>
                      {type.name}
                    </option>
                  ))}
                </select>
                <button
                  type="submit"
                  data-testid="recall-submit"
                  disabled={recallScheduleMutation.isPending || recallPatient.trim() === "" || recallType === ""}
                  className="w-full bg-gradient-to-br from-primary to-primary-dim text-white py-3 rounded-xl font-bold text-sm hover:opacity-90 transition-all disabled:opacity-50"
                >
                  Schedule recall
                </button>
              </form>
            </div>
          </section>
        ) : null}

        {tab === "waitlist" ? (
          <div className="space-y-6">
            <section className="glass-card rounded-[2rem] p-8 shadow-sm">
              <div className="flex items-center justify-between mb-6">
                <h3 className="text-xl font-bold font-display">Waitlist entries</h3>
                <select
                  data-testid="waitlist-status-filter"
                  value={waitlistStatus}
                  onChange={(event) => setWaitlistStatus(event.target.value)}
                  className={`${INPUT_CLASS} w-auto`}
                  aria-label="Filter waitlist by status"
                >
                  {WAITLIST_STATUSES.map((value) => (
                    <option key={value || "all"} value={value}>
                      {value === "" ? "All statuses" : value}
                    </option>
                  ))}
                </select>
              </div>
              <DataState
                loading={waitlistQuery.isPending}
                error={waitlistQuery.error}
                empty={(waitlistQuery.data?.results ?? []).length === 0}
                emptyTitle="No waitlist entries"
                emptyMessage="No patient is on the waitlist."
                onRetry={() => void waitlistQuery.refetch()}
              >
                <div className="space-y-3">
                  {(waitlistQuery.data?.results ?? []).map((entry) => (
                    <div key={entry.id} className="border border-border rounded-2xl p-4 flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <p className="font-bold text-foreground font-mono text-sm">{entry.patient.slice(0, 8)}…</p>
                        <p className="text-xs text-muted-foreground mt-1">
                          {entry.status} · urgency {entry.urgency} · {formatDateTime(entry.window_start)} → {formatDateTime(entry.window_end)}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => setOfferEntryId(entry.id)}
                          className="text-primary font-bold text-xs hover:underline"
                        >
                          Create offer
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </DataState>

              {offerEntryId ? (
                <div className="mt-6 border-t border-border pt-6">
                  <h4 className="text-sm font-bold mb-3">Create offer for entry {offerEntryId.slice(0, 8)}…</h4>
                  <div className="flex flex-wrap gap-3">
                    <input
                      data-testid="offer-start"
                      type="datetime-local"
                      value={offerStart}
                      onChange={(event) => setOfferStart(event.target.value)}
                      className={`${INPUT_CLASS} w-auto`}
                    />
                    <button
                      type="button"
                      data-testid="offer-submit"
                      disabled={offerCreateMutation.isPending || offerStart === ""}
                      onClick={() => offerCreateMutation.mutate()}
                      className="bg-gradient-to-br from-primary to-primary-dim text-white px-6 py-3 rounded-xl font-bold text-sm hover:opacity-90 transition-all disabled:opacity-50"
                    >
                      Send offer
                    </button>
                    <button
                      type="button"
                      onClick={() => setOfferEntryId(null)}
                      className="border border-border px-6 py-3 rounded-xl font-bold text-sm text-muted-foreground hover:bg-muted transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                  {offerStart === "" ? null : (
                    <p className="text-[11px] text-muted-foreground mt-2">
                      Offer start will be sent as {offerStart} (local clinic time).
                    </p>
                  )}
                </div>
              ) : null}
            </section>

            <section className="glass-card rounded-[2rem] p-8 shadow-sm">
              <h3 className="text-xl font-bold font-display mb-4">Add waitlist entry</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <input
                  data-testid="waitlist-patient"
                  value={newEntry.patient}
                  onChange={(event) => setNewEntry((s) => ({ ...s, patient: event.target.value }))}
                  placeholder="Patient id (UUID)"
                  className={INPUT_CLASS}
                />
                <select
                  data-testid="waitlist-type"
                  value={newEntry.appointment_type}
                  onChange={(event) => setNewEntry((s) => ({ ...s, appointment_type: event.target.value }))}
                  className={INPUT_CLASS}
                >
                  <option value="">Appointment type…</option>
                  {appointmentTypes.map((type) => (
                    <option key={type.id} value={type.id}>
                      {type.name}
                    </option>
                  ))}
                </select>
                <input
                  data-testid="waitlist-window-start"
                  type="datetime-local"
                  value={newEntry.window_start}
                  onChange={(event) => setNewEntry((s) => ({ ...s, window_start: event.target.value }))}
                  className={INPUT_CLASS}
                />
                <input
                  data-testid="waitlist-window-end"
                  type="datetime-local"
                  value={newEntry.window_end}
                  onChange={(event) => setNewEntry((s) => ({ ...s, window_end: event.target.value }))}
                  className={INPUT_CLASS}
                />
                <select
                  value={newEntry.urgency}
                  onChange={(event) => setNewEntry((s) => ({ ...s, urgency: event.target.value }))}
                  className={INPUT_CLASS}
                >
                  {["1", "2", "3", "4", "5"].map((value) => (
                    <option key={value} value={value}>
                      Urgency {value}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  data-testid="waitlist-submit"
                  disabled={entryCreateMutation.isPending || newEntry.patient.trim() === "" || newEntry.appointment_type === "" || newEntry.window_start === "" || newEntry.window_end === ""}
                  onClick={() => entryCreateMutation.mutate()}
                  className="bg-gradient-to-br from-primary to-primary-dim text-white py-3 rounded-xl font-bold text-sm hover:opacity-90 transition-all disabled:opacity-50"
                >
                  Add to waitlist
                </button>
              </div>
            </section>

            <section className="glass-card rounded-[2rem] p-8 shadow-sm">
              <h3 className="text-xl font-bold font-display mb-4">Recommendations &amp; gaps</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                <div>
                  <div className="flex flex-wrap gap-3 mb-4">
                    <input
                      data-testid="rec-date"
                      type="date"
                      value={recDate}
                      onChange={(event) => setRecDate(event.target.value)}
                      className={`${INPUT_CLASS} w-auto`}
                      aria-label="Recommendation date"
                    />
                    <select
                      data-testid="rec-type"
                      value={recType}
                      onChange={(event) => setRecType(event.target.value)}
                      className={`${INPUT_CLASS} w-auto`}
                      aria-label="Recommendation appointment type"
                    >
                      <option value="">Appointment type…</option>
                      {appointmentTypes.map((type) => (
                        <option key={type.id} value={type.id}>
                          {type.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <DataState
                    loading={recommendationsQuery.isPending && recDate !== "" && recType !== ""}
                    error={recommendationsQuery.error}
                    empty={recDate !== "" && recType !== "" && (recommendationsQuery.data ?? []).length === 0}
                    emptyTitle="No recommendations"
                    emptyMessage="No backfill candidates for this day and type."
                    onRetry={() => void recommendationsQuery.refetch()}
                  >
                    {recDate === "" || recType === "" ? (
                      <p className="text-sm text-muted-foreground">Pick a date and appointment type to see recommendations.</p>
                    ) : (
                      <div className="space-y-3">
                        {(recommendationsQuery.data ?? []).map((rec: Recommendation) => (
                          <div key={`${rec.entry_id}-${rec.start_at}`} className="border border-border rounded-xl p-3 text-sm">
                            <p className="font-bold">{formatDateTime(rec.start_at)} → {formatDateTime(rec.end_at)}</p>
                            <p className="text-xs text-muted-foreground mt-1">score {rec.total.toFixed(2)} · {rec.gap_reason}</p>
                            <ul className="mt-2 flex flex-wrap gap-1.5">
                              {rec.reasons.map((reason) => (
                                <li key={reason} className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-primary/10 text-primary">
                                  {reason}
                                </li>
                              ))}
                            </ul>
                          </div>
                        ))}
                      </div>
                    )}
                  </DataState>
                </div>
                <div>
                  <div className="flex flex-wrap gap-3 mb-4">
                    <input
                      data-testid="gap-from"
                      type="date"
                      value={gapFrom}
                      onChange={(event) => setGapFrom(event.target.value)}
                      className={`${INPUT_CLASS} w-auto`}
                      aria-label="Gaps from"
                    />
                    <input
                      data-testid="gap-to"
                      type="date"
                      value={gapTo}
                      onChange={(event) => setGapTo(event.target.value)}
                      className={`${INPUT_CLASS} w-auto`}
                      aria-label="Gaps to"
                    />
                  </div>
                  <DataState
                    loading={gapsQuery.isPending && gapFrom !== "" && gapTo !== ""}
                    error={gapsQuery.error}
                    empty={gapFrom !== "" && gapTo !== "" && (gapsQuery.data ?? []).length === 0}
                    emptyTitle="No gaps"
                    emptyMessage="No unfilled gaps in this window."
                    onRetry={() => void gapsQuery.refetch()}
                  >
                    {gapFrom === "" || gapTo === "" ? (
                      <p className="text-sm text-muted-foreground">Pick a window to list scheduling gaps.</p>
                    ) : (
                      <div className="space-y-2">
                        {(gapsQuery.data ?? []).map((gap: Gap, index) => (
                          <div key={`${gap.start}-${index}`} className="border border-border rounded-xl p-3 text-xs">
                            <p className="font-bold text-foreground">{formatDateTime(gap.start)} → {formatDateTime(gap.end)}</p>
                            <p className="text-muted-foreground mt-1">{gap.reason}</p>
                          </div>
                        ))}
                      </div>
                    )}
                  </DataState>
                </div>
              </div>
            </section>
          </div>
        ) : null}

        {tab === "marketing" ? (
          <div className="space-y-6">
            <section className="glass-card rounded-[2rem] p-8 shadow-sm">
              <h3 className="text-xl font-bold font-display mb-4 flex items-center gap-2">
                <Sparkles size={18} className="text-primary" /> Segments
              </h3>
              <DataState
                loading={segmentsQuery.isPending}
                error={segmentsQuery.error}
                empty={(segmentsQuery.data?.results ?? []).length === 0}
                emptyTitle="No segments"
                emptyMessage="Build a non-clinical segment to target a campaign."
                onRetry={() => void segmentsQuery.refetch()}
              >
                <div className="space-y-2 mb-8">
                  {(segmentsQuery.data?.results ?? []).map((segment) => (
                    <div key={segment.id} className="border border-border rounded-xl p-3 flex items-center justify-between gap-3">
                      <div>
                        <p className="font-bold text-foreground">{segment.name}</p>
                        <p className="text-[11px] font-mono text-muted-foreground mt-1">{JSON.stringify(segment.predicate_json)}</p>
                      </div>
                      <span className={`text-[10px] font-bold px-2.5 py-1 rounded-full ${segment.is_active ? "bg-emerald-100 text-emerald-700" : "bg-muted text-muted-foreground"}`}>
                        {segment.is_active ? "active" : "inactive"}
                      </span>
                    </div>
                  ))}
                </div>
              </DataState>

              <form onSubmit={submitSegment} className="border-t border-border pt-6 space-y-4">
                <div className="flex flex-wrap gap-3">
                  <input
                    data-testid="segment-name"
                    value={segmentName}
                    onChange={(event) => setSegmentName(event.target.value)}
                    placeholder="Segment name"
                    className={`${INPUT_CLASS} flex-1 min-w-[200px]`}
                  />
                  <select
                    data-testid="segment-logical"
                    value={logical}
                    onChange={(event) => setLogical(event.target.value as "all" | "any")}
                    className={`${INPUT_CLASS} w-auto`}
                    aria-label="Match all or any"
                  >
                    <option value="all">Match all</option>
                    <option value="any">Match any</option>
                  </select>
                </div>

                <div className="space-y-2">
                  {conditions.map((condition, index) => (
                    <div key={index} className="flex flex-wrap gap-2 items-center">
                      <select
                        value={condition.field}
                        onChange={(event) =>
                          setConditions((current) => current.map((c, i) => (i === index ? { ...c, field: event.target.value } : c)))
                        }
                        className={`${INPUT_CLASS} w-auto`}
                        aria-label="Segment field"
                      >
                        {SEGMENT_FIELDS.map((field) => (
                          <option key={field} value={field}>
                            {field}
                          </option>
                        ))}
                      </select>
                      <select
                        value={condition.op}
                        onChange={(event) =>
                          setConditions((current) => current.map((c, i) => (i === index ? { ...c, op: event.target.value } : c)))
                        }
                        className={`${INPUT_CLASS} w-auto`}
                        aria-label="Segment operator"
                      >
                        {SEGMENT_OPS.map((op) => (
                          <option key={op} value={op}>
                            {op}
                          </option>
                        ))}
                      </select>
                      <input
                        value={condition.value}
                        onChange={(event) =>
                          setConditions((current) => current.map((c, i) => (i === index ? { ...c, value: event.target.value } : c)))
                        }
                        placeholder={condition.op === "in" || condition.op === "between" ? "comma-separated" : "value"}
                        className={`${INPUT_CLASS} flex-1 min-w-[120px]`}
                        aria-label="Segment value"
                      />
                      <button
                        type="button"
                        onClick={() => setConditions((current) => current.filter((_, i) => i !== index))}
                        disabled={conditions.length === 1}
                        className="p-3 text-muted-foreground hover:text-destructive disabled:opacity-40"
                        aria-label="Remove condition"
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    onClick={() => setConditions((current) => [...current, EMPTY_CONDITION])}
                    className="inline-flex items-center gap-2 text-xs font-bold text-primary hover:underline"
                  >
                    <Plus size={14} /> Add condition
                  </button>
                </div>

                <div className="flex flex-wrap gap-3">
                  <button
                    type="button"
                    data-testid="segment-preview"
                    disabled={previewMutation.isPending}
                    onClick={() => previewMutation.mutate()}
                    className="inline-flex items-center gap-2 border border-border px-5 py-3 rounded-xl font-bold text-sm hover:bg-muted transition-colors disabled:opacity-50"
                  >
                    <RefreshCcw size={14} /> Preview
                  </button>
                  <button
                    type="submit"
                    data-testid="segment-submit"
                    disabled={segmentCreateMutation.isPending}
                    className="inline-flex items-center gap-2 bg-gradient-to-br from-primary to-primary-dim text-white px-6 py-3 rounded-xl font-bold text-sm hover:opacity-90 transition-all disabled:opacity-50"
                  >
                    Create segment
                  </button>
                </div>

                {previewCount !== null ? (
                  <div className="bg-muted/40 rounded-xl p-4 text-sm">
                    <p className="font-bold text-foreground">{previewCount} patient(s) match this predicate.</p>
                    {previewSample.length > 0 ? (
                      <ul className="mt-2 text-xs text-muted-foreground space-y-1">
                        {previewSample.map((row, index) => (
                          <li key={index}>{readString(row, "name") || readString(row, "patient_id")}</li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                ) : null}
              </form>
            </section>

            <section className="glass-card rounded-[2rem] p-8 shadow-sm">
              <h3 className="text-xl font-bold font-display mb-4">Campaigns</h3>
              <DataState
                loading={campaignsQuery.isPending}
                error={campaignsQuery.error}
                empty={(campaignsQuery.data?.results ?? []).length === 0}
                emptyTitle="No campaigns"
                emptyMessage="No marketing campaign has been created."
                onRetry={() => void campaignsQuery.refetch()}
              >
                <div className="space-y-3">
                  {(campaignsQuery.data?.results ?? []).map((campaign) => (
                    <div key={campaign.id} className="border border-border rounded-2xl p-4">
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                          <p className="font-bold text-foreground">{campaign.name}</p>
                          <p className="text-xs text-muted-foreground mt-1">
                            {campaign.channel} · {campaign.status}
                          </p>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          {campaign.status === "draft" || campaign.status === "pending_approval" ? (
                            <ActionButton label="Approve" icon={<CheckCircle2 size={13} />} onClick={() => campaignAction.mutate({ id: campaign.id, action: "approve" })} />
                          ) : null}
                          {campaign.status === "approved" || campaign.status === "paused" ? (
                            <ActionButton label="Activate" icon={<Send size={13} />} onClick={() => campaignAction.mutate({ id: campaign.id, action: "activate" })} />
                          ) : null}
                          {campaign.status === "active" ? (
                            <ActionButton label="Pause" icon={<XCircle size={13} />} onClick={() => campaignAction.mutate({ id: campaign.id, action: "pause" })} />
                          ) : null}
                          <button
                            type="button"
                            onClick={() => setOpenCampaignMetrics(openCampaignMetrics === campaign.id ? null : campaign.id)}
                            className="text-xs font-bold text-primary hover:underline"
                          >
                            {openCampaignMetrics === campaign.id ? "Hide metrics" : "Metrics"}
                          </button>
                        </div>
                      </div>
                      {openCampaignMetrics === campaign.id ? <CampaignMetricsPanel campaignId={campaign.id} /> : null}
                    </div>
                  ))}
                </div>
              </DataState>
            </section>
          </div>
        ) : null}
      </div>
    </AppLayout>
  );
}

function ActionButton({ label, icon, onClick }: { label: string; icon: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1.5 border border-border px-4 py-2 rounded-full text-xs font-bold hover:bg-muted transition-colors"
    >
      {icon} {label}
    </button>
  );
}
