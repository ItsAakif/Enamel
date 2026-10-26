import { test, expect, type Page } from "@playwright/test";
import { OWNER_STATE, activeClinicId, uniqueEmail } from "./fixtures";

test.use({ storageState: OWNER_STATE });

/**
 * Settings end-to-end.
 *
 * Every assertion is against server state: after a mutation the page refetches,
 * and the profile/location/member checks reload or re-read the list rather than
 * trusting the optimistic form value.
 *
 * The demo clinic is shared with other specs, so mutations that touch seeded
 * data (the owner's phone, the assistant membership) are restored before the
 * test finishes.
 */

async function openSettingsTab(page: Page, id: string): Promise<void> {
  await page.goto("/app/settings");
  await page.getByTestId(`settings-tab-${id}`).click();
}

test.describe("settings", () => {
  test("clinic profile loads from the API and persists a PATCH", async ({ page }) => {
    await openSettingsTab(page, "profile");
    await expect(page.getByTestId("clinic-name")).toHaveValue("Enamel Central");

    const phone = page.locator("#clinic-phone");
    const original = await phone.inputValue();
    const updated = `+91 90000 ${Date.now().toString().slice(-5)}`;

    await phone.fill(updated);
    const saved = page.waitForResponse(
      (response) =>
        response.url().includes("/api/v1/clinics/") && response.request().method() === "PATCH",
    );
    await page.getByTestId("clinic-save").click();
    await saved;
    await expect(page.getByText("Clinic profile saved.")).toBeVisible();

    // Reload: the value must come from the API, not from component state.
    await page.reload();
    await expect(page.getByTestId("settings-tab-profile")).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("#clinic-phone")).toHaveValue(updated);

    // Restore the seeded phone so other runs/specs see stable demo data.
    await page.locator("#clinic-phone").fill(original);
    // Editing the form invalidates the previous confirmation.
    await expect(page.getByText("Clinic profile saved.")).toBeHidden();
    const restored = page.waitForResponse(
      (response) =>
        response.url().includes("/api/v1/clinics/") && response.request().method() === "PATCH",
    );
    await page.getByTestId("clinic-save").click();
    await restored;
    await expect(page.getByText("Clinic profile saved.")).toBeVisible();
  });

  test("locations can be created, edited and deleted", async ({ page }) => {
    await openSettingsTab(page, "locations");

    const name = `E2E Site ${Date.now()}`;
    const edited = `${name} Edited`;

    await page.getByTestId("location-name").fill(name);
    await page.locator("#location-phone").fill("+91 90000 11111");
    await page.locator("#location-timezone").fill("Asia/Kolkata");
    await page.getByTestId("location-save").click();

    const list = page.getByTestId("locations-list");
    const row = list.locator("li").filter({ hasText: name });
    await expect(row).toBeVisible();

    await row.getByRole("button", { name: `Edit ${name}` }).click();
    await page.getByTestId("location-name").fill(edited);
    await page.getByTestId("location-save").click();

    const editedRow = list.locator("li").filter({ hasText: edited });
    await expect(editedRow).toBeVisible();

    await editedRow.getByRole("button", { name: `Delete ${edited}` }).click();
    await expect(list.locator("li").filter({ hasText: edited })).toHaveCount(0);
  });

  test("members list, invite, role change, suspend and reactivate are real API mutations", async ({
    page,
  }) => {
    await openSettingsTab(page, "members");

    const list = page.getByTestId("members-list");
    const assistant = list.locator("li").filter({ hasText: "assistant@enamel.demo" });
    await expect(assistant).toBeVisible();
    await expect(assistant).toContainText("active");

    // Role change and back.
    const role = assistant.locator("select");
    await role.selectOption("receptionist");
    await expect(assistant).toContainText("receptionist");
    await role.selectOption("assistant");
    await expect(assistant).toContainText("assistant");

    // Suspend and reactivate.
    await assistant.getByRole("button", { name: "Suspend" }).click();
    await expect(assistant).toContainText("suspended");
    await assistant.getByRole("button", { name: "Reactivate" }).click();
    await expect(assistant).toContainText("active");

    // Pending invitation round-trip.
    const email = uniqueEmail("invite.settings");
    await page.getByTestId("invite-email").fill(email);
    await page.getByTestId("invite-role").selectOption("receptionist");
    await page.getByTestId("invite-submit").click();

    const invitations = page.getByTestId("invitations-list");
    await expect(invitations.locator("li").filter({ hasText: email })).toBeVisible();
    await page.getByRole("button", { name: `Revoke invitation for ${email}` }).click();
    await expect(invitations.locator("li").filter({ hasText: email })).toHaveCount(0);
  });

  test("consent templates can be created and edited (versioned)", async ({ page }) => {
    await openSettingsTab(page, "consent");

    const key = `e2e_consent_${Date.now()}`;
    const name = `E2E Consent ${Date.now()}`;
    const edited = `${name} v2`;

    await page.locator("#template-name").fill(name);
    await page.locator("#template-key").fill(key);
    await page.locator("#template-purpose").fill("treatment");
    await page.locator("#template-body").fill("I consent to the proposed treatment.");
    await page.getByTestId("template-save").click();

    const list = page.getByTestId("templates-list");
    const row = list.locator("li").filter({ hasText: name });
    await expect(row).toBeVisible();

    await row.getByRole("button", { name: `Edit ${name}` }).click();
    await page.locator("#template-name").fill(edited);
    await page.getByTestId("template-save").click();

    const editedRow = list.locator("li").filter({ hasText: edited });
    await expect(editedRow).toBeVisible();
    await expect(editedRow).toContainText("v2");
  });

  test("booking policy is read-only and connectors expose a simulator mode", async ({ page }) => {
    await openSettingsTab(page, "booking");

    await expect(page.getByText("Read-only.", { exact: false })).toBeVisible();
    await expect(page.getByText("Minimum lead time")).toBeVisible();
    await expect(page.getByText("Cancel cutoff")).toBeVisible();
    await expect(page.getByText("Online rescheduling")).toBeVisible();

    await page.getByTestId("settings-tab-integrations").click();
    const name = `E2E Connector ${Date.now()}`;
    await page.getByTestId("connector-name").fill(name);
    await page.locator("#connector-mode").selectOption("simulator");
    await page.getByTestId("connector-save").click();

    const list = page.getByTestId("connectors-list");
    const row = list.locator("li").filter({ hasText: name });
    await expect(row).toBeVisible();
    await expect(row.getByText("simulator", { exact: true })).toBeVisible();

    await row.getByRole("button", { name: `Sync ${name}` }).click();
    await expect(page.getByText(/Sync started in simulator mode/)).toBeVisible();

    await row.getByRole("button", { name: "Runs" }).click();
    await expect(row.getByText("succeeded")).toBeVisible({ timeout: 20_000 });
  });

  test("the owner audit log filters and paginates with cursors", async ({ page }) => {
    await openSettingsTab(page, "audit");

    await expect(page.getByTestId("audit-table")).toBeVisible();

    // Filter to an entity type produced by this spec's location test.
    await page.getByTestId("audit-entity-type").fill("clinics.location");
    await page.getByTestId("audit-apply").click();
    await expect(page.getByTestId("audit-table").getByText("location.created").first()).toBeVisible();

    // Clear the filter and walk to the next cursor page.
    await page.getByRole("button", { name: "Clear" }).click();
    await expect(page.getByTestId("audit-entity-type")).toHaveValue("");

    const next = page.getByRole("button", { name: "Next" });
    await expect(next).toBeEnabled();
    await next.click();
    await expect(page.getByRole("button", { name: "Previous" })).toBeEnabled();
  });
});
