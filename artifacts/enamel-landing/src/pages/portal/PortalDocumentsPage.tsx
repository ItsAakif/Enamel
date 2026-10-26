import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, FileText } from "lucide-react";
import { customFetch, portalDocumentsList, type PortalDocument } from "@workspace/api-client-react";
import PortalLayout from "@/layouts/PortalLayout";
import { PORTAL_REQUEST } from "@/hooks/use-portal-auth";
import { apiErrorMessage, toPage } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import { formatDate } from "@/lib/format";
import { DataState } from "@/components/common/DataState";
import { Pill } from "@/components/settings/settings-ui";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function PortalDocumentsPage() {
  const [downloading, setDownloading] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const documentsQuery = useQuery({
    queryKey: ["portal", "documents"],
    queryFn: () => portalDocumentsList({ page_size: 50 }, PORTAL_REQUEST),
    retry: false,
  });

  const pageData = toPage<PortalDocument>(documentsQuery.data);
  const documents = pageData.results;

  async function download(document: PortalDocument) {
    setDownloadError(null);
    setDownloading(document.id);
    try {
      // download_url already carries a short-lived signed token issued per request.
      const blob = await customFetch<Blob>(document.download_url, { ...PORTAL_REQUEST, responseType: "blob" });
      downloadBlob(blob, document.filename);
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
          <h1 className="text-3xl font-extrabold text-foreground font-display leading-tight">Documents</h1>
          <p className="text-sm text-muted-foreground mt-2">
            Letters, forms and files your clinic has shared. Downloads use a short-lived signed link.
          </p>
        </header>

        {downloadError ? (
          <div role="alert" className="rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive font-medium">
            {downloadError}
          </div>
        ) : null}

        <DataState
          loading={documentsQuery.isPending}
          error={documentsQuery.error}
          empty={documents.length === 0}
          emptyTitle="No documents"
          emptyMessage="You have no shared documents yet."
          onRetry={() => void documentsQuery.refetch()}
        >
          <ul className="space-y-3" data-testid="portal-documents">
            {documents.map((document) => (
              <li
                key={document.id}
                className="bg-card border border-border rounded-2xl p-5 shadow-sm flex flex-wrap items-center justify-between gap-3"
              >
                <div className="min-w-0 flex items-center gap-3">
                  <div className="p-2.5 bg-primary/10 text-primary rounded-xl">
                    <FileText size={18} />
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-bold text-foreground truncate">{document.filename}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      <Pill tone="neutral" className="mr-2">
                        {document.kind}
                      </Pill>
                      {formatSize(document.size_bytes)} · {formatDate(document.created_at)}
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  data-testid={`download-${document.id}`}
                  disabled={downloading === document.id}
                  onClick={() => void download(document)}
                  className="inline-flex items-center gap-1.5 border border-border px-4 py-2.5 rounded-full font-bold text-xs hover:bg-muted transition-colors disabled:opacity-60 min-h-[44px]"
                >
                  <Download size={13} />
                  {downloading === document.id ? "Downloading…" : "Download"}
                </button>
              </li>
            ))}
          </ul>
        </DataState>
      </div>
    </PortalLayout>
  );
}
