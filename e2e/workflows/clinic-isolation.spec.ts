import { test, expect } from "@playwright/test";
import { OWNER_STATE, activeClinicId } from "../fixtures";
import {
  apiGet,
  apiGetRaw,
  createPatient,
  getClinics,
  patientSeed,
  type ClinicSummary,
  type PatientRecord,
} from "./helpers";

test.use({ storageState: OWNER_STATE });

/**
 * Workflow 12: clinic isolation.
 *
 * The owner belongs to two clinics. A patient is created in each, then the test
 * proves both directions of the tenant boundary: a cross-clinic id is a 404 and
 * the clinic-scoped UI never lists the other clinic's record.
 */

test.describe("clinic isolation", () => {
  test("scopes patients to the active clinic in the API and the UI", async ({ page }) => {
    await page.goto("/app/patients");
    const centralId = await activeClinicId(page);
    const context = page.context();

    const clinics = await getClinics(context);
    const central = clinics.find(
      (clinic: ClinicSummary) => clinic.id === centralId,
    );
    const north = clinics.find(
      (clinic: ClinicSummary) => clinic.slug === "enamel-north",
    );
    expect(central, "expected the seeded central clinic").toBeTruthy();
    expect(north, "expected the owner to also belong to Enamel North").toBeTruthy();
    expect(north?.id).not.toBe(central?.id);

    const centralPatient = await createPatient(context, centralId, patientSeed("Central"));
    const northPatient = await createPatient(
      context,
      north!.id,
      patientSeed("North"),
    );

    // API boundary: the same id is invisible under the other clinic header.
    const foreignInCentral = await apiGetRaw(
      context,
      centralId,
      `/api/v1/patients/${northPatient.id}/`,
    );
    expect(foreignInCentral.status()).toBe(404);
    const foreignInNorth = await apiGetRaw(
      context,
      north!.id,
      `/api/v1/patients/${centralPatient.id}/`,
    );
    expect(foreignInNorth.status()).toBe(404);

    const northList = await apiGet<{ count: number; results: PatientRecord[] }>(
      context,
      north!.id,
      `/api/v1/patients/?search=${encodeURIComponent(northPatient.last_name)}&page_size=100`,
    );
    expect(northList.count).toBe(1);
    expect(northList.results[0].id).toBe(northPatient.id);

    const centralSearch = await apiGet<{ count: number; results: PatientRecord[] }>(
      context,
      centralId,
      `/api/v1/patients/?search=${encodeURIComponent(northPatient.last_name)}&page_size=100`,
    );
    expect(centralSearch.count).toBe(0);

    // Switch the active clinic through the shell and reload so every query is
    // rebuilt against the new tenant scope.
    const switcher = page.getByTestId("clinic-switcher");
    await expect(switcher).toBeVisible();
    await switcher.selectOption(north!.id);
    await expect
      .poll(() => page.evaluate(() => window.localStorage.getItem("enamel.activeClinicId")))
      .toBe(north!.id);
    await page.reload();

    const search = page.getByTestId("patient-search");
    await search.fill(northPatient.last_name);
    const northLink = page.getByRole("link", { name: new RegExp(northPatient.last_name) });
    await expect(northLink).toBeVisible();

    await search.fill(centralPatient.last_name);
    await expect(page.getByText("No patients found", { exact: true })).toBeVisible();
    await expect(
      page.getByRole("link", { name: new RegExp(centralPatient.last_name) }),
    ).toHaveCount(0);

    // The detail route is clinic-scoped too.
    await search.fill(northPatient.last_name);
    await northLink.click();
    await expect(page).toHaveURL(new RegExp(`/app/patients/${northPatient.id}$`));
    await expect(page.getByRole("heading", { name: "Demographics" })).toBeVisible();
    await expect(page.getByText(northPatient.email, { exact: false })).toBeVisible();

    // Switching back restores the other tenant's directory.
    await page.getByTestId("clinic-switcher").selectOption(centralId);
    await expect
      .poll(() => page.evaluate(() => window.localStorage.getItem("enamel.activeClinicId")))
      .toBe(centralId);
    await page.goto("/app/patients");
    await page.getByTestId("patient-search").fill(centralPatient.last_name);
    await expect(
      page.getByRole("link", { name: new RegExp(centralPatient.last_name) }),
    ).toBeVisible();
    await page.getByTestId("patient-search").fill(northPatient.last_name);
    await expect(page.getByText("No patients found", { exact: true })).toBeVisible();
  });
});
