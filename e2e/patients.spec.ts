import { test, expect } from "@playwright/test";
import { OWNER_STATE, activeClinicId, createPatientViaApi, uniqueEmail } from "./fixtures";

test.use({ storageState: OWNER_STATE });

test.describe("patients", () => {
  test("lists patients and filters server-side", async ({ page }) => {
    await page.goto("/app/patients");
    const clinicId = await activeClinicId(page);

    const lastName = `Zz${Date.now()}`;
    const email = uniqueEmail("patient.search");
    await createPatientViaApi(page.context(), clinicId, email, "Search", lastName);

    await expect(page.getByRole("heading", { name: "Patient Records" })).toBeVisible();

    await page.getByTestId("patient-search").fill(lastName);

    await expect(page.getByRole("link", { name: new RegExp(lastName) })).toBeVisible();
    await expect(page.getByRole("cell", { name: email })).toBeVisible();
  });

  test("creates a patient through the slide-over and refreshes the list", async ({ page }) => {
    await page.goto("/app/patients");

    const lastName = `Ui${Date.now()}`;
    const email = uniqueEmail("patient.ui");

    await page.getByTestId("new-patient-btn").click();
    await page.getByTestId("patient-first-name").fill("Ui");
    await page.getByTestId("patient-last-name").fill(lastName);
    await page.locator('input[type="email"]').fill(email);
    await page.getByTestId("patient-submit").click();

    await page.getByTestId("patient-search").fill(lastName);
    await expect(page.getByRole("link", { name: new RegExp(lastName) })).toBeVisible();
  });

  test("a search with no matches shows the empty state, never stale rows", async ({ page }) => {
    await page.goto("/app/patients");
    await page.getByTestId("patient-search").fill(`nomatch-${Date.now()}`);
    await expect(page.getByText("No patients found")).toBeVisible();
  });
});
