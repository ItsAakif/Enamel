import { test, expect } from "@playwright/test";
import { OWNER_STATE, activeClinicId } from "../fixtures";
import {
  apiPost,
  patientSeed,
  getPatient,
} from "./helpers";

test.use({ storageState: OWNER_STATE });

/**
 * Workflow 2: create a patient through the staff UI, then record clinical
 * context (allergy + medical history) and confirm the patient detail renders it.
 */

test.describe("patient record", () => {
  test("creates a patient in the UI and renders clinical context", async ({ page }) => {
    const seed = patientSeed("Record");
    await page.goto("/app/patients");
    const clinicId = await activeClinicId(page);

    await page.getByTestId("new-patient-btn").click();
    const dialog = page.getByRole("dialog", { name: "New patient" });
    await expect(dialog).toBeVisible();

    await page.getByTestId("patient-first-name").fill(seed.firstName);
    await page.getByTestId("patient-last-name").fill(seed.lastName);
    await dialog.locator('input[type="email"]').fill(seed.email);
    await dialog
      .locator("label", { hasText: "Phone" })
      .locator("xpath=..")
      .locator("input")
      .fill(seed.phone);
    await dialog.locator('input[type="date"]').fill(seed.dateOfBirth);
    await page.getByTestId("patient-submit").click();

    await expect(page.getByText("Patient created", { exact: true })).toBeVisible();

    // Open the new record from the real directory.
    await page.getByTestId("patient-search").fill(seed.email);
    const link = page.getByRole("link", { name: new RegExp(seed.lastName) });
    await expect(link).toBeVisible();
    await link.click();
    await expect(page).toHaveURL(/\/app\/patients\/[0-9a-f-]{36}$/);

    const patient = await getPatient(
      page.context(),
      clinicId,
      page.url().split("/").pop() as string,
    );
    expect(patient.email).toBe(seed.email);
    expect(patient.phone_e164).toBe(seed.phone);
    expect(patient.date_of_birth).toBe(seed.dateOfBirth);

    // Demographics are rendered from the API, not from the form state.
    await expect(page.getByRole("heading", { name: "Demographics" })).toBeVisible();
    await expect(page.getByText(seed.email, { exact: false })).toBeVisible();
    await expect(page.getByText(seed.phone, { exact: false })).toBeVisible();
    await expect(page.getByText("12 Apr 1988", { exact: true })).toBeVisible();

    // Record an allergy and a condition through the staff API, then reload the
    // page so the assertions are against a fresh server read.
    await apiPost(page.context(), clinicId, `/api/v1/patients/${patient.id}/allergies/`, {
      substance: "Penicillin E2E",
      reaction: "Rash",
      severity: "severe",
      is_active: true,
    });
    await apiPost(
      page.context(),
      clinicId,
      `/api/v1/patients/${patient.id}/medical-history/`,
      {
        kind: "condition",
        label: "Type 2 diabetes E2E",
        since: "2019-06-01",
        notes: "Controlled with metformin",
        is_active: true,
      },
    );

    await page.reload();
    await expect(page.getByText("Penicillin E2E")).toBeVisible();
    await expect(page.getByText("Type 2 diabetes E2E")).toBeVisible();

    // The clinical timeline is built server-side from the same events.
    await expect(page.getByRole("heading", { name: "Clinical timeline" })).toBeVisible();
    await expect(page.getByText("Allergy recorded", { exact: true })).toBeVisible();
    // "Medical history" is both a section heading (h3) and a timeline event
    // title (h4), so the section is addressed by its heading level.
    await expect(
      page.getByRole("heading", { level: 3, name: "Medical history" }),
    ).toBeVisible();
  });
});
