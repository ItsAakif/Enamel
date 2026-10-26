import type { ReactNode } from "react";
import { AlertTriangle, Inbox, RefreshCw } from "lucide-react";
import { apiErrorMessage } from "@/lib/api";

interface DataStateProps {
  /** The request is in flight (or refetching with no data yet). */
  loading?: boolean;
  /** The request failed; never render fabricated data. */
  error?: unknown;
  /** The request succeeded but returned nothing to show. */
  empty?: boolean;
  emptyTitle?: string;
  emptyMessage?: string;
  onRetry?: () => void;
  className?: string;
  children: ReactNode;
}

const PANEL = "glass-card rounded-[2rem] p-10 shadow-sm";

function Skeleton() {
  return (
    <div className={PANEL} role="status" aria-live="polite" aria-busy="true">
      <div className="space-y-4 animate-pulse">
        <div className="h-6 w-40 rounded-lg bg-muted" />
        <div className="h-4 w-full rounded-lg bg-muted/70" />
        <div className="h-4 w-5/6 rounded-lg bg-muted/70" />
        <div className="h-4 w-2/3 rounded-lg bg-muted/70" />
      </div>
      <span className="sr-only">Loading</span>
    </div>
  );
}

/**
 * Shared loading / error+retry / empty wrapper (ADR-012).
 *
 * Pages must never invent numbers on failure, so the error branch is the only
 * thing rendered when a request rejects.
 */
export function DataState({
  loading = false,
  error = null,
  empty = false,
  emptyTitle = "Nothing here yet",
  emptyMessage = "There is no data to show for this view.",
  onRetry,
  className,
  children,
}: DataStateProps) {
  if (loading) return <Skeleton />;

  if (error !== null && error !== undefined) {
    return (
      <div className={`${PANEL} ${className ?? ""}`} role="alert">
        <div className="flex items-start gap-3">
          <div className="p-2.5 rounded-xl bg-destructive/10 text-destructive shrink-0">
            <AlertTriangle size={20} />
          </div>
          <div className="flex-1">
            <h3 className="font-bold font-display text-foreground">Could not load data</h3>
            <p className="text-sm text-muted-foreground mt-1">{apiErrorMessage(error)}</p>
            {onRetry ? (
              <button
                type="button"
                onClick={onRetry}
                className="mt-4 inline-flex items-center gap-2 bg-gradient-to-br from-primary to-primary-dim text-white px-5 py-2.5 rounded-full font-bold text-xs shadow-lg shadow-primary/20 hover:opacity-90 active:scale-95 transition-all"
              >
                <RefreshCw size={14} />
                Retry
              </button>
            ) : null}
          </div>
        </div>
      </div>
    );
  }

  if (empty) {
    return (
      <div className={`${PANEL} ${className ?? ""}`}>
        <div className="flex items-start gap-3">
          <div className="p-2.5 rounded-xl bg-muted text-muted-foreground shrink-0">
            <Inbox size={20} />
          </div>
          <div>
            <h3 className="font-bold font-display text-foreground">{emptyTitle}</h3>
            <p className="text-sm text-muted-foreground mt-1">{emptyMessage}</p>
          </div>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
