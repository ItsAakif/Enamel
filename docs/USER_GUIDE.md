# Enamel user guide

This repository ships a complete clinic platform plus an offline-capable demo.
This guide explains each surface, the demo entrances, and - honestly - which
parts are real and which are sandboxes. The same content is served at `/guide`
in the application (`artifacts/enamel-landing/src/pages/GuidePage.tsx`).

## The surfaces, and what each one is for

| Surface | Where | What it is |
|---|---|---|
| Staff workspace | `/app` | The clinical workspace for your team: scheduling, patient records, the imaging archive, charting, billing, analytics, automations and settings. Needs a staff account - sign up at `/register` for a new clinic, sign in at `/login`. |
| Patient portal | `/portal` | What your patients use. There is no portal password: the patient enters their clinic and their registered email or mobile, requests a one-time code, and the code signs them in. They can read their own records, appointments, treatment plans, invoices and documents. |
| Public booking | `/book/:slug` | A shareable self-service booking page per clinic. Patients pick a bookable service, a provider and a free slot; the slot is claimed inside the same availability engine the staff calendar uses. |
| Platform admin | operator-only | A separately permissioned service for the operator of a deployment (Django admin on its own loopback port, reached through SSH or a VPN). It is deliberately not linked anywhere in the app and is not reachable through the public tier. |
| The API | `/api/v1` | Every surface above runs on one versioned API. The OpenAPI schema (`backend/openapi/schema.json`) and the generated typed clients (`lib/api-client-react`, `lib/api-zod`) are the contract; nothing else is documented twice. |

## The demo: entrances and demo credentials

The demo deployment is seeded once with two clinics (`enamel-central` and
`enamel-north`), the accounts below, and synthetic patients. All staff
passwords are demo-only; change them or remove the accounts before ever
hosting real data.

- `owner@enamel.demo` / `EnamelDemo!2026` - clinic owner of `enamel-central`
  (also a member of `enamel-north`, so you can try the clinic switcher)
- `dentist@enamel.demo` / `EnamelDemo!2026` - clinical role
- `reception@enamel.demo` / `EnamelDemo!2026` - front-desk role
- `assistant@enamel.demo` / `EnamelDemo!2026` - assistant role

Patient portal demo: clinic `enamel-central` and the contact
`patient@enamel.demo` (or `+919800000001`). The demo patient has a booked
appointment and an issued invoice so the portal has something real to show.

Public booking demo: `/book/enamel-central`.

## A walkthrough of each surface

### Staff workspace (start: `/app`)

1. Sign in at `/login` with a demo account. The dashboard shows this month's
   metrics and today's schedule.
2. Create a patient: the "New Patient" button on the dashboard opens the
   create form in Patients, or use the sidebar.
3. Scheduling: pick a provider, pick the appointment type, drag or click a
   free slot; confirm it with "New appointment".
4. Charting: open a patient record and record tooth findings on the
   odontogram. Findings are clinical observations written by a clinician, not
   machine results.
5. Imaging: upload a study in the imaging area of the workspace. The archive
   strips identifier tags and flags radiopaque regions as suggestions only -
   not a diagnostic tool.
6. Billing: raise an invoice from an accepted treatment plan and record a
   sandbox payment.

### Patient portal (start: `/portal/login`)

1. Enter the clinic: `enamel-central`.
2. Enter the contact on file: `patient@enamel.demo`.
3. Request a code. On a demo deployment the server runs with `DEMO_MODE` and
   shows the code in a labelled panel and pre-fills it; otherwise the code is
   delivered to that contact only.
4. Verify the code and read the appointments, records, plans, invoices and
   documents of that patient - and nothing else.

### Public booking (start: `/book/enamel-central`)

1. Pick a bookable service and a provider.
2. Choose any free slot in the next weeks; the same availability engine the
   staff calendar uses confirms it.
3. Leave the patient's name and mobile number - the record appears in the
   clinic's staff workspace as an online booking.

## What is real, and what is a sandbox

Real in this build:

- The scheduling engine: working hours, bookable slots, conflict detection,
  cancellations and rescheduling.
- Patient records, dental charting, treatment plans, consent templates,
  invoices and payments data - all tenant-scoped with row-level security and
  an audit log.
- Analytics computed live over your own seeded data.
- The public booking flow and the portal one-time-code flow end to end.

Sandbox or simulator only:

- Payments: the gateway adapter is a sandbox - no real money moves and no
  card network is contacted.
- Messaging: the development adapter writes outgoing SMS/email/WhatsApp to
  logs instead of a live provider.
- E-signature: consent signatures are recorded in the database; there is no
  qualified e-signature provider.
- PMS connector: the adapter is a mock; no external practice-management
  system is contacted.
- AI provider: the assistant runs on the built-in mock provider; no external
  AI service is called.
- One-time-code delivery: the demo adapter logs the code and the demo
  response shows it; a production deployment would deliver it to the patient
  contact.

Not claimed anywhere in this repository: no clinical validation, no
regulatory clearance and no compliance certification. The imaging model is a
transparent intensity threshold evaluated on fixtures; the flagship claim of
this repository is engineering scope, not medicine.

## The portal one-time code: demo vs production

The portal has no password. Each sign-in resolves a fresh six-digit code that
expires in ten minutes and tolerates five wrong attempts. The code is hashed
at rest; the contact that requested it is hashed too, and the clinic plus the
registered contact are the only way to resolve a patient.

**Demo mode**: a deployment that runs with `DEMO_MODE` (the hosted demo
overlay) includes the generated code in the request-code response under
`demo_code` and shows it in a labelled panel, so the walkthrough works without
a mail or SMS provider.

**Production mode**: the production settings refuse to start with `DEMO_MODE`
enabled. Nothing is echoed; the code reaches the patient contact only,
through the delivery channels a real deployment configures.

## Want this for your business?

Enamel is a complete clinic platform: scheduling, patient records, charting,
the imaging archive, treatment plans, billing, the patient portal and
analytics. If you tried the demo and would like it tailored to your practice,
or want a walkthrough before you decide, get in touch:

**https://aakifmudel.xyz/contact**

Never enable `DEMO_MODE` on an instance that stores real patient data.
