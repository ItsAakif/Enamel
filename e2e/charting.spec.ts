import { test, expect } from "@playwright/test";
import { OWNER_STATE, activeClinicId, createPatientViaApi, uniqueEmail } from "./fixtures";

test.use({ storageState: OWNER_STATE });

test.describe("charting", () => {
  test("renders the FDI odontogram and saves a condition through the API", async ({ page }) => {
    await page.goto("/app/charting");
    const clinicId = await activeClinicId(page);
    await createPatientViaApi(page.context(), clinicId, uniqueEmail("chart.patient"), "Chart", "Subject");

    await page.reload();
    await expect(page.getByRole("heading", { name: "Odontogram" })).toBeVisible();

    // The patient picker auto-selects the first patient, so the chart loads.
    await expect(page.getByTestId("tooth-11")).toBeVisible();
    await expect(page.getByTestId("tooth-48")).toBeVisible();

    // Selecting a tooth opens the condition editor for that tooth.
    await page.getByTestId("tooth-16").click();
    await expect(page.getByText("#16")).toBeVisible();

    // Pick the first real condition code offered by the API legend.
    const conditionSelect = page.getByTestId("chart-condition");
    const firstCode = await conditionSelect.locator("option").nth(1).getAttribute("value");
    expect(firstCode, "expected at least one condition code in the legend").toBeTruthy();
    await conditionSelect.selectOption(firstCode as string);

    await page.getByTestId("chart-save").click();
    await expect(page.getByText("Chart saved", { exact: true })).toBeVisible();
    await expect(page.getByText("Tooth 16 updated.", { exact: true })).toBeVisible();
  });
});
