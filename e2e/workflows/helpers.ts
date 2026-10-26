import { expect, type APIResponse, type BrowserContext, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * Staff API helpers shared by the workflow specs.
 *
 * Everything here talks to the same HTTP surface the SPA uses: session cookie,
 * `X-Clinic-Id` tenant scope and `X-CSRFToken`. No test-only endpoints and no
 * direct database writes are involved except the explicit reminder harness
 * (`runManageCommand`) which needs to force a scheduled job due because the
 * product deliberately has no "run job now" API.
 */

export const ORIGIN = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173";
export const API_CONTAINER = process.env.E2E_API_CONTAINER ?? "enamel-api-1";

export const DEMO_CLINIC_SLUG = "enamel-central";
export const DEMO_CLINIC_NAME = "Enamel Central";

export interface ClinicSummary {
  id: string;
  name: string;
  slug: string;
}

export interface Lookup {
  id: string;
  [key: string]: unknown;
}

export interface ProviderRecord {
  id: string;
  display_name: string;
  is_bookable: boolean;
}

export interface PatientRecord {
  id: string;
  first_name: string;
  last_name: string;
  full_name: string;
  email: string;
  phone_e164: string;
  date_of_birth: string | null;
}

export interface AppointmentRecord {
  id: string;
  patient: string;
  provider: string;
  appointment_type: string;
  location: string;
  operatory: string | null;
  start_at: string;
  end_at: string;
  status: string;
  source: string;
  reason: string;
  rescheduled_from: string | null;
  cancellation_reason: string;
}

export interface AvailabilitySlot {
  start: string;
  end: string;
  provider: string | null;
  provider_name: string | null;
  operatory: string | null;
  operatory_name: string | null;
  location: string | null;
  reason: string;
}

const pad = (value: number): string => String(value).padStart(2, "0");

// --------------------------------------------------------------------------- //
// Time (Asia/Kolkata, fixed +05:30)
// --------------------------------------------------------------------------- //

const KOLKATA_DATE = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Kolkata",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const KOLKATA_LOCAL = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Kolkata",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const KOLKATA_WEEKDAY = new Intl.DateTimeFormat("en-US", {
  timeZone: "Asia/Kolkata",
  weekday: "short",
});

/** `YYYY-MM-DD` for `daysFromToday` calendar days ahead in clinic time. */
export function kolkataDate(daysFromToday = 0): string {
  const parts = KOLKATA_DATE.formatToParts(
    new Date(Date.now() + daysFromToday * 24 * 60 * 60 * 1000),
  );
  const value = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return `${value("year")}-${value("month")}-${value("day")}`;
}

/** An ISO instant for a clinic wall-clock time. */
export function kolkataIso(day: string, hour: number, minute = 0): string {
  return `${day}T${pad(hour)}:${pad(minute)}:00+05:30`;
}

/** The `datetime-local` value (`YYYY-MM-DDTHH:mm`) for an ISO instant. */
export function toKolkataLocalInput(value: string | Date): string {
  const date = typeof value === "string" ? new Date(value) : value;
  return KOLKATA_LOCAL.format(date).replace(" ", "T").slice(0, 16);
}

function kolkataWeekday(day: string): string {
  return KOLKATA_WEEKDAY.format(new Date(`${day}T12:00:00+05:30`));
}

/** The next Mon-Fri date at least `minDaysAhead` days away. */
export function nextBusinessDay(minDaysAhead = 1): string {
  let offset = minDaysAhead;
  for (let guard = 0; guard < 10; guard += 1) {
    const day = kolkataDate(offset);
    if (!["Sat", "Sun"].includes(kolkataWeekday(day))) return day;
    offset += 1;
  }
  return kolkataDate(minDaysAhead);
}

// --------------------------------------------------------------------------- //
// Unique records
// --------------------------------------------------------------------------- //

let sequence = 0;

function nextSequence(): number {
  sequence += 1;
  return sequence;
}

/**
 * A last name that sorts before every seeded and previously generated patient.
 *
 * Patient pickers take the first 100 rows ordered by `last_name`, so a
 * decreasing timestamp keeps the newest fixture visible without changing the
 * production ordering.
 */
export function freshLastName(): string {
  const decreasing = 10_000_000_000_000 - Date.now();
  return `0${decreasing}${pad(nextSequence() % 100)}`;
}

export function uniqueEmail(prefix: string): string {
  return `${prefix}.${Date.now()}.${Math.floor(Math.random() * 10_000)}@e2e.enamel.demo`;
}

export function uniquePhone(): string {
  const digits = String(10_000_000 - (Date.now() % 10_000_000)).padStart(8, "0");
  return `+9198${digits}`;
}

// --------------------------------------------------------------------------- //
// HTTP plumbing
// --------------------------------------------------------------------------- //

export async function ensureCsrf(context: BrowserContext): Promise<void> {
  await context.request.get(`${ORIGIN}/api/v1/auth/csrf/`);
}

async function csrfToken(context: BrowserContext): Promise<string | null> {
  const cookies = await context.cookies();
  return cookies.find((cookie) => cookie.name === "csrftoken")?.value ?? null;
}

export async function staffHeaders(
  context: BrowserContext,
  clinicId: string,
): Promise<Record<string, string>> {
  const csrf = await csrfToken(context);
  return {
    "X-Clinic-Id": clinicId,
    ...(csrf ? { "X-CSRFToken": csrf } : {}),
  };
}

export async function expectOk(response: APIResponse, label: string): Promise<unknown> {
  if (!response.ok()) {
    throw new Error(`${label} failed: ${response.status()} ${await response.text()}`);
  }
  const text = await response.text();
  return text ? (JSON.parse(text) as unknown) : null;
}

function unwrap<T>(body: unknown): T[] {
  if (Array.isArray(body)) return body as T[];
  const envelope = body as { results?: T[] } | null;
  return envelope?.results ?? [];
}

export async function apiGet<T>(
  context: BrowserContext,
  clinicId: string,
  path: string,
): Promise<T> {
  await ensureCsrf(context);
  const response = await context.request.get(`${ORIGIN}${path}`, {
    headers: { "X-Clinic-Id": clinicId },
  });
  return (await expectOk(response, `GET ${path}`)) as T;
}

export async function apiList<T>(
  context: BrowserContext,
  clinicId: string,
  path: string,
): Promise<T[]> {
  return unwrap<T>(await apiGet<unknown>(context, clinicId, path));
}

export async function apiPost<T>(
  context: BrowserContext,
  clinicId: string,
  path: string,
  data: unknown,
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  await ensureCsrf(context);
  const response = await context.request.post(`${ORIGIN}${path}`, {
    headers: { ...(await staffHeaders(context, clinicId)), ...extraHeaders },
    data,
  });
  return (await expectOk(response, `POST ${path}`)) as T;
}

/** A GET that leaves status/headers/body inspection to the caller. */
export async function apiGetRaw(
  context: BrowserContext,
  clinicId: string,
  path: string,
): Promise<APIResponse> {
  await ensureCsrf(context);
  return context.request.get(`${ORIGIN}${path}`, {
    headers: { "X-Clinic-Id": clinicId },
  });
}

/** Fetch a PDF (or other binary) endpoint and assert it is a real document. */
export async function apiDownloadPdf(
  context: BrowserContext,
  clinicId: string,
  path: string,
): Promise<{ body: Buffer; sha256: string | null }> {
  const response = await apiGetRaw(context, clinicId, path);
  if (!response.ok()) {
    throw new Error(`GET ${path} failed: ${response.status()} ${await response.text()}`);
  }
  expect(response.headers()["content-type"], `${path} must be a PDF`).toContain(
    "application/pdf",
  );
  const body = await response.body();
  expect(body.subarray(0, 5).toString("latin1"), `${path} must start with %PDF-`).toBe(
    "%PDF-",
  );
  return { body, sha256: response.headers()["x-document-sha256"] ?? null };
}

export async function apiPatch<T>(
  context: BrowserContext,
  clinicId: string,
  path: string,
  data: unknown,
): Promise<T> {
  await ensureCsrf(context);
  const response = await context.request.patch(`${ORIGIN}${path}`, {
    headers: await staffHeaders(context, clinicId),
    data,
  });
  return (await expectOk(response, `PATCH ${path}`)) as T;
}

export async function rawPost(
  context: BrowserContext,
  clinicId: string,
  path: string,
  data: unknown,
): Promise<APIResponse> {
  await ensureCsrf(context);
  return context.request.post(`${ORIGIN}${path}`, {
    headers: await staffHeaders(context, clinicId),
    data,
  });
}

// --------------------------------------------------------------------------- //
// Lookups
// --------------------------------------------------------------------------- //

export async function getClinics(context: BrowserContext): Promise<ClinicSummary[]> {
  return apiList<ClinicSummary>(context, "", "/api/v1/clinics/");
}

export async function getClinicBySlug(
  context: BrowserContext,
  slug: string,
): Promise<ClinicSummary> {
  const clinics = await getClinics(context);
  const clinic = clinics.find((item) => item.slug === slug);
  expect(clinic, `expected the seeded clinic '${slug}'`).toBeTruthy();
  return clinic as ClinicSummary;
}

export async function getProviders(
  context: BrowserContext,
  clinicId: string,
): Promise<ProviderRecord[]> {
  return apiList<ProviderRecord>(context, clinicId, "/api/v1/providers/?page_size=100");
}

export async function getAppointmentTypes(
  context: BrowserContext,
  clinicId: string,
): Promise<Lookup[]> {
  return apiList<Lookup>(context, clinicId, "/api/v1/appointment-types/?page_size=100");
}

export async function getLocations(
  context: BrowserContext,
  clinicId: string,
): Promise<Lookup[]> {
  return apiList<Lookup>(context, clinicId, `/api/v1/clinics/${clinicId}/locations/`);
}

export async function getOperatories(
  context: BrowserContext,
  clinicId: string,
): Promise<Lookup[]> {
  return apiList<Lookup>(context, clinicId, "/api/v1/operatories/?page_size=100");
}

// --------------------------------------------------------------------------- //
// Patients
// --------------------------------------------------------------------------- //

export interface PatientSeed {
  firstName: string;
  lastName: string;
  email: string;
  phone: string;
  dateOfBirth: string;
}

export function patientSeed(firstName = "E2E"): PatientSeed {
  return {
    firstName,
    lastName: freshLastName(),
    email: uniqueEmail("workflow"),
    phone: uniquePhone(),
    dateOfBirth: "1988-04-12",
  };
}

export async function createPatient(
  context: BrowserContext,
  clinicId: string,
  seed: PatientSeed = patientSeed(),
): Promise<PatientRecord> {
  const created = await apiPost<PatientRecord>(context, clinicId, "/api/v1/patients/", {
    first_name: seed.firstName,
    last_name: seed.lastName,
    preferred_name: seed.firstName,
    email: seed.email,
    phone: seed.phone,
    date_of_birth: seed.dateOfBirth,
    sex_at_birth: "unknown",
    preferred_contact: "email",
    source: "staff",
  });
  return created;
}

export async function getPatient(
  context: BrowserContext,
  clinicId: string,
  patientId: string,
): Promise<PatientRecord> {
  return apiGet<PatientRecord>(context, clinicId, `/api/v1/patients/${patientId}/`);
}

// --------------------------------------------------------------------------- //
// Scheduling
// --------------------------------------------------------------------------- //

export interface AppointmentSeedOptions {
  startAt: string;
  providerId?: string;
  appointmentTypeId?: string;
  locationId?: string;
  operatoryId?: string | null;
  reason?: string;
  source?: string;
}

export async function createAppointment(
  context: BrowserContext,
  clinicId: string,
  patientId: string,
  options: AppointmentSeedOptions,
): Promise<AppointmentRecord> {
  const [providers, types, locations] = await Promise.all([
    getProviders(context, clinicId),
    getAppointmentTypes(context, clinicId),
    getLocations(context, clinicId),
  ]);
  expect(providers.length, "expected a seeded provider").toBeGreaterThan(0);
  expect(types.length, "expected a seeded appointment type").toBeGreaterThan(0);
  expect(locations.length, "expected a seeded location").toBeGreaterThan(0);
  const providerId = options.providerId ?? providers[0].id;
  const body = {
    patient: patientId,
    provider: providerId,
    appointment_type: options.appointmentTypeId ?? types[0].id,
    location: options.locationId ?? locations[0].id,
    operatory: options.operatoryId ?? undefined,
    start_at: options.startAt,
    reason: options.reason ?? "e2e workflow",
    source: options.source ?? "staff",
  };
  return apiPost<AppointmentRecord>(context, clinicId, "/api/v1/appointments/", body);
}

export async function getAppointment(
  context: BrowserContext,
  clinicId: string,
  appointmentId: string,
): Promise<AppointmentRecord> {
  return apiGet<AppointmentRecord>(
    context,
    clinicId,
    `/api/v1/appointments/${appointmentId}/`,
  );
}

export async function findAppointments(
  context: BrowserContext,
  clinicId: string,
  patientId: string,
): Promise<AppointmentRecord[]> {
  return apiList<AppointmentRecord>(
    context,
    clinicId,
    `/api/v1/appointments/?patient=${patientId}&page_size=100&ordering=-start_at`,
  );
}

export async function getAvailability(
  context: BrowserContext,
  clinicId: string,
  appointmentTypeId: string,
  from: string,
  to: string,
): Promise<{ window: Record<string, unknown>; slots: AvailabilitySlot[] }> {
  return apiGet(
    context,
    clinicId,
    `/api/v1/availability/?appointment_type=${appointmentTypeId}&from=${from}&to=${to}`,
  );
}

/**
 * The first genuinely bookable slot at least two days out.
 *
 * Availability already excludes occupied provider/operatory lanes, so using a
 * returned slot cannot collide with a previous run. `providerId` narrows the
 * result to one lane - a reschedule keeps the original provider, so the target
 * slot has to be free for *that* provider.
 */
export async function firstOpenSlot(
  context: BrowserContext,
  clinicId: string,
  options: {
    appointmentTypeId?: string;
    minDaysAhead?: number;
    windowDays?: number;
    providerId?: string;
  } = {},
): Promise<AvailabilitySlot> {
  const types = await getAppointmentTypes(context, clinicId);
  expect(types.length, "expected a seeded appointment type").toBeGreaterThan(0);
  const typeId = options.appointmentTypeId ?? types[0].id;
  const from = kolkataDate(options.minDaysAhead ?? 3);
  const to = kolkataDate((options.minDaysAhead ?? 3) + (options.windowDays ?? 14));
  const availability = await getAvailability(context, clinicId, typeId, from, to);
  const slots = availability.slots.filter(
    (slot) =>
      slot.provider !== null &&
      slot.reason === "available" &&
      (options.providerId === undefined || slot.provider === options.providerId),
  );
  expect(
    slots.length,
    options.providerId
      ? "expected at least one open slot for the requested provider"
      : "expected at least one open availability slot",
  ).toBeGreaterThan(0);
  return slots[0];
}

/**
 * The first start time that *every* bookable provider can take.
 *
 * The AI tool registry books with the clinic's default provider and the
 * harness cannot name it reliably (the backend orders by display name and id),
 * so the slot is chosen so that any provider is free at that instant.
 */
export async function firstOpenSlotForEveryProvider(
  context: BrowserContext,
  clinicId: string,
  options: { appointmentTypeId?: string; minDaysAhead?: number; windowDays?: number } = {},
): Promise<AvailabilitySlot> {
  const types = await getAppointmentTypes(context, clinicId);
  expect(types.length, "expected a seeded appointment type").toBeGreaterThan(0);
  const typeId = options.appointmentTypeId ?? types[0].id;
  const from = kolkataDate(options.minDaysAhead ?? 3);
  const to = kolkataDate((options.minDaysAhead ?? 3) + (options.windowDays ?? 14));
  const availability = await getAvailability(context, clinicId, typeId, from, to);

  const providers = (await getProviders(context, clinicId)).filter(
    (provider) => provider.is_bookable !== false,
  );
  expect(providers.length, "expected a bookable provider").toBeGreaterThan(0);
  const wanted = new Set(providers.map((provider) => provider.id));

  const byStart = new Map<string, AvailabilitySlot[]>();
  for (const slot of availability.slots) {
    if (slot.provider === null || slot.reason !== "available") continue;
    // The tool books one provider *and* one location, so the slot is keyed by
    // both: a start time only counts when the same location is free for every
    // provider.
    const key = `${slot.start}|${slot.location ?? ""}`;
    const bucket = byStart.get(key) ?? [];
    bucket.push(slot);
    byStart.set(key, bucket);
  }

  for (const [, slots] of byStart) {
    const free = new Set(slots.map((slot) => slot.provider as string));
    if ([...wanted].every((id) => free.has(id))) return slots[0];
  }
  throw new Error("expected at least one start time that every provider can take");
}

export async function rescheduleAppointment(
  context: BrowserContext,
  clinicId: string,
  appointmentId: string,
  startAt: string,
): Promise<AppointmentRecord> {
  return apiPost<AppointmentRecord>(
    context,
    clinicId,
    `/api/v1/appointments/${appointmentId}/reschedule/`,
    { start_at: startAt, reason: "e2e reschedule" },
  );
}

export async function cancelAppointment(
  context: BrowserContext,
  clinicId: string,
  appointmentId: string,
  reason: string,
): Promise<AppointmentRecord> {
  return apiPost<AppointmentRecord>(
    context,
    clinicId,
    `/api/v1/appointments/${appointmentId}/cancel/`,
    { reason },
  );
}

// --------------------------------------------------------------------------- //
// Plans, consents and billing
// --------------------------------------------------------------------------- //

export interface PlanRecord {
  id: string;
  patient: string;
  title: string;
  status: string;
  total_minor: number;
  currency: string;
  items: Array<{ id: string; status: string; procedure_code: string; line_total_minor: number }>;
}

export async function createPlan(
  context: BrowserContext,
  clinicId: string,
  patientId: string,
  title: string,
  unitPriceMinor = 250_000,
): Promise<PlanRecord> {
  return apiPost<PlanRecord>(context, clinicId, "/api/v1/treatment-plans/", {
    patient: patientId,
    title,
    currency: "INR",
    notes: "e2e plan",
    items: [
      {
        procedure_code: "D2740",
        description: "E2E ceramic crown",
        tooth: 16,
        quantity: 1,
        unit_price_minor: unitPriceMinor,
        tax_rate_bp: 0,
        status: "accepted",
      },
    ],
  });
}

export async function planAction(
  context: BrowserContext,
  clinicId: string,
  planId: string,
  action: "present" | "accept" | "decline",
): Promise<PlanRecord> {
  return apiPost<PlanRecord>(
    context,
    clinicId,
    `/api/v1/treatment-plans/${planId}/${action}/`,
    {},
  );
}

export async function getAcceptedPlan(
  context: BrowserContext,
  clinicId: string,
  patientId: string,
  title: string,
): Promise<PlanRecord> {
  const plan = await createPlan(context, clinicId, patientId, title);
  await planAction(context, clinicId, plan.id, "present");
  return planAction(context, clinicId, plan.id, "accept");
}

export async function getPlan(
  context: BrowserContext,
  clinicId: string,
  planId: string,
): Promise<PlanRecord> {
  return apiGet<PlanRecord>(context, clinicId, `/api/v1/treatment-plans/${planId}/`);
}

export async function listPlans(
  context: BrowserContext,
  clinicId: string,
  patientId: string,
): Promise<PlanRecord[]> {
  return apiList<PlanRecord>(
    context,
    clinicId,
    `/api/v1/treatment-plans/?patient=${patientId}&page_size=100`,
  );
}

export async function getConsentTemplate(
  context: BrowserContext,
  clinicId: string,
  key = "treatment_consent",
): Promise<Lookup> {
  const templates = await apiList<Lookup>(
    context,
    clinicId,
    "/api/v1/consent-templates/?page_size=100",
  );
  const template =
    templates.find((item) => item.key === key && item.is_active !== false) ?? templates[0];
  expect(template, "expected a seeded consent template").toBeTruthy();
  return template as Lookup;
}

export interface ConsentRecord {
  id: string;
  patient: string;
  template_version: number;
  signer_name: string;
  relationship: string;
  signed_at: string;
  status: string;
  document_hash: string;
  legal_notice: string;
  is_legally_compliant: boolean;
}

export async function createConsentRecord(
  context: BrowserContext,
  clinicId: string,
  patientId: string,
  signerName: string,
): Promise<ConsentRecord> {
  const template = await getConsentTemplate(context, clinicId);
  return apiPost<ConsentRecord>(context, clinicId, "/api/v1/consent-records/", {
    patient: patientId,
    template: template.id,
    signer_name: signerName,
    relationship: "self",
    // Only external_esign may claim legal compliance; typed/drawn marks set
    // is_legally_compliant=false by design.
    signature_type: "external_esign",
  });
}

export interface InvoiceRecord {
  id: string;
  patient: string;
  plan: string | null;
  number: string;
  status: string;
  currency: string;
  total_minor: number;
  paid_minor: number;
  balance_minor: number;
  lines: Array<{ id: string; description: string; line_total_minor: number }>;
}

export async function invoiceFromPlan(
  context: BrowserContext,
  clinicId: string,
  planId: string,
): Promise<InvoiceRecord> {
  return apiPost<InvoiceRecord>(
    context,
    clinicId,
    `/api/v1/invoices/from-plan/${planId}/`,
    {},
  );
}

export async function getInvoice(
  context: BrowserContext,
  clinicId: string,
  invoiceId: string,
): Promise<InvoiceRecord> {
  return apiGet<InvoiceRecord>(context, clinicId, `/api/v1/invoices/${invoiceId}/`);
}

export async function listInvoices(
  context: BrowserContext,
  clinicId: string,
  patientId: string,
): Promise<InvoiceRecord[]> {
  return apiList<InvoiceRecord>(
    context,
    clinicId,
    `/api/v1/invoices/?patient=${patientId}&page_size=100`,
  );
}

export async function recordPayment(
  context: BrowserContext,
  clinicId: string,
  invoiceId: string,
  amountMinor: number,
  method = "cash",
): Promise<{
  id: string;
  amount_minor: number;
  status: string;
  receipt_number: string;
}> {
  return apiPost(
    context,
    clinicId,
    `/api/v1/invoices/${invoiceId}/payments/`,
    { amount_minor: amountMinor, method },
    { "Idempotency-Key": `e2e-${Date.now()}-${Math.floor(Math.random() * 100_000)}` },
  );
}

// --------------------------------------------------------------------------- //
// Messaging
// --------------------------------------------------------------------------- //

export interface MessageRecord {
  id: string;
  patient: string | null;
  channel: string;
  purpose: string;
  status: string;
  reason: string;
  template: string | null;
  template_version: number;
}

export interface MessageTemplateRecord {
  id: string;
  key: string;
  channel: string;
  purpose: string;
  is_active: boolean;
}

export async function getMessageTemplates(
  context: BrowserContext,
  clinicId: string,
): Promise<MessageTemplateRecord[]> {
  return apiList<MessageTemplateRecord>(
    context,
    clinicId,
    "/api/v1/message-templates/?page_size=100",
  );
}

export async function grantMessagingConsent(
  context: BrowserContext,
  clinicId: string,
  patientId: string,
  channel: string,
  purpose: string,
): Promise<void> {
  await apiPost(context, clinicId, "/api/v1/consents/", {
    patient: patientId,
    channel,
    purpose,
    source: "e2e",
    wording_version: "e2e-1",
  });
}

export async function getMessages(
  context: BrowserContext,
  clinicId: string,
  patientId: string,
): Promise<MessageRecord[]> {
  return apiList<MessageRecord>(
    context,
    clinicId,
    `/api/v1/messages/?patient=${patientId}&page_size=100`,
  );
}

// --------------------------------------------------------------------------- //
// Compose-only harness hooks
// --------------------------------------------------------------------------- //

/**
 * The owner URL the harness uses for its direct database inspection.
 *
 * The owner credential lives in the **migration path only**: the runtime
 * containers do not carry it in their environment (ADR-013, the F-04
 * finding). The harness is an *operator* tool, so it reads the owner URL on
 * the host - `E2E_OWNER_DATABASE_URL`, the `DATABASE_MIGRATION_URL` of the
 * environment or of `deploy/.env` - and hands it to the one-off command it
 * runs inside the container. The container's own environment never holds it.
 */
function ownerDatabaseUrl(): string {
  const explicit = process.env.E2E_OWNER_DATABASE_URL?.trim();
  if (explicit) {
    return explicit;
  }
  const fromEnvironment = process.env.DATABASE_MIGRATION_URL?.trim();
  if (fromEnvironment) {
    return fromEnvironment;
  }
  const envFile = resolve(process.cwd(), "deploy", ".env");
  try {
    const content = readFileSync(envFile, "utf8");
    for (const line of content.split(/\r?\n/)) {
      const match = /^\s*DATABASE_MIGRATION_URL\s*=\s*(.+?)\s*$/.exec(line);
      if (match) {
        return match[1].replace(/^["']|["']$/g, "");
      }
    }
  } catch {
    // Fall through to the error below: the harness must not guess a credential.
  }
  throw new Error(
    "the harness needs the owner URL for its direct database inspection: set " +
      "E2E_OWNER_DATABASE_URL (or DATABASE_MIGRATION_URL), or add " +
      "DATABASE_MIGRATION_URL to deploy/.env",
  );
}

/**
 * Run a command inside the compose API container and report *why* it failed.
 *
 * `execFileSync` only puts the exit status in the thrown error; the traceback
 * of a failing `manage.py shell` snippet lives in stderr and would otherwise be
 * invisible, so it is folded into the message.
 */
function execInContainer(args: string[], timeout: number): string {
  // The harness is an *operator* tool: it inspects and drives the database
  // directly, so it connects with the owner URL (the same one the migration
  // step uses). The product itself is exercised through the API, which runs
  // as the non-superuser runtime role with the row-level-security policies in
  // force. The credential is handed to the one-off command; the container's
  // own environment does not hold it.
  try {
    return execFileSync(
      "docker",
      ["exec", "-e", `DATABASE_URL=${ownerDatabaseUrl()}`, API_CONTAINER, ...args],
      {
        encoding: "utf8",
        timeout,
      },
    );
  } catch (error) {
    const failure = error as { stdout?: string; stderr?: string; message?: string };
    const details = [
      `command failed: docker exec ${API_CONTAINER} ${args.join(" ")}`,
      failure.stderr?.trim() ? `stderr:\n${failure.stderr.trim()}` : "",
      failure.stdout?.trim() ? `stdout:\n${failure.stdout.trim()}` : "",
    ]
      .filter(Boolean)
      .join("\n");
    throw new Error(`${details}\n${failure.message ?? String(error)}`);
  }
}

/**
 * Run a Django management command inside the compose API container.
 *
 * The reminder workflow has to force a job that the product schedules nine days
 * ahead. There is deliberately no API to "run this job now", so the test drives
 * the same durable-job code path (`core.jobs.run_job`) through the shell.
 */
export function runManageCommand(args: string[]): string {
  return execInContainer(["python", "manage.py", ...args], 120_000);
}

/** Run an arbitrary command inside the compose API container. */
export function runContainerCommand(args: string[]): string {
  return execInContainer(args, 300_000);
}

/** Run a Python snippet in `manage.py shell` and parse a JSON marker line. */
export function runShell<T>(code: string): T {
  const marker = "__E2E_RESULT__";
  // The snippet is passed as a single argv entry (never through a shell), so
  // newlines and quotes inside it survive verbatim. `json` is imported under an
  // alias because the snippet may use the plain name for its own purposes.
  const wrapped = [
    "import json as __json",
    code,
    `print("${marker}" + __json.dumps(__result))`,
  ].join("\n");
  const output = execInContainer(
    ["python", "manage.py", "shell", "-c", wrapped],
    120_000,
  );
  const line = output
    .split(/\r?\n/)
    .reverse()
    .find((item) => item.includes(marker));
  if (!line) throw new Error(`manage.py shell produced no result marker:\n${output}`);
  return JSON.parse(line.slice(line.indexOf(marker) + marker.length)) as T;
}

// --------------------------------------------------------------------------- //
// Browser helpers
// --------------------------------------------------------------------------- //

export async function selectPatientInPicker(
  page: Page,
  testId: string,
  patient: PatientRecord,
): Promise<void> {
  const picker = page.getByTestId(testId);
  await expect(picker).toBeVisible();
  await expect
    .poll(async () => picker.locator(`option[value="${patient.id}"]`).count())
    .toBeGreaterThan(0);
  await picker.selectOption(patient.id);
}

export async function expectToast(page: Page, title: string | RegExp): Promise<void> {
  await expect(page.getByText(title, { exact: typeof title === "string" }).first()).toBeVisible();
}
