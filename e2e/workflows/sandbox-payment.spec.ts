import { test, expect } from "@playwright/test";
import { OWNER_STATE, activeClinicId } from "../fixtures";
import {
  apiDownloadPdf,
  createPatient,
  getAcceptedPlan,
  getInvoice,
  invoiceFromPlan,
  patientSeed,
  type InvoiceRecord,
} from "./helpers";

test.use({ storageState: OWNER_STATE });

/**
 * Workflow 8: sandbox payment.
 *
 * An accepted plan becomes an issued invoice, the full balance is recorded
 * through the Billing UI's sandbox payment form, and the invoice settles to
 * `paid` with no balance. The receipt PDF is downloaded from the same screen.
 */

test.describe("sandbox payment", () => {
  test("settles an invoice and downloads its receipt", async ({ page }) => {
    await page.goto("/app/patients");
    const clinicId = await activeClinicId(page);
    const context = page.context();

    const patient = await createPatient(context, clinicId, patientSeed("Pay"));
    const plan = await getAcceptedPlan(
      context,
      clinicId,
      patient.id,
      `E2E Payment plan ${Date.now()}`,
    );
    const invoice = await invoiceFromPlan(context, clinicId, plan.id);
    expect(invoice.status).toBe("issued");
    expect(invoice.balance_minor).toBe(invoice.total_minor);
    expect(invoice.total_minor).toBe(plan.total_minor);

    await page.goto("/app/billing");
    await expect(page.getByRole("heading", { name: /Billing/ })).toBeVisible();

    const search = page.getByTestId("invoice-search");
    await search.fill(invoice.number);
    const row = page.locator("tbody tr").filter({ hasText: invoice.number });
    await expect(row).toHaveCount(1);
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: "View" }).click();

    const dialog = page.getByRole("dialog", { name: "Invoice detail" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(invoice.number, { exact: true })).toBeVisible();
    await expect(dialog.getByText("Balance", { exact: true })).toBeVisible();

    const amount = (invoice.total_minor / 100).toFixed(2);
    await page.getByTestId("payment-amount").fill(amount);
    await page.getByTestId("payment-method").selectOption("upi");
    await page.getByTestId("payment-submit").click();

    await expect(page.getByText("Payment recorded", { exact: true })).toBeVisible();

    await expect
      .poll(async () => {
        const current = await getInvoice(context, clinicId, invoice.id);
        return `${current.status}:${current.balance_minor}`;
      }, { message: "the invoice should settle after the sandbox payment" })
      .toBe("paid:0");

    const settled: InvoiceRecord = await getInvoice(context, clinicId, invoice.id);
    expect(settled.paid_minor).toBe(invoice.total_minor);
    expect(settled.balance_minor).toBe(0);

    // The detail panel refetches after the payment mutation.
    await expect(dialog.getByText("paid", { exact: false }).first()).toBeVisible();

    const downloadPromise = page.waitForEvent("download");
    await page.getByTestId("receipt-download").click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe(`receipt-${invoice.number}.pdf`);
    expect(await download.failure()).toBeNull();

    const receipt = await apiDownloadPdf(
      context,
      clinicId,
      `/api/v1/invoices/${invoice.id}/pdf/`,
    );
    expect(receipt.body.length).toBeGreaterThan(1_000);
    expect(receipt.sha256).toMatch(/^[0-9a-f]{64}$/);

    await expect(page.getByRole("alert")).toHaveCount(0);
  });
});
