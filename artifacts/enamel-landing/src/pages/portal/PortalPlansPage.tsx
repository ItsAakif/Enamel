import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ClipboardList, X } from "lucide-react";
import { portalPlansItemsCreate, portalPlansList, type PortalPlan, type PortalPlanItem } from "@workspace/api-client-react";
import PortalLayout from "@/layouts/PortalLayout";
import { PORTAL_REQUEST } from "@/hooks/use-portal-auth";
import { apiErrorCode, apiErrorMessage, toPage } from "@/lib/api";
import { formatDate, formatMinorUnits } from "@/lib/format";
import { DataState } from "@/components/common/DataState";
import { Pill } from "@/components/settings/settings-ui";

const ITEM_TONE: Record<string, "success" | "danger" | "neutral" | "primary"> = {
  proposed: "primary",
  accepted: "success",
  declined: "danger",
};

function planError(error: unknown): string {
  const code = apiErrorCode(error);
  if (code === "invalid_transition") return apiErrorMessage(error);
  if (code === "not_found") return "This plan item is no longer available.";
  return apiErrorMessage(error);
}

export default function PortalPlansPage() {
  const queryClient = useQueryClient();
  const [rowError, setRowError] = useState<{ id: string; message: string } | null>(null);

  const plansQuery = useQuery({
    queryKey: ["portal", "plans"],
    queryFn: () => portalPlansList({ page_size: 50 }, PORTAL_REQUEST),
    retry: false,
  });

  const pageData = toPage<PortalPlan>(plansQuery.data);
  const plans = pageData.results;

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ["portal", "plans"] });
  }

  const actionMutation = useMutation({
    mutationFn: ({ planId, itemId, action }: { planId: string; itemId: string; action: "accept" | "decline" }) =>
      portalPlansItemsCreate(planId, itemId, action, PORTAL_REQUEST),
    onSuccess: () => {
      setRowError(null);
      invalidate();
    },
    onError: (error, variables) => setRowError({ id: variables.itemId, message: planError(error) }),
  });

  return (
    <PortalLayout>
      <div className="space-y-6">
        <header>
          <h1 className="text-3xl font-extrabold text-foreground font-display leading-tight">Treatment Plans</h1>
          <p className="text-sm text-muted-foreground mt-2">
            Review the treatments your clinician proposed and accept or decline each item.
          </p>
        </header>

        <DataState
          loading={plansQuery.isPending}
          error={plansQuery.error}
          empty={plans.length === 0}
          emptyTitle="No treatment plans"
          emptyMessage="You have no treatment plans on file."
          onRetry={() => void plansQuery.refetch()}
        >
          <ul className="space-y-5" data-testid="portal-plans">
            {plans.map((plan) => (
              <li key={plan.id} className="bg-card border border-border rounded-[2rem] p-6 shadow-sm">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="text-lg font-bold font-display text-foreground flex items-center gap-2">
                      <ClipboardList size={18} className="text-primary" />
                      {plan.title}
                    </h2>
                    <p className="text-xs text-muted-foreground mt-1">
                      {plan.status} · created {formatDate(plan.created_at)}
                      {plan.expires_at ? ` · expires ${formatDate(plan.expires_at)}` : ""}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-lg font-extrabold font-display text-foreground">
                      {formatMinorUnits(plan.total_minor, plan.currency)}
                    </p>
                    <p className="text-[11px] text-muted-foreground">total</p>
                  </div>
                </div>

                <ul className="mt-5 divide-y divide-border/50">
                  {plan.items.map((item: PortalPlanItem) => {
                    const proposed = item.status === "proposed";
                    const busy = actionMutation.isPending && actionMutation.variables?.itemId === item.id;
                    return (
                      <li key={item.id} className="py-3">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          <div className="min-w-0">
                            <p className="text-sm font-semibold text-foreground">{item.description}</p>
                            <p className="text-xs text-muted-foreground mt-0.5">
                              <code className="font-mono">{item.procedure_code}</code>
                              {item.tooth !== null ? ` · tooth ${item.tooth}` : ""}
                              {item.surfaces.length > 0 ? ` · ${item.surfaces.join(",")}` : ""} · qty {item.quantity}
                            </p>
                          </div>
                          <div className="flex items-center gap-3">
                            <span className="text-sm font-bold text-foreground">
                              {formatMinorUnits(item.line_total_minor, plan.currency)}
                            </span>
                            <Pill tone={ITEM_TONE[item.status] ?? "neutral"}>{item.status}</Pill>
                          </div>
                        </div>

                        {proposed ? (
                          <div className="mt-2 flex items-center gap-2">
                            <button
                              type="button"
                              data-testid={`accept-${item.id}`}
                              disabled={busy}
                              onClick={() =>
                                actionMutation.mutate({ planId: plan.id, itemId: item.id, action: "accept" })
                              }
                              className="inline-flex items-center gap-1.5 bg-gradient-to-br from-primary to-primary-dim text-white px-4 py-2.5 rounded-full font-bold text-xs shadow-md shadow-primary/20 hover:opacity-90 transition-all disabled:opacity-60 min-h-[44px]"
                            >
                              <Check size={13} /> Accept
                            </button>
                            <button
                              type="button"
                              data-testid={`decline-${item.id}`}
                              disabled={busy}
                              onClick={() =>
                                actionMutation.mutate({ planId: plan.id, itemId: item.id, action: "decline" })
                              }
                              className="inline-flex items-center gap-1.5 border border-border px-4 py-2.5 rounded-full font-bold text-xs hover:bg-muted transition-colors disabled:opacity-60 min-h-[44px]"
                            >
                              <X size={13} /> Decline
                            </button>
                          </div>
                        ) : null}

                        {rowError && rowError.id === item.id ? (
                          <p role="alert" className="mt-2 text-xs text-destructive font-medium">
                            {rowError.message}
                          </p>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              </li>
            ))}
          </ul>
        </DataState>
      </div>
    </PortalLayout>
  );
}
