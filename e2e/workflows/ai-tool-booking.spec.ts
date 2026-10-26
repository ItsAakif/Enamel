import { test, expect } from "@playwright/test";
import { OWNER_STATE, activeClinicId } from "../fixtures";
import {
  apiGet,
  apiPost,
  createPatient,
  findAppointments,
  firstOpenSlotForEveryProvider,
  getAppointmentTypes,
  patientSeed,
  rawPost,
} from "./helpers";

test.use({ storageState: OWNER_STATE });

interface VoiceCallResource {
  id: string;
  status: string;
  patient_id: string | null;
  identity_confirmed: boolean;
  outcome: string;
  summary: string;
  turns: Array<{ role: string; kind: string; text: string }>;
  tool_calls: Array<{
    tool: string;
    status: string;
    confirmed: boolean;
    result_json: Record<string, unknown> | null;
  }>;
}

interface ApiErrorBody {
  error: {
    code: string;
    details?: { confirmation_nonce?: string; missing_features?: string[] };
  };
}

/**
 * Workflow 9: AI tool booking.
 *
 * A real call is started against an existing patient, identity is confirmed by
 * the caller's date of birth, and `book_appointment` goes through the registry's
 * argument validation and confirmation gates before creating an `ai` appointment.
 */

test.describe("AI tool booking", () => {
  test("confirms identity and books through the guarded booking tool", async ({ page }) => {
    await page.goto("/app/patients");
    const clinicId = await activeClinicId(page);
    const context = page.context();

    const patient = await createPatient(context, clinicId, patientSeed("Voice"));
    expect(patient.date_of_birth).toBeTruthy();

    const call = await apiPost<VoiceCallResource>(
      context,
      clinicId,
      "/api/v1/ai/calls/",
      { patient_id: patient.id, recording_consent: false },
    );
    expect(call.patient_id).toBe(patient.id);
    expect(call.identity_confirmed).toBe(false);

    // The tool resolves a real active clinic appointment type (by UUID or code),
    // never an arbitrary label.
    const types = await getAppointmentTypes(context, clinicId);
    const examType = types.find((entry) => entry.code === "exam") ?? types[0];
    expect(examType, "expected a seeded appointment type").toBeTruthy();

    // The registry books with the clinic's default provider, so the requested
    // start is a slot that every bookable provider can take.
    const slot = await firstOpenSlotForEveryProvider(context, clinicId, {
      appointmentTypeId: examType.id,
      minDaysAhead: 21,
    });
    const args = {
      appointment_type: examType.id,
      start_at: slot.start,
      reason: "E2E AI booking",
    };

    // Identity gate: the tool refuses to act before the caller is identified.
    const unidentified = await rawPost(
      context,
      clinicId,
      `/api/v1/ai/calls/${call.id}/tools/book_appointment/`,
      { args, confirmed: false },
    );
    expect(unidentified.status()).toBe(409);
    expect(((await unidentified.json()) as ApiErrorBody).error.code).toBe("identity_required");
    expect(await findAppointments(context, clinicId, patient.id)).toHaveLength(0);

    await page.goto("/app/voice-ai");
    await expect(page.getByRole("heading", { name: "Voice AI" })).toBeVisible();
    const callRow = page.getByTestId(`call-row-${call.id}`);
    await expect(callRow).toBeVisible();
    await callRow.click();

    // Identity gate: the caller proves ownership with the stored date of birth.
    await page.getByTestId("turn-input").fill(`My date of birth is ${patient.date_of_birth}`);
    await page.getByTestId("turn-submit").click();
    await expect(
      page.getByText("Thanks, I have confirmed your identity.", { exact: true }),
    ).toBeVisible();

    const identified = await apiGet<VoiceCallResource>(
      context,
      clinicId,
      `/api/v1/ai/calls/${call.id}/`,
    );
    expect(identified.identity_confirmed).toBe(true);

    // A booking request produces an assistant confirmation prompt (not a write).
    await page.getByTestId("turn-input").fill("I would like to book a cleaning appointment");
    await page.getByTestId("turn-submit").click();
    await expect(page.getByText(/would like me to book appointment/i)).toBeVisible();

    // Extra/mistyped arguments are rejected before any confirmation gate.
    const invalid = await rawPost(
      context,
      clinicId,
      `/api/v1/ai/calls/${call.id}/tools/book_appointment/`,
      { args: { ...args, clinic_id: clinicId }, confirmed: false },
    );
    expect(invalid.status()).toBe(400);
    expect(((await invalid.json()) as ApiErrorBody).error.code).toBe("invalid_arguments");

    // Unconfirmed execution returns the nonce bound to this call.
    const unconfirmed = await rawPost(
      context,
      clinicId,
      `/api/v1/ai/calls/${call.id}/tools/book_appointment/`,
      { args, confirmed: false },
    );
    expect(unconfirmed.status()).toBe(409);
    const unconfirmedBody = (await unconfirmed.json()) as ApiErrorBody;
    expect(unconfirmedBody.error.code).toBe("confirmation_required");
    const nonce = unconfirmedBody.error.details?.confirmation_nonce ?? "";
    expect(nonce).toMatch(/^[0-9a-f]{32}$/);

    // A forged nonce is rejected and nothing is written.
    const forged = await rawPost(
      context,
      clinicId,
      `/api/v1/ai/calls/${call.id}/tools/book_appointment/`,
      { args, confirmed: true, confirmation_nonce: "0".repeat(32) },
    );
    expect(forged.status()).toBe(409);
    expect(((await forged.json()) as ApiErrorBody).error.code).toBe("confirmation_invalid");
    expect(await findAppointments(context, clinicId, patient.id)).toHaveLength(0);

    const booked = await apiPost<{
      status: string;
      result: { appointment_id: string; duplicate: boolean };
    }>(
      context,
      clinicId,
      `/api/v1/ai/calls/${call.id}/tools/book_appointment/`,
      { args, confirmed: true, confirmation_nonce: nonce },
    );
    expect(booked.status).toBe("ok");
    expect(booked.result.duplicate).toBe(false);

    const appointments = await findAppointments(context, clinicId, patient.id);
    const appointment = appointments.find((row) => row.id === booked.result.appointment_id);
    expect(appointment, "the tool must create a real appointment").toBeTruthy();
    expect(appointment?.source).toBe("ai");
    expect(appointment?.reason).toBe("E2E AI booking");
    expect(new Date(appointment!.start_at).toISOString()).toBe(new Date(slot.start).toISOString());

    // The nonce is single use: replaying it is rejected and books nothing.
    const replayed = await rawPost(
      context,
      clinicId,
      `/api/v1/ai/calls/${call.id}/tools/book_appointment/`,
      { args, confirmed: true, confirmation_nonce: nonce },
    );
    expect(replayed.status()).toBe(409);
    expect(((await replayed.json()) as ApiErrorBody).error.code).toBe("confirmation_invalid");
    expect(await findAppointments(context, clinicId, patient.id)).toHaveLength(1);

    const detail = await apiGet<VoiceCallResource>(
      context,
      clinicId,
      `/api/v1/ai/calls/${call.id}/`,
    );
    expect(detail.outcome).toBe("booked");
    expect(detail.summary).toBe("Appointment booked.");
    const successful = detail.tool_calls.filter(
      (entry) => entry.tool === "book_appointment" && entry.status === "ok",
    );
    expect(successful).toHaveLength(1);
    expect(successful[0].confirmed).toBe(true);

    // Reloading proves the trace/outcome come from the server, not local state.
    await page.reload();
    await expect(page.getByRole("heading", { name: "Voice AI" })).toBeVisible();
    await page.getByTestId(`call-row-${call.id}`).click();
    await expect(page.getByText("book_appointment", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("ok", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Appointment booked.", { exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "booked" })).toBeVisible();
  });
});
