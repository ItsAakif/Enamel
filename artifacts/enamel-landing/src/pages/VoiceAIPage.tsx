import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  ChevronRight,
  Clock,
  MessageSquare,
  Mic,
  Phone,
  PhoneCall,
  Play,
  Send,
  ShieldAlert,
  Wrench,
} from "lucide-react";
import {
  aiCallsCreate,
  aiCallsEscalate,
  aiCallsList,
  aiCallsRetrieve,
  aiCallsToolsInvoke,
  aiCallsTurnsCreate,
} from "@workspace/api-client-react";
import AppLayout from "@/layouts/AppLayout";
import { DataState } from "@/components/common/DataState";
import { useToast } from "@/hooks/use-toast";
import {
  apiErrorMessage,
  readBoolean,
  readNumber,
  readRecordArray,
  readString,
  toPage,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";

const INPUT_CLASS =
  "w-full border border-border rounded-xl px-4 py-3 text-sm font-medium bg-white/70 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all";

const TOOLS = [
  "lookup_faq",
  "book_appointment",
  "reschedule_appointment",
  "cancel_appointment",
  "escalate_to_human",
] as const;

interface CallTurn {
  id: string;
  role: string;
  kind: string;
  ordinal: number;
  text: string;
  confidence: number | null;
  createdAt: string;
}

interface CallToolCall {
  id: string;
  tool: string;
  status: string;
  confirmed: boolean;
  argsJson: string;
  resultJson: string;
  createdAt: string;
}

interface CallView {
  id: string;
  mode: string;
  direction: string;
  adapter: string;
  status: string;
  patientId: string | null;
  summary: string;
  outcome: string;
  startedAt: string;
  endedAt: string | null;
  latencyMs: number;
  turns: CallTurn[];
  toolCalls: CallToolCall[];
}

function readTurn(source: unknown): CallTurn {
  return {
    id: readString(source, "id"),
    role: readString(source, "role"),
    kind: readString(source, "kind"),
    ordinal: readNumber(source, "ordinal") ?? 0,
    text: readString(source, "text"),
    confidence: readNumber(source, "confidence"),
    createdAt: readString(source, "created_at"),
  };
}

function readToolCall(source: unknown): CallToolCall {
  const args = source && typeof source === "object" ? (source as Record<string, unknown>).args_json : null;
  const result = source && typeof source === "object" ? (source as Record<string, unknown>).result_json : null;
  return {
    id: readString(source, "id"),
    tool: readString(source, "tool"),
    status: readString(source, "status"),
    confirmed: readBoolean(source, "confirmed"),
    argsJson: args === undefined ? "" : JSON.stringify(args),
    resultJson: result === undefined || result === null ? "" : JSON.stringify(result),
    createdAt: readString(source, "created_at"),
  };
}

function readCall(source: unknown): CallView {
  return {
    id: readString(source, "id"),
    mode: readString(source, "mode"),
    direction: readString(source, "direction"),
    adapter: readString(source, "adapter"),
    status: readString(source, "status"),
    patientId: readString(source, "patient_id") || null,
    summary: readString(source, "summary"),
    outcome: readString(source, "outcome"),
    startedAt: readString(source, "started_at"),
    endedAt: readString(source, "ended_at") || null,
    latencyMs: readNumber(source, "latency_ms") ?? 0,
    turns: readRecordArray(source, "turns").map(readTurn),
    toolCalls: readRecordArray(source, "tool_calls").map(readToolCall),
  };
}

/** The API labels the local-test adapter; the UI shows "Simulator". */
function modeLabel(call: CallView): string {
  if (call.mode === "local_test" || call.adapter === "local_test") return "Simulator";
  return call.mode || call.adapter || "Unknown";
}

const STATUS_BADGE: Record<string, string> = {
  active: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30",
  completed: "bg-sky-100 text-sky-700 dark:bg-sky-900/30",
  escalated: "bg-amber-100 text-amber-700 dark:bg-amber-900/30",
  failed: "bg-destructive/10 text-destructive",
};

export default function VoiceAIPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [turnText, setTurnText] = useState("");
  const [tool, setTool] = useState<string>(TOOLS[0]);
  const [toolArgs, setToolArgs] = useState("{}");

  const callsQuery = useQuery({
    queryKey: ["voice-ai", "calls"],
    queryFn: async () => {
      return toPage<unknown>(await aiCallsList());
    },
  });

  const calls = useMemo(
    () => (callsQuery.data?.results ?? []).map(readCall),
    [callsQuery.data],
  );

  useEffect(() => {
    if (selectedId === null && calls.length > 0) setSelectedId(calls[0].id);
  }, [calls, selectedId]);

  const detailQuery = useQuery({
    queryKey: ["voice-ai", "call", selectedId],
    queryFn: () => aiCallsRetrieve(selectedId as string),
    enabled: selectedId !== null,
  });
  const detail = detailQuery.data === undefined ? null : readCall(detailQuery.data);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["voice-ai", "calls"] });
    void queryClient.invalidateQueries({ queryKey: ["voice-ai", "call"] });
  };

  const startMutation = useMutation({
    mutationFn: () => aiCallsCreate({ recording_consent: false }),
    onSuccess: (created) => {
      const call = readCall(created);
      setSelectedId(call.id);
      invalidate();
      toast({ title: "Simulated call started", description: `Mode: ${modeLabel(call)}.` });
    },
    onError: (error) => toast({ title: "Could not start call", description: apiErrorMessage(error), variant: "destructive" }),
  });

  const turnMutation = useMutation({
    mutationFn: ({ callId, text }: { callId: string; text: string }) =>
      aiCallsTurnsCreate(callId, { text, role: "caller" }),
    onSuccess: () => {
      setTurnText("");
      invalidate();
    },
    onError: (error) => toast({ title: "Turn failed", description: apiErrorMessage(error), variant: "destructive" }),
  });

  const toolMutation = useMutation({
    mutationFn: ({ callId, toolName, args }: { callId: string; toolName: string; args: Record<string, unknown> }) =>
      aiCallsToolsInvoke(callId, toolName, { args, confirmed: false }),
    onSuccess: (result) => {
      invalidate();
      const status = readString(result, "status");
      toast({ title: `Tool ${tool}`, description: status === "ok" ? "Tool call succeeded." : `Tool returned ${status || "an error"}.` });
    },
    onError: (error) => toast({ title: `Tool ${tool} failed`, description: apiErrorMessage(error), variant: "destructive" }),
  });

  const escalateMutation = useMutation({
    mutationFn: (callId: string) => aiCallsEscalate(callId, { reason_code: "caller_request" }),
    onSuccess: () => {
      invalidate();
      toast({ title: "Escalated", description: "The call was escalated to staff." });
    },
    onError: (error) => toast({ title: "Escalation failed", description: apiErrorMessage(error), variant: "destructive" }),
  });

  function submitTurn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail || turnText.trim() === "") return;
    turnMutation.mutate({ callId: detail.id, text: turnText.trim() });
  }

  function submitTool() {
    if (!detail) return;
    let parsed: Record<string, unknown>;
    try {
      const value = JSON.parse(toolArgs || "{}");
      if (value === null || typeof value !== "object" || Array.isArray(value)) {
        throw new Error("arguments must be a JSON object");
      }
      parsed = value as Record<string, unknown>;
    } catch {
      toast({ title: "Invalid arguments", description: "Tool arguments must be a JSON object.", variant: "destructive" });
      return;
    }
    toolMutation.mutate({ callId: detail.id, toolName: tool, args: parsed });
  }

  const activeCount = calls.filter((call) => call.status === "active").length;

  return (
    <AppLayout>
      <div className="max-w-screen-2xl mx-auto space-y-6">
        <header className="flex flex-wrap gap-4 justify-between items-end">
          <div>
            <span className="text-[10px] font-bold tracking-[0.2em] text-primary uppercase mb-2 block font-sans">
              Conversational AI
            </span>
            <h2 className="text-4xl font-extrabold text-foreground font-display leading-none">Voice AI</h2>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 bg-emerald-50 border border-emerald-200 text-emerald-700 px-4 py-2 rounded-full">
              <span className={`w-2 h-2 rounded-full ${activeCount > 0 ? "bg-emerald-500 animate-pulse" : "bg-muted-foreground"}`} />
              <span className="text-xs font-bold uppercase tracking-wider">
                {activeCount} Active Session{activeCount === 1 ? "" : "s"}
              </span>
            </div>
            <button
              type="button"
              data-testid="start-call-btn"
              disabled={startMutation.isPending}
              onClick={() => startMutation.mutate()}
              className="flex items-center gap-2 bg-gradient-to-br from-primary to-primary-dim text-white px-6 py-2.5 rounded-full font-bold text-sm shadow-lg shadow-primary/20 hover:opacity-90 transition-all disabled:opacity-60"
            >
              <Play size={16} /> Start simulated call
            </button>
          </div>
        </header>

        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
          <div className="xl:col-span-7 space-y-6">
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              className="relative rounded-[2rem] bg-zinc-950 overflow-hidden border border-border shadow-inner"
              style={{ minHeight: 280 }}
            >
              <div className="absolute inset-0 opacity-20 mix-blend-screen pointer-events-none">
                <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[500px] bg-primary/30 rounded-full blur-[80px]" />
              </div>
              <div className="relative z-10 flex flex-col items-center justify-center py-12 px-8 text-center">
                <div className="relative flex items-center justify-center mb-8 h-28">
                  <motion.div
                    animate={detail?.status === "active" ? { scale: [1, 1.2, 1] } : { scale: 1 }}
                    transition={{ repeat: Infinity, duration: 2 }}
                    className="absolute w-28 h-28 bg-primary/20 rounded-full blur-xl"
                  />
                  <div className="w-16 h-16 bg-gradient-to-br from-primary to-primary-dim rounded-full flex items-center justify-center shadow-[0_0_40px_rgba(70,71,211,0.5)] relative z-20">
                    <Mic size={28} className="text-white" />
                  </div>
                </div>
                {detail ? (
                  <>
                    <div className="inline-flex items-center gap-2 bg-white/5 border border-white/10 px-4 py-1.5 rounded-full mb-4">
                      <span className={`w-2 h-2 rounded-full ${detail.status === "active" ? "bg-emerald-400 animate-pulse" : "bg-zinc-500"}`} />
                      <span className="text-xs font-bold uppercase tracking-widest text-emerald-400">
                        {modeLabel(detail)} · {detail.status}
                      </span>
                    </div>
                    <h1 className="font-display text-2xl font-extrabold tracking-tight text-white mb-1">
                      {detail.patientId ? `Call for patient ${detail.patientId.slice(0, 8)}…` : "Unidentified caller"}
                    </h1>
                    <p className="text-zinc-400 text-sm">
                      Adapter: <span className="text-white italic">{detail.adapter || "unknown"}</span>
                      {detail.latencyMs > 0 ? ` · ${detail.latencyMs} ms` : ""}
                    </p>
                    <div className="flex justify-center gap-4 mt-6">
                      <button
                        type="button"
                        data-testid="escalate-call-btn"
                        disabled={escalateMutation.isPending || detail.status !== "active"}
                        onClick={() => escalateMutation.mutate(detail.id)}
                        className="flex items-center gap-2 bg-white/10 text-white border border-white/20 px-6 py-2.5 rounded-full text-sm font-bold hover:bg-white/20 transition-all disabled:opacity-40"
                      >
                        <ShieldAlert size={16} /> Escalate
                      </button>
                    </div>
                  </>
                ) : (
                  <p className="text-zinc-400 text-sm">No call selected. Start a simulated call to begin.</p>
                )}
              </div>
            </motion.div>

            <motion.div
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1 }}
              className="glass-card rounded-[2rem] overflow-hidden shadow-sm"
            >
              <div className="p-6 border-b border-border">
                <h3 className="text-xs font-bold uppercase tracking-widest text-muted-foreground">Transcript</h3>
              </div>
              <DataState
                loading={detailQuery.isPending && selectedId !== null}
                error={detailQuery.error}
                empty={detail !== null && detail.turns.length === 0}
                emptyTitle="No transcript yet"
                emptyMessage="Send a caller turn below to simulate the conversation."
                onRetry={() => void detailQuery.refetch()}
              >
                <div className="p-6 space-y-4 max-h-80 overflow-y-auto" style={{ scrollbarWidth: "none" }}>
                  {(detail?.turns ?? []).map((turn) => {
                    const isCaller = turn.role === "caller";
                    return (
                      <div key={turn.id} className={`flex gap-3 ${isCaller ? "flex-row" : "flex-row-reverse"}`}>
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-black shrink-0 ${isCaller ? "bg-muted text-foreground" : "bg-primary text-white"}`}>
                          {isCaller ? "PT" : "AI"}
                        </div>
                        <div className={`max-w-[75%] ${isCaller ? "" : "text-right"}`}>
                          <div className={`flex items-center gap-2 mb-1 ${isCaller ? "" : "flex-row-reverse"}`}>
                            <span className="text-[10px] font-bold text-foreground capitalize">{turn.role}</span>
                            <span className="text-[10px] text-muted-foreground">{turn.kind}</span>
                          </div>
                          <div className={`px-4 py-3 rounded-2xl text-sm leading-relaxed ${isCaller ? "bg-muted text-foreground rounded-tl-none" : "bg-primary text-white rounded-tr-none"}`}>
                            {turn.text}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </DataState>

              <form onSubmit={submitTurn} className="p-6 border-t border-border flex gap-3">
                <input
                  data-testid="turn-input"
                  value={turnText}
                  onChange={(event) => setTurnText(event.target.value)}
                  placeholder="Send a caller turn…"
                  className={INPUT_CLASS}
                  disabled={!detail}
                />
                <button
                  type="submit"
                  data-testid="turn-submit"
                  disabled={!detail || turnMutation.isPending}
                  className="shrink-0 inline-flex items-center gap-2 bg-gradient-to-br from-primary to-primary-dim text-white px-5 py-3 rounded-xl font-bold text-sm hover:opacity-90 transition-all disabled:opacity-50"
                >
                  <Send size={16} /> Send
                </button>
              </form>
            </motion.div>
          </div>

          <motion.div
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.15 }}
            className="xl:col-span-5 space-y-5"
          >
            <div className="glass-card rounded-[2rem] p-6 shadow-sm">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-xs font-bold uppercase tracking-widest text-muted-foreground">Calls</h3>
                <span className="text-[10px] font-bold text-muted-foreground">{callsQuery.data?.count ?? 0} total</span>
              </div>
              <DataState
                loading={callsQuery.isPending}
                error={callsQuery.error}
                empty={calls.length === 0}
                emptyTitle="No calls"
                emptyMessage="No voice calls have been recorded in this clinic yet."
                onRetry={() => void callsQuery.refetch()}
              >
                <div className="space-y-2 max-h-72 overflow-y-auto">
                  {calls.map((call) => (
                    <button
                      key={call.id}
                      type="button"
                      data-testid={`call-row-${call.id}`}
                      onClick={() => setSelectedId(call.id)}
                      className={`w-full flex items-center gap-3 p-3 rounded-xl text-left transition-colors ${
                        call.id === selectedId ? "bg-primary/10 border border-primary/30" : "bg-muted/40 hover:bg-muted"
                      }`}
                    >
                      <div className="w-9 h-9 rounded-full bg-primary/10 text-primary flex items-center justify-center shrink-0">
                        <Phone size={15} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-bold text-foreground truncate">
                          {call.patientId ? `Patient ${call.patientId.slice(0, 8)}…` : "Unknown caller"}
                        </p>
                        <p className="text-[10px] text-muted-foreground truncate">
                          {modeLabel(call)} · {call.outcome || call.status}
                        </p>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <span className="text-[10px] text-muted-foreground flex items-center gap-1">
                          <Clock size={10} /> {formatDateTime(call.startedAt)}
                        </span>
                        <ChevronRight size={14} className="text-muted-foreground" />
                      </div>
                    </button>
                  ))}
                </div>
              </DataState>
            </div>

            <div className="glass-card rounded-[2rem] p-6 shadow-sm">
              <h3 className="text-xs font-bold uppercase tracking-widest text-muted-foreground mb-4 flex items-center gap-2">
                <Wrench size={14} /> Tool trace
              </h3>
              {detail && detail.toolCalls.length > 0 ? (
                <div className="space-y-2 max-h-56 overflow-y-auto">
                  {detail.toolCalls.map((toolCall) => (
                    <div key={toolCall.id} className="border border-border rounded-xl p-3 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-foreground">{toolCall.tool}</span>
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase ${toolCall.status === "ok" ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>
                          {toolCall.status}
                        </span>
                      </div>
                      <p className="text-muted-foreground mt-1 truncate">args: {toolCall.argsJson || "{}"}</p>
                      {toolCall.resultJson ? <p className="text-muted-foreground truncate">result: {toolCall.resultJson}</p> : null}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No tool calls recorded for this call.</p>
              )}

              <div className="mt-5 pt-5 border-t border-border space-y-3">
                <select
                  data-testid="tool-select"
                  value={tool}
                  onChange={(event) => setTool(event.target.value)}
                  className={INPUT_CLASS}
                  disabled={!detail}
                >
                  {TOOLS.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
                <input
                  data-testid="tool-args"
                  value={toolArgs}
                  onChange={(event) => setToolArgs(event.target.value)}
                  placeholder='{"query": "opening hours"}'
                  className={`${INPUT_CLASS} font-mono text-xs`}
                  disabled={!detail}
                />
                <button
                  type="button"
                  data-testid="tool-invoke"
                  disabled={!detail || toolMutation.isPending}
                  onClick={submitTool}
                  className="w-full inline-flex items-center justify-center gap-2 bg-gradient-to-br from-primary to-primary-dim text-white py-3 rounded-xl font-bold text-sm hover:opacity-90 transition-all disabled:opacity-50"
                >
                  <Wrench size={16} /> Invoke tool
                </button>
              </div>
            </div>

            {detail ? (
              <div className="bg-gradient-to-br from-zinc-900 to-primary rounded-[2rem] p-6 text-white shadow-xl relative overflow-hidden">
                <div className="relative z-10">
                  <p className="text-[10px] font-bold uppercase tracking-widest opacity-70 mb-2">Summary / outcome</p>
                  <h4 className="text-lg font-black font-display mb-2 capitalize">{detail.outcome || "In progress"}</h4>
                  <p className="text-sm text-zinc-300">
                    {detail.summary || "No summary recorded yet."}
                  </p>
                  <div className="mt-4 flex items-center gap-2 text-xs text-zinc-300">
                    <MessageSquare size={12} /> {detail.turns.length} turns · <PhoneCall size={12} /> {detail.toolCalls.length} tool calls
                  </div>
                </div>
                <div className="absolute -right-8 -bottom-8 w-32 h-32 bg-white/10 rounded-full blur-3xl pointer-events-none" />
              </div>
            ) : null}
          </motion.div>
        </div>
      </div>
    </AppLayout>
  );
}
