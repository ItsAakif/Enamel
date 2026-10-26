import { test, expect } from "@playwright/test";
import { OWNER_STATE, activeClinicId } from "../fixtures";
import {
  createPatient,
  findAppointments,
  firstOpenSlot,
  getAppointmentTypes,
  getAvailability,
  getLocations,
  patientSeed,
  selectPatientInPicker,
  toKolkataLocalInput,
} from "./helpers";

test.use({ storageState: OWNER_STATE });

/**
 * Workflow 4: availability plus staff booking.
 *
 * The test asks the availability API for a genuinely open slot, books that
 * exact slot through the scheduling slide-over, then proves the appointment
 * exists and the slot has disappeared from availability.
 */

test.describe("availability and booking", () => {
  test("books an open slot through the scheduling UI", async ({ page }) => {
    await page.goto("/app/scheduling");
    const clinicId = await activeClinicId(page);
    const context = page.context();

    const patient = await createPatient(context, clinicId, patientSeed("Book"));
    const types = await getAppointmentTypes(context, clinicId);
    const locations = await getLocations(context, clinicId);
    const slot = await firstOpenSlot(context, clinicId, { appointmentTypeId: types[0].id });
    expect(slot.provider, "availability must resolve a provider lane").toBeTruthy();

    await page.getByTestId("new-appointment-btn").click();
    const dialog = page.getByRole("dialog", { name: "New appointment" });
    await expect(dialog).toBeVisible();

    await selectPatientInPicker(page, "appt-patient", patient);
    await page.getByTestId("appt-provider").selectOption(slot.provider as string);
    await page.getByTestId("appt-type").selectOption(types[0].id);
    await page.getByTestId("appt-location").selectOption(slot.location ?? locations[0].id);
    await page.getByTestId("appt-start").fill(toKolkataLocalInput(slot.start));
    if (slot.operatory) {
      await page.getByTestId("appt-operatory").selectOption(slot.operatory);
    }
    await page.getByTestId("appt-reason").fill("E2E availability booking");
    await page.getByTestId("appt-submit").click();

    await expect(dialog).toBeHidden();

    await expect
      .poll(async () => (await findAppointments(context, clinicId, patient.id)).length, {
        message: "the booked appointment should be readable from the API",
      })
      .toBe(1);

    const [appointment] = await findAppointments(context, clinicId, patient.id);
    expect(appointment.status).toBe("scheduled");
    expect(appointment.source).toBe("staff");
    expect(appointment.patient).toBe(patient.id);
    expect(appointment.provider).toBe(slot.provider);
    expect(new Date(appointment.start_at).toISOString()).toBe(new Date(slot.start).toISOString());
    expect(appointment.reason).toBe("E2E availability booking");

    // The slot is no longer offered for that provider.
    const availability = await getAvailability(
      context,
      clinicId,
      types[0].id,
      slot.start.slice(0, 10),
      slot.start.slice(0, 10),
    );
    const stillOpen = availability.slots.some(
      (candidate) =>
        candidate.provider === slot.provider &&
        new Date(candidate.start).toISOString() === new Date(slot.start).toISOString(),
    );
    expect(stillOpen, "a booked slot must not remain in availability").toBe(false);
  });
});
