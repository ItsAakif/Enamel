import { test, expect, type BrowserContext } from "@playwright/test";
import { OWNER_STATE, activeClinicId } from "../fixtures";
import {
  createAppointment,
  findAppointments,
  firstOpenSlot,
  getAppointment,
  getAppointmentTypes,
  getProviders,
  kolkataDate,
  kolkataIso,
  patientSeed,
  createPatient,
  toKolkataLocalInput,
  type AppointmentRecord,
} from "./helpers";

test.use({ storageState: OWNER_STATE });

async function bookTodaySlot(
  context: BrowserContext,
  clinicId: string,
  patientId: string,
  providerId: string,
): Promise<AppointmentRecord> {
  const today = kolkataDate(0);
  let lastError: unknown = null;
  for (const hour of [14, 15, 16, 13, 12, 11, 10, 9, 8, 17]) {
    try {
      return await createAppointment(context, clinicId, patientId, {
        startAt: kolkataIso(today, hour),
        providerId,
        reason: "E2E reschedule source",
      });
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError ?? new Error("no free hour today");
}

/**
 * Workflow 5: reschedule then cancel.
 *
 * The appointment is created at a visible hour today, rescheduled through the
 * slide-over to a real open slot, then cancelled from the daily board.
 */

test.describe("reschedule and cancel", () => {
  test("moves an appointment and then cancels it", async ({ page }) => {
    await page.goto("/app/scheduling");
    const clinicId = await activeClinicId(page);
    const context = page.context();

    const patient = await createPatient(context, clinicId, patientSeed("Move"));
    const providers = await getProviders(context, clinicId);
    const types = await getAppointmentTypes(context, clinicId);
    expect(providers.length, "expected two seeded providers").toBeGreaterThan(1);

    const original = await bookTodaySlot(context, clinicId, patient.id, providers[1].id);

    await page.reload();
    await expect(page.getByRole("heading", { name: "Scheduling Center" })).toBeVisible();

    const boardAppointment = page.getByRole("button", {
      name: new RegExp(patient.last_name),
    });
    await expect(boardAppointment).toBeVisible();
    await boardAppointment.click();
    await expect(page.getByTestId("appointment-detail")).toBeVisible();

    // The board is in weekly view; the appointment was booked for today.
    const target = await firstOpenSlot(context, clinicId, {
      appointmentTypeId: types[0].id,
      // A reschedule keeps the original provider, so the target slot has to be
      // free for *that* provider, not for any lane of the clinic.
      providerId: original.provider,
    });

    await page.getByRole("button", { name: "Reschedule", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Reschedule appointment" });
    await expect(dialog).toBeVisible();
    await page.getByTestId("appt-start").fill(toKolkataLocalInput(target.start));
    await page.getByTestId("appt-submit").click();
    await expect(dialog).toBeHidden();

    await expect
      .poll(async () => {
        const rows = await findAppointments(context, clinicId, patient.id);
        return rows.some(
          (row) =>
            row.status === "scheduled" &&
            new Date(row.start_at).toISOString() === new Date(target.start).toISOString(),
        );
      }, { message: "the rescheduled appointment should exist" })
      .toBe(true);

    const rows = await findAppointments(context, clinicId, patient.id);
    const old = rows.find((row) => row.id === original.id);
    const replacement = rows.find(
      (row) =>
        row.status === "scheduled" &&
        new Date(row.start_at).toISOString() === new Date(target.start).toISOString(),
    );

    expect(old?.status).toBe("cancelled");
    expect(old?.cancellation_reason).toBe("rescheduled");
    expect(replacement, "expected the replacement appointment").toBeTruthy();
    expect(replacement?.rescheduled_from).toBe(original.id);
    expect(replacement?.provider).toBe(original.provider);

    // Navigate the daily board to the replacement's day and open it.
    await page.getByTestId("view-daily").click();
    // The board buckets by the clinic-local calendar day, so the target day is
    // derived in clinic time rather than from the raw UTC instant.
    const targetDay = toKolkataLocalInput(target.start).slice(0, 10);
    let anchor = kolkataDate(0);
    for (let guard = 0; anchor !== targetDay && guard < 30; guard += 1) {
      await page.getByTestId("next-range").click();
      const nextDate = new Date(`${anchor}T12:00:00+05:30`);
      nextDate.setUTCDate(nextDate.getUTCDate() + 1);
      anchor = nextDate.toISOString().slice(0, 10);
    }
    expect(anchor).toBe(targetDay);

    const dailyAppointment = page.getByRole("button", {
      name: new RegExp(patient.last_name),
    });
    await expect(dailyAppointment).toBeVisible();
    await dailyAppointment.click();
    await expect(page.getByTestId("appointment-detail")).toBeVisible();

    await page.getByRole("button", { name: "Cancel appointment", exact: true }).click();

    await expect
      .poll(async () => (await getAppointment(context, clinicId, replacement!.id)).status)
      .toBe("cancelled");

    const cancelled = await getAppointment(context, clinicId, replacement!.id);
    expect(cancelled.cancellation_reason).toBe("Cancelled from scheduling board");
    await expect(page.getByTestId("appointment-detail")).toBeHidden();
  });
});
