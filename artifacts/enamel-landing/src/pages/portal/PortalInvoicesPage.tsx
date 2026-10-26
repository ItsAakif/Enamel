import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Receipt } from "lucide-react";
import { customFetch, portalInvoicesList, type PortalInvoice } from "@workspace/api-client-react";
import PortalLayout from "@/layouts/PortalLayout";
import { PORTAL_REQUEST } from "@/hooks/use-portal-auth";
import { apiErrorMessage, toPage } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import { formatDate, formatMinorUnits } from "@/lib/format";
import { DataState } from "@/components/common/DataState";
import { Pill } from "@/components/settings/settings-ui";

const STATUS_TONE: Record<string, "success" | "warning" | "danger" | "neutral" | "primary"> = {
  paid: "success",
  partially_paid: "warning",
  issued: "primary",
  overdue: "danger",
  void: "neutral",
};

export default function PortalInvoicesPage() {
  const [downloading, setDownloading] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const invoicesQuery = useQuery({
    queryKey: ["portal", "invoices"],
    queryFn: () => portalInvoicesList({ page_size: 50 }, PORTAL_REQUEST),
    retry: false,
  });

  const pageData = toPage<PortalInvoice>(invoicesQuery.data);
  const invoices = pageData.results;

  async function downloadReceipt(invoice: PortalInvoice) {
    setDownloadError(null);
    setDownloading(invoice.id);
    try {
      // The API hands out a short-lived signed URL; the generated client has no
      // function for it, so the bytes are fetched through customFetch.
      const blob = await customFetch<Blob>(invoice.receipt_url, { ...PORTAL_REQUEST, responseType: "blob" });
      downloadBlob(blob, `receipt-${invoice.number}.pdf`);
    } catch (error) {
      setDownloadError(apiErrorMessage(error));
    } finally {
      setDownloading(null);
    }
  }

  return (
    <PortalLayout>
      <div className="space-y-6">
        <header>
          <h1 className="text-3xl font-extrabold text-foreground font-display leading-tight">Invoices</h1>
          <p className="text-sm text-muted-foreground mt-2">Your balances and signed receipt downloads.</p>
        </header>

        {downloadError ? (
          <div role="alert" className="rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive font-medium">
            {downloadError}
          </div>
        ) : null}

        <DataState
          loading={invoicesQuery.isPending}
          error={invoicesQuery.error}
          empty={invoices.length === 0}
          emptyTitle="No invoices"
          emptyMessage="You have no invoices on file."
          onRetry={() => void invoicesQuery.refetch()}
        >
          <ul className="space-y-3" data-testid="portal-invoices">
            {invoices.map((invoice) => (
              <li key={invoice.id} className="bg-card border border-border rounded-2xl p-5 shadow-sm">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-base font-bold text-foreground flex items-center gap-2">
                      <Receipt size={16} className="text-primary" />
                      {invoice.number}
                    </p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      issued {formatDate(invoice.issued_at ?? invoice.created_at)}
                      {invoice.due_at ? ` · due ${formatDate(invoice.due_at)}` : ""}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-base font-extrabold font-display text-foreground">
                      {formatMinorUnits(invoice.total_minor, invoice.currency)}
                    </p>
                    <p className="text-[11px] text-muted-foreground">
                      balance {formatMinorUnits(invoice.balance_minor, invoice.currency)}
                    </p>
                  </div>
                </div>

                <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Pill tone={STATUS_TONE[invoice.status] ?? "neutral"}>{invoice.status}</Pill>
                    <span className="text-xs text-muted-foreground">
                      paid {formatMinorUnits(invoice.paid_minor, invoice.currency)} of{" "}
                      {formatMinorUnits(invoice.total_minor, invoice.currency)}
                    </span>
                  </div>
                  <button
                    type="button"
                    data-testid={`receipt-${invoice.id}`}
                    disabled={downloading === invoice.id}
                    onClick={() => void downloadReceipt(invoice)}
                    className="inline-flex items-center gap-1.5 border border-border px-4 py-2.5 rounded-full font-bold text-xs hover:bg-muted transition-colors disabled:opacity-60 min-h-[44px]"
                  >
                    <Download size={13} />
                    {downloading === invoice.id ? "Downloading…" : "Receipt"}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </DataState>
      </div>
    </PortalLayout>
  );
}
