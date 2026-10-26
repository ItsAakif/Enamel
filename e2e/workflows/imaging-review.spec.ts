import { test, expect } from "@playwright/test";
import { File } from "node:buffer";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { OWNER_STATE, activeClinicId } from "../fixtures";
import {
  ORIGIN,
  apiGet,
  apiList,
  createPatient,
  ensureCsrf,
  patientSeed,
  staffHeaders,
} from "./helpers";

test.use({ storageState: OWNER_STATE });

interface Study {
  id: string;
  patient: string;
  modality: string;
  study_instance_uid: string;
  instance_count: number;
}

interface InferenceJob {
  id: string;
  study: string;
  status: string;
  model_name: string;
  model_version: string;
  output_hash: string;
  findings_json: {
    model: { name: string; version: string };
    status: string;
    disclosure: string;
    instances: Array<{
      instance_id: string;
      sop_instance_uid: string;
      findings: Array<{ label: string; confidence: number; bbox: number[] }>;
    }>;
  } | null;
}

interface ChartTooth {
  tooth: number;
  states: Array<{ condition_code: string; status: string; surfaces: string[] }>;
}

interface ChartResponse {
  dentition: string;
  teeth: ChartTooth[];
  counts: { total: number; teeth_with_states: number };
}

function chartSnapshot(chart: ChartResponse): string {
  return JSON.stringify(
    chart.teeth
      .map((tooth) => ({
        tooth: tooth.tooth,
        states: tooth.states.map((state) => ({
          code: state.condition_code,
          status: state.status,
          surfaces: state.surfaces,
        })),
      }))
      .filter((tooth) => tooth.states.length > 0)
      .sort((left, right) => left.tooth - right.tooth),
  );
}

/**
 * Workflow 11: imaging review.
 *
 * The seeded intra-oral study is opened in the Diagnostics viewer, an instance
 * preview is rendered, and an assistive inference job is run and polled to
 * completion. The reloaded page shows the disclosed model output, and the
 * patient's chart is byte-for-byte unchanged: findings are never auto-charted.
 */

test.describe("imaging review", () => {
  test("runs assistive inference on a study without touching the chart", async ({ page }) => {
    await page.goto("/app/patients");
    const clinicId = await activeClinicId(page);
    const context = page.context();

    const studies = await apiList<Study>(
      context,
      clinicId,
      "/api/v1/imaging/studies/?modality=IO&page_size=100",
    );
    const seeded = studies.find((row) => row.modality === "IO" && row.instance_count > 0);

    // Arrange the archive through the real upload endpoint. This is idempotent
    // for the canonical fixture SOP instances and also re-materialises bytes if
    // the media volume was restored separately from the database.
    const uploadPatient =
      seeded?.patient ?? (await createPatient(context, clinicId, patientSeed("Imaging"))).id;
    const fixtureDir = resolve(process.cwd(), "backend", "fixtures", "dicom");
    const fixtureNames = [
      "study1_series1_instance1.dcm",
      "study1_series1_instance2.dcm",
      "study1_series2_instance1.dcm",
    ];
    const form = new FormData();
    form.append("patient", uploadPatient);
    for (const name of fixtureNames) {
      form.append(
        "files",
        new File([readFileSync(resolve(fixtureDir, name))], name, {
          type: "application/dicom",
        }),
      );
    }
    await ensureCsrf(context);
    const upload = await context.request.post(`${ORIGIN}/api/v1/imaging/studies/`, {
      headers: await staffHeaders(context, clinicId),
      multipart: form,
    });
    if (!upload.ok()) {
      throw new Error(
        `imaging fixture upload failed: ${upload.status()} ${await upload.text()}`,
      );
    }
    const uploaded = (await upload.json()) as Study[];
    const study = uploaded.find((row) => row.modality === "IO" && row.instance_count > 0);
    expect(study, "expected the canonical intra-oral study").toBeTruthy();

    const chartBefore = await apiGet<ChartResponse>(
      context,
      clinicId,
      `/api/v1/patients/${study!.patient}/chart/`,
    );
    const snapshotBefore = chartSnapshot(chartBefore);

    const jobsBefore = await apiList<InferenceJob>(
      context,
      clinicId,
      `/api/v1/imaging/studies/${study!.id}/inference/?page_size=20`,
    );
    const knownJobs = new Set(jobsBefore.map((job) => job.id));

    await page.goto("/app/diagnostics");
    await expect(page.getByRole("heading", { name: "AI Diagnostics" })).toBeVisible();
    await page.getByTestId("study-modality-filter").selectOption("IO");

    const studyRow = page.getByTestId(`study-row-${study!.id}`);
    await expect(studyRow).toBeVisible();
    await studyRow.click();

    const instanceButtons = page.locator('[data-testid^="instance-"]');
    await expect(instanceButtons.first()).toBeVisible();
    const instanceCount = await instanceButtons.count();
    expect(instanceCount).toBeGreaterThan(0);
    await instanceButtons.first().click();
    await expect(page.locator('img[alt="DICOM preview"]')).toBeVisible();

    await expect(
      page.getByText("Findings are advisory and are never written to the chart automatically."),
    ).toBeVisible();

    // The advisory label the viewer shows next to every finding.
    const advisory = page.getByTestId("advisory-label");
    await expect(advisory).toBeVisible();
    await expect(advisory).toContainText("Experimental assistance — not a diagnosis.");
    await expect(advisory).toContainText(
      "Findings are advisory and are never written to the chart automatically.",
    );

    const inferenceResponse = page.waitForResponse(
      (response) =>
        response.url().includes(`/api/v1/imaging/studies/${study!.id}/inference/`) &&
        response.request().method() === "POST",
    );
    await page.getByTestId("run-inference").click();
    await expect(page.getByText("Inference job queued", { exact: true })).toBeVisible();

    // The API discloses the advisory nature of the signal when it is queued.
    const queued = await inferenceResponse;
    expect(queued.status()).toBe(202);
    const queuedBody = (await queued.json()) as { disclosure: string };
    expect(queuedBody.disclosure).toContain("Experimental assistive signal only");

    await expect
      .poll(async () => {
        const jobs = await apiList<InferenceJob>(
          context,
          clinicId,
          `/api/v1/imaging/studies/${study!.id}/inference/?page_size=20`,
        );
        const created = jobs.find((job) => !knownJobs.has(job.id));
        return created?.status ?? "queued";
      }, { message: "the inference job should complete", timeout: 90_000 })
      .toBe("completed");

    const jobs = await apiList<InferenceJob>(
      context,
      clinicId,
      `/api/v1/imaging/studies/${study!.id}/inference/?page_size=20`,
    );
    const job = jobs.find((row) => !knownJobs.has(row.id));
    expect(job, "expected the newly queued inference job").toBeTruthy();
    expect(job?.findings_json).toBeTruthy();
    const findings = job!.findings_json!;
    expect(findings.model.name).toBe(job!.model_name);
    expect(findings.model.version).toBe(job!.model_version);
    expect(findings.status).toBe("suggested");
    // The persisted findings carry the CV pipeline's own limitations.
    expect(findings.disclosure).toContain("Fixture-only evaluation");
    expect(findings.disclosure).toContain("not validated for diagnostic use");
    expect(findings.instances).toHaveLength(study!.instance_count);
    expect(findings.instances.every((entry) => Array.isArray(entry.findings))).toBe(true);
    expect(job?.output_hash).toMatch(/^[0-9a-f]{64}$/);

    // Reload: the trace must come from the persisted job, not React state.
    await page.reload();
    await expect(page.getByRole("heading", { name: "AI Diagnostics" })).toBeVisible();
    await page.getByTestId("study-modality-filter").selectOption("IO");
    await page.getByTestId(`study-row-${study!.id}`).click();
    await expect(page.getByText("completed", { exact: true }).first()).toBeVisible();
    await expect(
      page.getByText(`${job!.model_name} ${job!.model_version}`, { exact: false }).first(),
    ).toBeVisible();
    // The advisory label and the limitations are rendered from the stored job.
    await expect(page.getByTestId("advisory-label")).toBeVisible();
    await expect(page.getByText(/Fixture-only evaluation/).first()).toBeVisible();

    const chartAfter = await apiGet<ChartResponse>(
      context,
      clinicId,
      `/api/v1/patients/${study!.patient}/chart/`,
    );
    expect(chartSnapshot(chartAfter)).toBe(snapshotBefore);
    expect(chartAfter.counts.total).toBe(chartBefore.counts.total);
  });
});
