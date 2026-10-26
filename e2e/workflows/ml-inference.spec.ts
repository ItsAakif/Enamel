import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { OWNER_STATE, activeClinicId } from "../fixtures";
import {
  apiGet,
  apiList,
  apiPost,
  createAppointment,
  createPatient,
  firstOpenSlot,
  patientSeed,
  rawPost,
  runContainerCommand,
} from "./helpers";

test.use({ storageState: OWNER_STATE });

interface NoShowModel {
  id: string;
  clinic: string | null;
  version: string;
  status: string;
  threshold: number;
  dataset_hash: string;
  metrics_hash: string;
  sklearn_version: string;
  synthetic_data: boolean;
  metrics_summary: {
    candidate_roc_auc: number;
    dataset_n: number;
    disclosure: string;
  };
}

interface PredictResponse {
  probability: number;
  threshold: number;
  decision: string;
  model_version: string;
  factors: Array<{ feature: string; value: unknown; importance: number }>;
  factors_method: string;
  synthetic_data: boolean;
  disclosure: string;
  prediction_id: string;
}

interface PredictionRow {
  id: string;
  appointment: string;
  model_version: string;
  probability: number;
  decision: string;
}

interface ApiErrorBody {
  error: { code: string; details?: { missing_features?: string[] } };
}

/**
 * Workflow 10: no-show ML inference.
 *
 * The journey reads the active registry row, scores a real appointment with
 * explicit values for the features the clinic does not persist, proves missing
 * features are reported instead of imputed, and reproduces the persisted metrics
 * hash from the shipped pipeline.
 */

test.describe("ML inference", () => {
  test.setTimeout(120_000);

  test("scores an appointment and reproduces the registry metrics hash", async ({ page }) => {
    await page.goto("/app/scheduling");
    const clinicId = await activeClinicId(page);
    const context = page.context();

    const models = await apiList<NoShowModel>(
      context,
      clinicId,
      "/api/v1/ml/no-show/models/?status=active&page_size=100",
    );
    const model = models.find((row) => row.status === "active");
    expect(model, "expected an active no-show model").toBeTruthy();
    expect(model?.synthetic_data).toBe(true);
    expect(model?.metrics_hash).toMatch(/^[0-9a-f]{64}$/);

    const patient = await createPatient(context, clinicId, patientSeed("Score"));
    // Availability already excludes occupied provider lanes, so this cannot
    // collide with a previous run's appointment.
    const slot = await firstOpenSlot(context, clinicId, { minDaysAhead: 6 });
    const appointment = await createAppointment(context, clinicId, patient.id, {
      startAt: slot.start,
      providerId: slot.provider ?? undefined,
      locationId: slot.location ?? undefined,
      reason: "E2E no-show scoring",
    });

    // `insurance_self_pay` and `distance_band` have no clinic source, and the
    // seeded type (`exam`) is not one of the trained categories, so the caller
    // must supply all three rather than the API guessing.
    const features = {
      insurance_self_pay: "insurance",
      distance_band: "2-5km",
      appointment_type: "checkup",
    };

    const prediction = await apiPost<PredictResponse>(
      context,
      clinicId,
      "/api/v1/ml/no-show/predict/",
      { appointment_id: appointment.id, features },
    );

    expect(prediction.probability).toBeGreaterThanOrEqual(0);
    expect(prediction.probability).toBeLessThanOrEqual(1);
    expect(prediction.threshold).toBe(model!.threshold);
    expect(["high_risk", "low_risk"]).toContain(prediction.decision);
    expect(prediction.model_version).toBe(model!.version);
    expect(prediction.factors_method).toBe("global_permutation_importance");
    expect(prediction.factors.length).toBeGreaterThan(0);
    expect(prediction.factors.length).toBeLessThanOrEqual(5);
    expect(prediction.synthetic_data).toBe(true);
    expect(prediction.disclosure).toContain("synthetic data");

    const predictions = await apiList<PredictionRow>(
      context,
      clinicId,
      `/api/v1/ml/no-show/predictions/?appointment=${appointment.id}&page_size=100`,
    );
    const stored = predictions.find((row) => row.id === prediction.prediction_id);
    expect(stored, "the prediction must be persisted").toBeTruthy();
    expect(stored?.model_version).toBe(model!.version);
    expect(stored?.probability).toBe(prediction.probability);

    // An incomplete vector is a 422 that names every gap; nothing is imputed.
    const incomplete = await rawPost(
      context,
      clinicId,
      "/api/v1/ml/no-show/predict/",
      { appointment_id: appointment.id, features: { insurance_self_pay: "insurance" } },
    );
    expect(incomplete.status()).toBe(422);
    const incompleteBody = (await incomplete.json()) as ApiErrorBody;
    expect(incompleteBody.error.code).toBe("validation_error");
    expect(incompleteBody.error.details?.missing_features).toContain("distance_band");

    // A foreign/unrelated id is indistinguishable from a missing appointment.
    const foreign = await rawPost(context, clinicId, "/api/v1/ml/no-show/predict/", {
      appointment_id: randomUUID(),
      features,
    });
    expect(foreign.status()).toBe(404);

    // The serving version must be reproducible from the shipped pipeline.
    const output = runContainerCommand([
      "python",
      "scripts/train_noshow.py",
      "--dataset",
      "synthetic",
      "--seed",
      "42",
      "--out",
      "artifacts/ml",
      "--check",
    ]);
    expect(output).toContain("[check] OK");
    const reproduced = /metrics_hash=([0-9a-f]{64})/.exec(output)?.[1];
    expect(reproduced, "the pipeline must print its metrics hash").toBeTruthy();
    expect(reproduced).toBe(model!.metrics_hash);

    // The registry view the UI consumes also carries the disclosure.
    const refreshed = await apiGet<{ results: NoShowModel[] }>(
      context,
      clinicId,
      "/api/v1/ml/no-show/models/?status=active&page_size=100",
    );
    expect(refreshed.results[0].metrics_summary.disclosure).toMatch(/synthetic/i);
  });
});
