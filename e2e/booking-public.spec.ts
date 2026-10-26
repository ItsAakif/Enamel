import { test, expect } from "@playwright/test";
import { DEMO } from "./fixtures";

test.describe("public booking", () => {
  test("renders the clinic, its services and bookable times", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("pageerror", (error) => consoleErrors.push(error.message));

    await page.goto(`/book/${DEMO.clinicSlug}`);

    await expect(page.getByText(DEMO.clinicName).first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "1. Choose a service" })).toBeVisible();

    const serviceCard = page
      .locator("div.glass-card")
      .filter({ has: page.getByRole("heading", { name: "1. Choose a service" }) });
    const serviceButton = serviceCard.getByRole("button").first();
    await expect(serviceButton).toBeVisible();
    await serviceButton.click();

    const timeCard = page
      .locator("div.glass-card")
      .filter({ has: page.getByRole("heading", { name: "2. Pick a time" }) });
    await expect(timeCard).toBeVisible();

    // Either real slots load, or the API reports none for the next two weeks.
    await expect(
      timeCard
        .getByRole("button")
        .first()
        .or(timeCard.getByRole("heading", { name: "No open times" })),
    ).toBeVisible({ timeout: 20_000 });

    expect(consoleErrors, `page errors: ${consoleErrors.join(" | ")}`).toEqual([]);
  });
});
