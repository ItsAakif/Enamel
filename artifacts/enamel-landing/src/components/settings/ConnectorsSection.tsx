import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Plug, RefreshCw } from "lucide-react";
import {
  connectorsCreate,
  connectorsList,
  connectorsRunsList,
  connectorsSyncCreate,
  getConnectorsListQueryKey,
  getConnectorsRunsListQueryKey,
  type ConnectorConfig,
  type ConnectorKindEnum,
  type ConnectorModeEnum,
  type SyncRun,
} from "@workspace/api-client-react";
import { apiErrorFieldErrors, apiErrorMessage, readNumber, readString, toPage } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { DataState } from "@/components/common/DataState";
import {
  ErrorBanner,
  Field,
  Pill,
  PrimaryButton,
  SectionCard,
  SelectInput,
  TextInput,
} from "@/components/settings/settings-ui";

const KINDS: ConnectorKindEnum[] = ["open_dental"];
const MODES: ConnectorModeEnum[] = ["simulator", "live"];

function ModeBadge({ mode }: { mode: string }) {
  if (mode === "live") {
    return <Pill tone="danger">live</Pill>;
  }
  return <Pill tone="primary">simulator</Pill>;
}

function ConnectorRuns({ connectorId }: { connectorId: string }) {
  const runsQuery = useQuery({
    queryKey: getConnectorsRunsListQueryKey(connectorId),
    queryFn: async () => toPage<SyncRun>(await connectorsRunsList(connectorId)).results,
    retry: false,
  });

  if (runsQuery.isPending) return <p className="text-xs text-muted-foreground mt-2">Loading runs…</p>;
  if (runsQuery.error) return <p className="text-xs text-destructive mt-2">{apiErrorMessage(runsQuery.error)}</p>;
  const runs = runsQuery.data ?? [];
  if (runs.length === 0) return <p className="text-xs text-muted-foreground mt-2">No sync runs yet.</p>;

  return (
    <ul className="mt-3 space-y-2">
      {runs.map((run) => (
        <li key={run.id} className="text-xs bg-background/60 rounded-lg px-3 py-2 flex flex-wrap items-center gap-x-3 gap-y-1">
          <Pill tone={run.status === "succeeded" ? "success" : run.status === "failed" ? "danger" : "neutral"}>
            {run.status}
          </Pill>
          <span className="font-mono text-muted-foreground">{run.direction}</span>
          <span className="text-muted-foreground">{formatDateTime(run.started_at)}</span>
          {run.error_code ? <span className="text-destructive">{run.error_code}</span> : null}
          <span className="text-muted-foreground">
            {readNumber(run.counts_json, "created") ?? 0} created · {readNumber(run.counts_json, "updated") ?? 0} updated
          </span>
        </li>
      ))}
    </ul>
  );
}

export function ConnectorsSection() {
  const queryClient = useQueryClient();

  const connectorsQuery = useQuery({
    queryKey: getConnectorsListQueryKey(),
    queryFn: async () => toPage<ConnectorConfig>(await connectorsList()).results,
    retry: false,
  });

  const [form, setForm] = useState<{ name: string; kind: ConnectorKindEnum; mode: ConnectorModeEnum; base_url: string }>({
    name: "",
    kind: "open_dental",
    mode: "simulator",
    base_url: "",
  });
  const [formError, setFormError] = useState<string | null>(null);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: getConnectorsListQueryKey() });
  }

  const createMutation = useMutation({
    mutationFn: () => connectorsCreate(form),
    onSuccess: () => {
      setForm({ name: "", kind: "open_dental", mode: "simulator", base_url: "" });
      setFormError(null);
      invalidate();
    },
    onError: (error) => setFormError(apiErrorMessage(error)),
  });

  const syncMutation = useMutation({
    mutationFn: (connectorId: string) => connectorsSyncCreate(connectorId),
    onSuccess: (result, connectorId) => {
      setSyncMessage(`Sync started in ${result.mode} mode · run ${result.run.status}.`);
      void queryClient.invalidateQueries({ queryKey: getConnectorsRunsListQueryKey(connectorId) });
      invalidate();
    },
    onError: (error) => setSyncMessage(apiErrorMessage(error)),
  });

  const fieldErrors = apiErrorFieldErrors(createMutation.error);
  const connectors = connectorsQuery.data ?? [];

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    createMutation.mutate();
  }

  return (
    <SectionCard
      icon={Plug}
      title="Connectors & Integrations"
      description="Practice-management bridges. The mode column shows whether a connector is a simulator or a live integration."
    >
      <DataState
        loading={connectorsQuery.isPending}
        error={connectorsQuery.error}
        empty={connectors.length === 0}
        emptyTitle="No connectors"
        emptyMessage="Register the first connector below."
        onRetry={() => void connectorsQuery.refetch()}
      >
        <ul className="space-y-3" data-testid="connectors-list">
          {connectors.map((connector) => (
            <li key={connector.id} className="p-4 bg-muted/50 rounded-xl">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-bold text-foreground">{connector.name}</span>
                    <ModeBadge mode={connector.mode} />
                    <Pill tone={connector.is_active ? "success" : "neutral"}>
                      {connector.is_active ? "active" : "inactive"}
                    </Pill>
                    {connector.has_credentials ? <Pill tone="neutral">credentials</Pill> : null}
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    <code className="font-mono">{connector.kind}</code> · {connector.base_url || "no base url"} · last sync{" "}
                    {connector.last_sync_at ? formatDateTime(connector.last_sync_at) : "never"}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="inline-flex items-center gap-1.5 border border-border px-4 py-2 rounded-full font-bold text-xs hover:bg-muted transition-colors"
                    aria-expanded={expandedId === connector.id}
                    onClick={() => setExpandedId(expandedId === connector.id ? null : connector.id)}
                  >
                    <ChevronDown size={12} /> Runs
                  </button>
                  <PrimaryButton
                    className="px-4 py-2"
                    busy={syncMutation.isPending && syncMutation.variables === connector.id}
                    onClick={() => syncMutation.mutate(connector.id)}
                    aria-label={`Sync ${connector.name}`}
                  >
                    <RefreshCw size={12} /> Sync
                  </PrimaryButton>
                </div>
              </div>
              {expandedId === connector.id ? <ConnectorRuns connectorId={connector.id} /> : null}
            </li>
          ))}
        </ul>
      </DataState>

      {syncMessage ? (
        <div role="status" className="mt-4 rounded-xl border border-border bg-background/60 px-4 py-3 text-xs text-foreground">
          {syncMessage}
        </div>
      ) : null}

      <form onSubmit={onSubmit} className="mt-6 border-t border-border/50 pt-6 space-y-4" noValidate>
        <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground">Register connector</p>
        {formError ? <ErrorBanner>{formError}</ErrorBanner> : null}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Field label="Name" htmlFor="connector-name" error={fieldErrors.name}>
            <TextInput
              id="connector-name"
              data-testid="connector-name"
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </Field>
          <Field label="Kind" htmlFor="connector-kind" error={fieldErrors.kind}>
            <SelectInput
              id="connector-kind"
              value={form.kind}
              onChange={(e) => setForm({ ...form, kind: e.target.value as ConnectorKindEnum })}
            >
              {KINDS.map((kind) => (
                <option key={kind} value={kind}>
                  {kind}
                </option>
              ))}
            </SelectInput>
          </Field>
          <Field label="Mode" htmlFor="connector-mode" error={fieldErrors.mode} hint="Live connectors need credentials and CONNECTOR_ALLOW_LIVE.">
            <SelectInput
              id="connector-mode"
              value={form.mode}
              onChange={(e) => setForm({ ...form, mode: e.target.value as ConnectorModeEnum })}
            >
              {MODES.map((mode) => (
                <option key={mode} value={mode}>
                  {mode}
                </option>
              ))}
            </SelectInput>
          </Field>
          <Field label="Base URL" htmlFor="connector-base-url" error={fieldErrors.base_url}>
            <TextInput
              id="connector-base-url"
              value={form.base_url}
              onChange={(e) => setForm({ ...form, base_url: e.target.value })}
            />
          </Field>
        </div>
        <PrimaryButton type="submit" data-testid="connector-save" busy={createMutation.isPending}>
          Register connector
        </PrimaryButton>
      </form>
    </SectionCard>
  );
}
