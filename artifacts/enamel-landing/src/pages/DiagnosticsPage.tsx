import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  AlertTriangle,
  CloudUpload,
  FileImage,
  Link2,
  Loader2,
  Play,
  ScanLine,
} from "lucide-react";
import {
  authCsrfRetrieve,
  imagingInstancesPreviewRetrieve,
  imagingStudiesInferenceCreate,
  imagingStudiesInferenceList,
  imagingStudiesList,
  imagingStudiesRetrieve,
  type ImagingStudiesListParams,
  type InferenceJob,
  type Instance,
  type Study,
  type StudyDetail,
} from "@workspace/api-client-react";
import AppLayout from "@/layouts/AppLayout";
import { DataState } from "@/components/common/DataState";
import { useToast } from "@/hooks/use-toast";
import { apiErrorMessage, readRecordArray, readString, toPage } from "@/lib/api";
import { formatDate, formatDateTime } from "@/lib/format";
import { uploadImagingStudies, type UploadProgress } from "@/lib/upload";

const INPUT_CLASS =
  "w-full border border-border rounded-xl px-4 py-3 text-sm font-medium bg-white/70 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all";

const PAGE_SIZE = 25;
const DISCLAIMER = "Experimental assistance — not a diagnosis.";

/** The list view also accepts `modality`/`patient`; only pagination is documented. */
type StudyListParams = ImagingStudiesListParams & { modality?: string };

interface FindingRow {
  label: string;
  confidence: number;
  bbox: string;
}

interface InferenceView {
  disclosure: string;
  modelName: string;
  modelVersion: string;
  status: string;
  instanceCount: number;
  findings: FindingRow[];
}

function readFindings(job: InferenceJob | undefined): InferenceView | null {
  if (!job || job.findings_json === null || job.findings_json === undefined) return null;
  const payload = job.findings_json as Record<string, unknown>;
  const model = payload.model as Record<string, unknown> | undefined;
  const instances = readRecordArray(payload, "instances");
  const findings: FindingRow[] = [];
  for (const instance of instances) {
    for (const finding of readRecordArray(instance, "findings")) {
      const bbox = finding.bbox;
      findings.push({
        label: readString(finding, "label"),
        confidence: typeof finding.confidence === "number" ? finding.confidence : 0,
        bbox: Array.isArray(bbox) ? bbox.join(", ") : "",
      });
    }
  }
  return {
    disclosure: readString(payload, "disclosure"),
    modelName: readString(model ?? {}, "name"),
    modelVersion: readString(model ?? {}, "version"),
    status: readString(payload, "status"),
    instanceCount: instances.length,
    findings,
  };
}

export default function DiagnosticsPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [modality, setModality] = useState("");
  const [page, setPage] = useState(1);
  const [selectedStudyId, setSelectedStudyId] = useState<string | null>(null);
  const [selectedInstanceId, setSelectedInstanceId] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  const [showUpload, setShowUpload] = useState(false);
  const [uploadPatient, setUploadPatient] = useState("");
  const [uploadSource, setUploadSource] = useState("");
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setPage(1);
  }, [modality]);

  const listParams = useMemo<StudyListParams>(
    () => ({ modality: modality || undefined, page, page_size: PAGE_SIZE }),
    [modality, page],
  );

  const studiesQuery = useQuery({
    queryKey: ["diagnostics", "studies", listParams],
    queryFn: async () => toPage<Study>(await imagingStudiesList(listParams)),
  });

  const studies = studiesQuery.data?.results ?? [];
  const total = studiesQuery.data?.count ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  useEffect(() => {
    if (selectedStudyId === null && studies.length > 0) setSelectedStudyId(studies[0].id);
  }, [studies, selectedStudyId]);

  const detailQuery = useQuery({
    queryKey: ["diagnostics", "study", selectedStudyId],
    queryFn: () => imagingStudiesRetrieve(selectedStudyId as string),
    enabled: selectedStudyId !== null,
  });
  const detail: StudyDetail | undefined = detailQuery.data;

  const inferenceQuery = useQuery({
    queryKey: ["diagnostics", "inference", selectedStudyId],
    queryFn: async () => toPage<InferenceJob>(await imagingStudiesInferenceList(selectedStudyId as string, { page_size: 20 })),
    enabled: selectedStudyId !== null,
  });

  const previewQuery = useQuery({
    queryKey: ["diagnostics", "preview", selectedInstanceId],
    queryFn: () => imagingInstancesPreviewRetrieve(selectedInstanceId as string),
    enabled: selectedInstanceId !== null,
    retry: false,
  });

  useEffect(() => {
    const blob = previewQuery.data;
    if (!blob || !(blob instanceof Blob)) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(blob);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [previewQuery.data]);

  const inferenceMutation = useMutation({
    mutationFn: () => imagingStudiesInferenceCreate(selectedStudyId as string),
    onSuccess: (response) => {
      void queryClient.invalidateQueries({ queryKey: ["diagnostics", "inference"] });
      toast({ title: "Inference job queued", description: `${response.model_name} ${response.model_version}. ${DISCLAIMER}` });
    },
    onError: (error) => toast({ title: "Inference failed to start", description: apiErrorMessage(error), variant: "destructive" }),
  });

  const uploadMutation = useMutation({
    mutationFn: async () => {
      await authCsrfRetrieve();
      setUploadError(null);
      setUploadProgress({ loaded: 0, total: 0, percent: 0 });
      return uploadImagingStudies(uploadPatient.trim(), uploadFiles, uploadSource, setUploadProgress);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["diagnostics", "studies"] });
      setShowUpload(false);
      setUploadFiles([]);
      setUploadPatient("");
      setUploadSource("");
      setUploadProgress(null);
      if (fileInput.current) fileInput.current.value = "";
      toast({ title: "Study uploaded", description: "The DICOM study was ingested." });
    },
    onError: (error) => {
      setUploadProgress(null);
      setUploadError(apiErrorMessage(error));
    },
  });

  const instances: Instance[] = detail ? detail.series.flatMap((series) => [...series.instances]) : [];
  const latestJob = inferenceQuery.data?.results?.[0];
  const findings = readFindings(latestJob);

  return (
    <AppLayout>
      <div className="max-w-screen-2xl mx-auto h-full flex flex-col">
        <header className="mb-8 flex flex-wrap gap-4 justify-between items-end">
          <div>
            <span className="text-xs font-bold uppercase tracking-[0.2em] text-primary mb-2 block font-sans">Radiology</span>
            <h2 className="text-4xl font-extrabold text-foreground font-display leading-none">AI Diagnostics</h2>
          </div>
          <button
            type="button"
            data-testid="upload-study-btn"
            onClick={() => setShowUpload(true)}
            className="flex items-center gap-2 bg-gradient-to-br from-primary to-primary-dim text-white px-6 py-2.5 rounded-full font-bold text-sm shadow-lg shadow-primary/20 hover:opacity-90 transition-all"
          >
            <CloudUpload size={16} /> Upload study
          </button>
        </header>

        <div className="grid grid-cols-1 xl:grid-cols-12 gap-8 flex-1">
          <div className="xl:col-span-4 flex flex-col space-y-4">
            <div className="glass-card rounded-2xl p-6 shadow-sm flex-1">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-bold uppercase tracking-widest text-muted-foreground">Imaging studies</h3>
                <select
                  data-testid="study-modality-filter"
                  value={modality}
                  onChange={(event) => setModality(event.target.value)}
                  className="border border-border rounded-lg px-3 py-1.5 text-xs font-bold bg-white/70"
                  aria-label="Filter by modality"
                >
                  <option value="">All modalities</option>
                  {["DX", "OPG", "IO", "CBCT", "PX"].map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </div>
              <DataState
                loading={studiesQuery.isPending}
                error={studiesQuery.error}
                empty={studies.length === 0}
                emptyTitle="No studies"
                emptyMessage="Upload a DICOM study to get started."
                onRetry={() => void studiesQuery.refetch()}
              >
                <div className="space-y-3">
                  {studies.map((study) => (
                    <button
                      key={study.id}
                      type="button"
                      data-testid={`study-row-${study.id}`}
                      onClick={() => {
                        setSelectedStudyId(study.id);
                        setSelectedInstanceId(null);
                      }}
                      className={`w-full text-left p-4 rounded-xl border transition-colors ${
                        study.id === selectedStudyId ? "bg-primary/5 border-primary shadow-sm" : "bg-background border-border hover:bg-muted"
                      }`}
                    >
                      <div className="flex justify-between items-start mb-2">
                        <p className="font-bold text-sm text-foreground font-mono">{study.patient.slice(0, 8)}…</p>
                        <span className="text-[10px] text-muted-foreground">{formatDate(study.study_date ?? study.created_at)}</span>
                      </div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="px-2 py-1 bg-muted rounded-md text-[10px] font-bold">{study.modality || "—"}</span>
                        <span className="px-2 py-1 bg-muted rounded-md text-[10px] font-bold">{study.series_count} series</span>
                        <span className="px-2 py-1 bg-muted rounded-md text-[10px] font-bold">{study.instance_count} images</span>
                      </div>
                    </button>
                  ))}
                </div>
                <div className="flex items-center justify-between mt-4">
                  <span className="text-xs text-muted-foreground">
                    Page {page} of {totalPages}
                  </span>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={page <= 1}
                      onClick={() => setPage((p) => Math.max(1, p - 1))}
                      className="px-3 py-1.5 rounded-full border border-border text-[10px] font-bold disabled:opacity-40 hover:bg-muted"
                    >
                      Prev
                    </button>
                    <button
                      type="button"
                      disabled={page >= totalPages}
                      onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                      className="px-3 py-1.5 rounded-full border border-border text-[10px] font-bold disabled:opacity-40 hover:bg-muted"
                    >
                      Next
                    </button>
                  </div>
                </div>
              </DataState>
            </div>
          </div>

          <div className="xl:col-span-8 flex flex-col">
            <motion.div
              initial={{ opacity: 0, scale: 0.98 }}
              animate={{ opacity: 1, scale: 1 }}
              className="bg-zinc-950 rounded-[2rem] p-6 shadow-2xl relative overflow-hidden flex-1 flex flex-col min-h-[600px]"
            >
              <DataState
                loading={detailQuery.isPending && selectedStudyId !== null}
                error={detailQuery.error}
                empty={selectedStudyId === null}
                emptyTitle="No study selected"
                emptyMessage="Select a study on the left to view its series and instances."
                onRetry={() => void detailQuery.refetch()}
              >
                {detail ? (
                  <>
                    <div className="flex justify-between items-center mb-6 px-2">
                      <div className="text-white">
                        <h3 className="font-display font-bold text-xl">
                          {detail.description || `Study ${detail.id.slice(0, 8)}`}
                        </h3>
                        <p className="text-xs text-zinc-400 font-medium mt-1">
                          UID: {detail.study_instance_uid} · {detail.modality || "—"} · {detail.instance_count} images
                        </p>
                      </div>
                    </div>

                    <div className="flex-1 bg-black rounded-xl border border-white/10 relative overflow-hidden flex items-center justify-center min-h-[280px]">
                      {previewUrl ? (
                        <img src={previewUrl} alt="DICOM preview" className="w-full h-full object-contain" />
                      ) : (
                        <div className="text-center text-zinc-500 px-6">
                          <FileImage size={36} className="mx-auto mb-3 opacity-60" />
                          <p className="text-sm">
                            {selectedInstanceId
                              ? previewQuery.isError
                                ? "Preview unavailable for this instance."
                                : "Loading preview…"
                              : "Select an instance below to preview its DICOM image."}
                          </p>
                        </div>
                      )}
                    </div>

                    <div className="mt-4 flex flex-wrap gap-2">
                      {instances.map((instance) => (
                        <button
                          key={instance.id}
                          type="button"
                          data-testid={`instance-${instance.id}`}
                          onClick={() => setSelectedInstanceId(instance.id)}
                          className={`px-3 py-1.5 rounded-lg text-[10px] font-bold transition-colors ${
                            instance.id === selectedInstanceId ? "bg-primary text-white" : "bg-white/10 text-zinc-300 hover:bg-white/20"
                          }`}
                          title={instance.sop_instance_uid}
                        >
                          #{instance.instance_number ?? "?"} {instance.has_preview ? "" : "(no preview)"}
                        </button>
                      ))}
                    </div>

                    <div className="mt-4 flex flex-wrap items-center gap-3 text-xs text-zinc-400">
                      <span className="inline-flex items-center gap-1.5">
                        <Link2 size={12} /> DICOMweb:
                      </span>
                      <a
                        className="text-primary underline break-all"
                        href={`/api/v1/imaging/dicomweb/studies/${encodeURIComponent(detail.study_instance_uid)}/series/`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        /api/v1/imaging/dicomweb/studies/{detail.study_instance_uid}/series/
                      </a>
                    </div>

                    <div className="mt-6 bg-white/5 border border-white/10 rounded-xl p-6 backdrop-blur-md">
                      <div className="flex justify-between items-center mb-4">
                        <h4 className="text-white font-bold text-sm uppercase tracking-widest flex items-center gap-2">
                          <ScanLine size={14} /> Assistive analysis
                        </h4>
                        <button
                          type="button"
                          data-testid="run-inference"
                          disabled={inferenceMutation.isPending}
                          onClick={() => inferenceMutation.mutate()}
                          className="inline-flex items-center gap-2 bg-primary text-white px-4 py-2 rounded-lg text-xs font-bold hover:bg-primary-dim transition-colors disabled:opacity-60"
                        >
                          {inferenceMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                          Run inference
                        </button>
                      </div>

                      <div className="mb-4 flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3">
                        <AlertTriangle size={16} className="text-amber-400 mt-0.5 shrink-0" />
                        <p data-testid="advisory-label" className="text-xs text-amber-200">
                          <span className="font-bold">{DISCLAIMER}</span> Findings are advisory and are never written to the chart automatically.
                        </p>
                      </div>

                      <DataState
                        loading={inferenceQuery.isPending && selectedStudyId !== null}
                        error={inferenceQuery.error}
                        empty={(inferenceQuery.data?.results ?? []).length === 0}
                        emptyTitle="No analysis yet"
                        emptyMessage="Run an inference job to see assistive findings."
                        onRetry={() => void inferenceQuery.refetch()}
                      >
                        {latestJob ? (
                          <div className="space-y-4">
                            <div className="flex flex-wrap items-center gap-3 text-xs text-zinc-300">
                              <span className={`px-2.5 py-1 rounded-full font-bold uppercase ${latestJob.status === "completed" ? "bg-emerald-500/20 text-emerald-300" : latestJob.status === "failed" ? "bg-destructive/20 text-red-300" : "bg-white/10"}`}>
                                {latestJob.status}
                              </span>
                              <span>Model: {latestJob.model_name} {latestJob.model_version}</span>
                              <span>Started: {formatDateTime(latestJob.started_at ?? latestJob.created_at)}</span>
                            </div>
                            {latestJob.status === "failed" && latestJob.error_code ? (
                              <p className="text-xs text-red-300">Failed: {latestJob.error_code}</p>
                            ) : null}
                            {findings ? (
                              <>
                                {findings.findings.length === 0 ? (
                                  <p className="text-xs text-zinc-400">No advisory findings were produced for this study.</p>
                                ) : (
                                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                    {findings.findings.map((finding, index) => (
                                      <div key={index} className="bg-black/50 p-4 rounded-lg border border-amber-500/30 flex items-start gap-3">
                                        <AlertTriangle className="text-amber-500 mt-0.5" size={16} />
                                        <div>
                                          <p className="text-sm font-bold text-white mb-1 capitalize">{finding.label}</p>
                                          <p className="text-xs text-zinc-400">
                                            confidence {(finding.confidence * 100).toFixed(1)}% · bbox {finding.bbox}
                                          </p>
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                )}
                                {findings.disclosure ? (
                                  <p className="text-[11px] text-zinc-500 leading-relaxed">{findings.disclosure}</p>
                                ) : null}
                              </>
                            ) : null}

                            {(inferenceQuery.data?.results?.length ?? 0) > 1 ? (
                              <div className="pt-3 border-t border-white/10">
                                <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500 mb-2">History</p>
                                <ul className="space-y-1">
                                  {(inferenceQuery.data?.results ?? []).map((job) => (
                                    <li key={job.id} className="text-[11px] text-zinc-400 flex items-center justify-between">
                                      <span>{job.model_name} {job.model_version}</span>
                                      <span>{formatDateTime(job.created_at)} · {job.status}</span>
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            ) : null}
                          </div>
                        ) : null}
                      </DataState>
                    </div>
                  </>
                ) : null}
              </DataState>
            </motion.div>
          </div>
        </div>
      </div>

      {showUpload ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="glass-card rounded-[2rem] p-8 shadow-2xl w-full max-w-lg" role="dialog" aria-modal="true" aria-label="Upload DICOM study">
            <h3 className="text-xl font-bold font-display mb-4 flex items-center gap-2">
              <CloudUpload size={18} className="text-primary" /> Upload DICOM study
            </h3>
            <div className="space-y-4">
              <input
                data-testid="upload-patient"
                value={uploadPatient}
                onChange={(event) => setUploadPatient(event.target.value)}
                placeholder="Patient id (UUID)"
                className={INPUT_CLASS}
              />
              <input
                value={uploadSource}
                onChange={(event) => setUploadSource(event.target.value)}
                placeholder="Source (optional)"
                className={INPUT_CLASS}
              />
              <input
                ref={fileInput}
                data-testid="upload-files"
                type="file"
                multiple
                accept=".dcm,application/dicom"
                onChange={(event) => setUploadFiles(Array.from(event.target.files ?? []))}
                className={INPUT_CLASS}
              />
              {uploadFiles.length > 0 ? (
                <p className="text-xs text-muted-foreground">{uploadFiles.length} file(s) selected.</p>
              ) : null}

              {/*
                De-identification removes the configured identifier tags from
                the DICOM metadata. It cannot remove anything that is burnt into
                the pixel data, and the default tag list does not scrub nested
                sequences. The source system has to de-identify the image
                before it is uploaded.
              */}
              <div
                data-testid="upload-deid-notice"
                className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-[11px] leading-relaxed text-amber-700 dark:text-amber-300"
              >
                <p className="font-bold uppercase tracking-widest mb-1">De-identification limits</p>
                <p>
                  The configured identifier tags are removed from the metadata
                  and the pixel data is stored unchanged. Annotations burnt into
                  the image are <strong>not</strong> removed: confirm that the
                  source system de-identifies the image before you upload it.
                  Nested sequences are not scrubbed by the default tag list
                  either.
                </p>
              </div>

              {uploadProgress ? (
                <div>
                  <div className="h-2 bg-muted rounded-full overflow-hidden">
                    <div
                      className="h-full bg-primary rounded-full transition-all"
                      style={{ width: `${uploadProgress.percent ?? 0}%` }}
                    />
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-1">
                    {uploadProgress.percent === null ? "Uploading…" : `${uploadProgress.percent}% uploaded`}
                  </p>
                </div>
              ) : null}

              {uploadError ? (
                <div role="alert" className="rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                  {uploadError}
                </div>
              ) : null}

              <div className="flex gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowUpload(false)}
                  className="flex-1 border border-border py-3 rounded-xl font-bold text-sm text-muted-foreground hover:bg-muted transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  data-testid="upload-submit"
                  disabled={uploadMutation.isPending || uploadPatient.trim() === "" || uploadFiles.length === 0}
                  onClick={() => uploadMutation.mutate()}
                  className="flex-1 bg-gradient-to-br from-primary to-primary-dim text-white py-3 rounded-xl font-bold text-sm hover:opacity-90 transition-all disabled:opacity-50"
                >
                  {uploadMutation.isPending ? "Uploading…" : "Upload"}
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </AppLayout>
  );
}
