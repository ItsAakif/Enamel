import { test, expect } from "@playwright/test";
import { OWNER_STATE, activeClinicId } from "../fixtures";
import { createPatient, patientSeed, selectPatientInPicker, apiGet } from "./helpers";

test.use({ storageState: OWNER_STATE });

/**
 * Workflow 3: odontogram persistence.
 *
 * A condition is recorded on tooth 16 through the charting UI, then the page is
 * reloaded and the same state is read back from the API and rendered again.
 */

test.describe("odontogram persistence", () => {
  test("saves a tooth condition and survives a reload", async ({ page }) => {
    await page.goto("/app/charting");
    const clinicId = await activeClinicId(page);
    const patient = await createPatient(page.context(), clinicId, patientSeed("Chart"));

    await page.reload();
    await expect(page.getByRole("heading", { name: "Odontogram" })).toBeVisible();
    await selectPatientInPicker(page, "chart-patient", patient);

    await expect(page.getByTestId("tooth-16")).toBeVisible();
    await page.getByTestId("tooth-16").click();
    await expect(page.getByText("#16", { exact: false }).first()).toBeVisible();

    const conditionSelect = page.getByTestId("chart-condition");
    const codes = await conditionSelect.locator("option").evaluateAll((options) =>
      options.map((option) => (option as HTMLOptionElement).value).filter(Boolean),
    );
    const code = codes.includes("caries") ? "caries" : codes[0];
    expect(code, "expected at least one condition code in the legend").toBeTruthy();
    await conditionSelect.selectOption(code);

    await page.getByTestId("chart-save").click();
    await expect(page.getByText("Chart saved", { exact: true })).toBeVisible();

    // Read the server truth immediately after the save.
    const chart = await apiGet<{
      teeth: Array<{ tooth: number; states: Array<{ condition_code: string; status: string }> }>;
      counts: { total: number };
    }>(page.context(), clinicId, `/api/v1/patients/${patient.id}/chart/`);
    const tooth16 = chart.teeth.find((tooth) => tooth.tooth === 16);
    expect(tooth16, "tooth 16 must be present in the chart").toBeTruthy();
    expect(tooth16?.states.some((state) => state.condition_code === code)).toBe(true);
    expect(chart.counts.total).toBeGreaterThan(0);

    // A full reload proves persistence rather than React state.
    await page.reload();
    await expect(page.getByRole("heading", { name: "Odontogram" })).toBeVisible();
    await selectPatientInPicker(page, "chart-patient", patient);
    await page.getByTestId("tooth-16").click();

    await expect(
      page.getByRole("button", { name: new RegExp(`^${code}`) }).first(),
    ).toBeVisible();

    // The chart history records the change.
    await expect(page.getByRole("heading", { name: "History" })).toBeVisible();
    await expect(page.getByText("#16", { exact: false }).last()).toBeVisible();
    await expect(page.getByText("created", { exact: false }).first()).toBeVisible();
  });
});
