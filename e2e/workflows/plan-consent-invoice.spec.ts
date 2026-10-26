import { test, expect } from "@playwright/test";
import { OWNER_STATE, activeClinicId } from "../fixtures";
import {
  apiDownloadPdf,
  apiList,
  createConsentRecord,
  createPatient,
  createPlan,
  getPlan,
  listInvoices,
  listPlans,
  patientSeed,
  planAction,
  type ConsentRecord,
  type PlanRecord,
} from "./helpers";

test.use({ storageState: OWNER_STATE });

/**
 * Workflow 7: treatment plan -> consent -> invoice.
 *
 * The plan lifecycle and the signed consent are arranged through the staff API
 * (the SPA has no plan editor yet), then the invoice is raised through the real
 * Billing UI. Every artifact is read back from the API, including both PDFs.
 */

test.describe("plan, consent and invoice", () => {
  test("accepts a plan, signs consent and raises an invoice from it", async ({ page }) => {
    await page.goto("/app/patients");
    const clinicId = await activeClinicId(page);
    const context = page.context();

    const patient = await createPatient(context, clinicId, patientSeed("Plan"));
    const stamp = Date.now();
    const title = `E2E Plan ${stamp}`;

    const presented = await createPlan(context, clinicId, patient.id, title);
    expect(presented.status).toBe("draft");

    // Presenting is a real transition and is visible on the returned plan.
    const presentedPlan = await planAction(context, clinicId, presented.id, "present");
    expect(presentedPlan.status).toBe("presented");
    expect(presentedPlan.items[0].status).toBe("accepted");

    const acceptedPlan = await planAction(context, clinicId, presented.id, "accept");
    expect(acceptedPlan.status).toBe("accepted");
    expect(acceptedPlan.total_minor).toBeGreaterThan(0);

    const storedPlan = await getPlan(context, clinicId, presented.id);
    expect(storedPlan.status).toBe("accepted");
    expect(storedPlan.items.length).toBe(1);

    const plans = await listPlans(context, clinicId, patient.id);
    expect(plans.map((plan: PlanRecord) => plan.id)).toContain(presented.id);

    // Sign the seeded treatment-consent template.
    const consent = await createConsentRecord(
      context,
      clinicId,
      patient.id,
      `${patient.first_name} ${patient.last_name}`,
    );
    expect(consent.status).toBe("signed");
    expect(consent.is_legally_compliant).toBe(true);
    expect(consent.template_version).toBeGreaterThanOrEqual(1);

    const consentRecords = await apiList<ConsentRecord>(
      context,
      clinicId,
      `/api/v1/consent-records/?patient=${patient.id}&page_size=100`,
    );
    expect(consentRecords.map((row) => row.id)).toContain(consent.id);

    const consentPdf = await apiDownloadPdf(context, clinicId, `/api/v1/consents/${consent.id}/pdf/`);
    expect(consentPdf.body.length).toBeGreaterThan(1_000);
    expect(consentPdf.sha256).toMatch(/^[0-9a-f]{64}$/);

    // Raise the invoice from the accepted plan through the Billing UI.
    await page.goto("/app/billing");
    await expect(page.getByRole("heading", { name: /Billing/ })).toBeVisible();
    await page.getByTestId("new-invoice-btn").click();
    const planDialog = page.getByRole("dialog", {
      name: "Create invoice from an accepted treatment plan",
    });
    await expect(planDialog).toBeVisible();

    const planRow = planDialog.locator("div.border").filter({ hasText: title });
    await expect(planRow).toHaveCount(1);
    await planRow.getByRole("button", { name: "Raise invoice" }).click();

    await expect(page.getByText("Invoice created", { exact: true })).toBeVisible();
    const invoiceDialog = page.getByRole("dialog", { name: "Invoice detail" });
    await expect(invoiceDialog).toBeVisible();

    const invoices = await listInvoices(context, clinicId, patient.id);
    const invoice = invoices.find((row) => row.plan === presented.id);
    expect(invoice, "the invoice must retain its source plan").toBeTruthy();
    expect(invoice?.status).toBe("issued");
    expect(invoice?.total_minor).toBe(acceptedPlan.total_minor);
    expect(invoice?.balance_minor).toBe(acceptedPlan.total_minor);
    expect(invoice?.patient).toBe(patient.id);

    await expect(invoiceDialog.getByText(invoice!.number, { exact: true })).toBeVisible();
    await expect(invoiceDialog.getByText("E2E ceramic crown")).toBeVisible();
    await expect(invoiceDialog.getByText("issued", { exact: false }).first()).toBeVisible();

    const invoicePdf = await apiDownloadPdf(
      context,
      clinicId,
      `/api/v1/invoices/${invoice!.id}/pdf/`,
    );
    expect(invoicePdf.body.length).toBeGreaterThan(1_000);
    expect(invoicePdf.sha256).toMatch(/^[0-9a-f]{64}$/);
  });
});
