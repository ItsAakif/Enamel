import { test, expect, type BrowserContext } from "@playwright/test";
import { OWNER_STATE, activeClinicId } from "../fixtures";
import {
  createAppointment,
  createPatient,
  getMessages,
  getMessageTemplates,
  getProviders,
  grantMessagingConsent,
  kolkataDate,
  kolkataIso,
  patientSeed,
  runShell,
  type AppointmentRecord,
} from "./helpers";

test.use({ storageState: OWNER_STATE });

async function bookFutureAppointment(
  context: BrowserContext,
  clinicId: string,
  patientId: string,
  providerId: string,
): Promise<AppointmentRecord> {
  let lastError: unknown = null;
  for (const days of [9, 10, 11, 12]) {
    for (const hour of [10, 11, 14, 15, 9, 16]) {
      try {
        return await createAppointment(context, clinicId, patientId, {
          startAt: kolkataIso(kolkataDate(days), hour),
          providerId,
          reason: "E2E reminder appointment",
        });
      } catch (error) {
        lastError = error;
      }
    }
  }
  throw lastError ?? new Error("no free future slot");
}

interface ReminderHarnessResult {
  pending: number;
  resumed: number;
  statuses: Array<{ appointment: string; step: string; status: string }>;
}

/**
 * Workflow 6: reminder job outcome.
 *
 * A real appointment enqueues three future reminder jobs. The journey grants
 * the required consent, proves nothing sent early, then forces the durable
 * jobs due through the same `core.jobs.run_job` path the worker/beat uses and
 * asserts the messages were actually sent (not skipped by quiet hours or the
 * consent gate).
 *
 * A second appointment belongs to a patient without consent: the same jobs run
 * and the dispatcher must *skip* them with `no_consent` instead of sending.
 */

test.describe("reminder job", () => {
  test.skip(
    !process.env.E2E_BASE_URL,
    "forcing durable jobs due needs the compose stack (docker exec)",
  );

  test("dispatches due appointment reminders and records sent messages", async ({ page }) => {
    const context = page.context();
    await page.goto("/app");
    const clinicId = await activeClinicId(page);

    const patient = await createPatient(context, clinicId, patientSeed("Remind"));
    await grantMessagingConsent(
      context,
      clinicId,
      patient.id,
      "email",
      "appointment_reminders",
    );

    // The same journey for a patient that never granted messaging consent.
    const unconsented = await createPatient(context, clinicId, patientSeed("NoConsent"));

    const providers = await getProviders(context, clinicId);
    expect(providers.length).toBeGreaterThan(1);
    const appointment = await bookFutureAppointment(
      context,
      clinicId,
      patient.id,
      providers[1].id,
    );
    const unconsentedAppointment = await bookFutureAppointment(
      context,
      clinicId,
      unconsented.id,
      providers[0].id,
    );

    // Three future steps are queued but none has run yet.
    await expect
      .poll(async () => (await getMessages(context, clinicId, patient.id)).length, {
        message: "reminders must not send before their due date",
      })
      .toBe(0);
    await expect
      .poll(async () => (await getMessages(context, clinicId, unconsented.id)).length, {
        message: "reminders must not send before their due date",
      })
      .toBe(0);

    const quotedClinic = JSON.stringify(clinicId);
    const appointmentIds = JSON.stringify([
      appointment.id,
      unconsentedAppointment.id,
    ]);

    // Read the clinic's real messaging policy *before* anything mutates it, so
    // the restore below can always put the seeded policy back - even when the
    // harness script itself fails half way through.
    const originalMessaging = runShell<Record<string, unknown> | null>(`
from apps.clinics.models import Clinic

clinic = Clinic.objects.get(pk=${quotedClinic})
__result = (clinic.settings_json or {}).get("messaging")
`);

    try {
      const forced = runShell<ReminderHarnessResult>(`
from apps.core.models import JobRequest
from apps.clinics.models import Clinic
from apps.core.jobs import run_job
from django.utils import timezone

appointment_ids = ${appointmentIds}
clinic_id = ${quotedClinic}
clinic = Clinic.objects.get(pk=clinic_id)
settings_json = dict(clinic.settings_json or {})
messaging = dict(settings_json.get("messaging") or {})
messaging["quiet_hours_start"] = 0
messaging["quiet_hours_end"] = 0
settings_json["messaging"] = messaging
clinic.settings_json = settings_json
clinic.save(update_fields=["settings_json"])

pending = [
    job
    for job in JobRequest.objects.filter(kind="messaging.reminder", status="pending")
    if job.payload.get("appointment_id") in appointment_ids
]
pending.sort(key=lambda job: (job.due_at, str(job.id)))
now = timezone.now()
for job in pending:
    job.due_at = now
    job.save(update_fields=["due_at"])
resumed = 0
for job in pending:
    if run_job(job.id) is not None:
        resumed += 1
statuses = [
    {
        "appointment": job.payload.get("appointment_id"),
        "step": job.payload.get("step"),
        "status": job.status,
    }
    for job in JobRequest.objects.filter(kind="messaging.reminder")
    if job.payload.get("appointment_id") in appointment_ids
]
statuses.sort(key=lambda row: (row["appointment"], row["step"]))
__result = {
    "pending": len(pending),
    "resumed": resumed,
    "statuses": statuses,
}
`);

      expect(forced.pending, "a future appointment should queue three reminders").toBe(6);

      await expect
        .poll(async () => (await getMessages(context, clinicId, patient.id)).length, {
          message: "three reminder messages should be recorded",
          timeout: 30_000,
        })
        .toBe(3);

      const messages = await getMessages(context, clinicId, patient.id);
      expect(messages.every((message) => message.purpose === "appointment_reminders")).toBe(true);
      expect(messages.every((message) => message.channel === "email")).toBe(true);
      expect(messages.every((message) => message.status === "sent")).toBe(true);
      expect(messages.every((message) => message.reason === "")).toBe(true);

      // Every send used the approved appointment-reminder template.
      const templates = await getMessageTemplates(context, clinicId);
      const reminderTemplate = templates.find((template) => template.key === "appointment_reminder");
      expect(reminderTemplate, "expected the seeded appointment_reminder template").toBeTruthy();
      expect(
        messages.every((message) => message.template === reminderTemplate!.id),
      ).toBe(true);
      expect(messages.every((message) => message.template_version === 1)).toBe(true);

      // The consent gate skips the very same jobs for the other patient.
      await expect
        .poll(async () => (await getMessages(context, clinicId, unconsented.id)).length, {
          message: "three skipped reminder messages should be recorded",
          timeout: 30_000,
        })
        .toBe(3);

      const skipped = await getMessages(context, clinicId, unconsented.id);
      expect(skipped.every((message) => message.status === "skipped")).toBe(true);
      expect(skipped.every((message) => message.reason === "no_consent")).toBe(true);
      expect(skipped.every((message) => message.purpose === "appointment_reminders")).toBe(true);

      // The durable rows finished, one per step, with no retries left behind.
      await expect
        .poll(
          async () => {
            const settled = runShell<Array<{ step: string; status: string }>>(`
from apps.core.models import JobRequest
appointment_ids = ${appointmentIds}
__result = [
    {"step": job.payload.get("step"), "status": job.status}
    for job in JobRequest.objects.filter(kind="messaging.reminder")
    if job.payload.get("appointment_id") in appointment_ids
]
`);
            return settled.length === 6 && settled.every((row) => row.status === "done");
          },
          { message: "every reminder job should settle as done", timeout: 60_000 },
        )
        .toBe(true);

      const settled = runShell<Array<{ appointment: string; step: string; status: string }>>(`
from apps.core.models import JobRequest
appointment_ids = ${appointmentIds}
rows = [
    {
        "appointment": job.payload.get("appointment_id"),
        "step": job.payload.get("step"),
        "status": job.status,
    }
    for job in JobRequest.objects.filter(kind="messaging.reminder")
    if job.payload.get("appointment_id") in appointment_ids
]
rows.sort(key=lambda row: (row["appointment"], row["step"]))
__result = rows
`);
      expect(settled).toHaveLength(6);
      expect(settled.every((row) => row.status === "done")).toBe(true);
      for (const id of [appointment.id, unconsentedAppointment.id]) {
        expect(settled.filter((row) => row.appointment === id).map((row) => row.step).sort()).toEqual([
          "morning_of",
          "t_minus_2",
          "t_minus_7",
        ]);
      }
    } finally {
      // Restore the clinic's real quiet-hours policy even when an assertion
      // fails. A failed restore must not mask the real failure, but it is
      // reported so a poisoned demo fixture is never silent.
      try {
        runShell(`
from apps.clinics.models import Clinic

clinic = Clinic.objects.get(pk=${quotedClinic})
original = __json.loads(${JSON.stringify(JSON.stringify(originalMessaging))})
settings_json = dict(clinic.settings_json or {})
if original is None:
    settings_json.pop("messaging", None)
else:
    settings_json["messaging"] = original
clinic.settings_json = settings_json
clinic.save(update_fields=["settings_json"])
__result = True
`);
      } catch (error) {
        process.stdout.write(
          `warning: could not restore the clinic messaging policy: ${String(error)}\n`,
        );
      }
    }
  });
});
