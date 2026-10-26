# Interop app (T130) - FHIR facade and Open Dental connector

Read-only FHIR R4 facade over the canonical model plus the first PMS connector
(Open Dental) with a deterministic simulator, durable sync runs, idempotent
external-id mapping and conflict reporting.

## Mapping table (internal -> FHIR R4)

| Internal | FHIR resource | Notes |
|---|---|---|
| `patients.Patient` | `Patient` | `identifier` under `urn:enamel:patient` = `Patient.number`; `name`, `birthDate`, `gender` (intersex -> `other`), `telecom`, `address` |
| `scheduling.Appointment` | `Appointment` | `identifier` `urn:enamel:appointment`; status map below; `participant` patient + `Practitioner/{provider}`; `serviceType` = appointment type |
| `patients.Encounter` | `Encounter` | `identifier` `urn:enamel:encounter`; `class` = `AMB` (v3-ActCode); `subject` = `Patient/{id}`; `period.start` = `occurred_at` |

Clinical free text (`Appointment.notes`, `Encounter.notes`) is **not** projected.

Appointment status map: `requested->proposed`, `held->pending`,
`scheduled|confirmed->booked`, `checked_in->checked-in`, `in_progress->arrived`,
`completed->fulfilled`, `cancelled->cancelled`, `no_show->noshow`.

Encounter status map: `draft->planned`, `finalized|amended->finished`.

## Validation

Resources are validated with the published `fhir.resources` pydantic models
(`fhir.resources.R4B.*`) in `apps/interop/fhir/validate.py`. A projection that
does not validate raises `FhirValidationError` (500) rather than emitting a
malformed resource.

**Documented deviation.** `fhir.resources` 6.x ships the exact R4 modules but
requires pydantic v1; this project pins pydantic 2.14. `fhir.resources` 7.x
requires pydantic 2 but its *default* modules are R5. The facade therefore
validates with the `R4B` subpackage (the R4 errata line, which keeps R4 resource
shapes such as a single `Encounter.class` Coding). This is real validation
against published models; no hand-rolled schema check is claimed as validation.

## API

| Endpoint | Permission | Notes |
|---|---|---|
| `GET /api/v1/fhir/Patient/{id}/` | `analytics.view` | `application/fhir+json`; cross-clinic 404 |
| `GET /api/v1/fhir/Appointment/{id}/` | `analytics.view` | read-only |
| `GET /api/v1/fhir/Encounter/{id}/` | `analytics.view` | read-only |
| `GET|POST /api/v1/connectors/` | `integrations.manage` | credentials write-only, never serialized back |
| `POST /api/v1/connectors/{id}/sync/` | `integrations.manage` | response includes `mode` |
| `GET /api/v1/connectors/{id}/runs/` | `integrations.manage` | paginated |

**Permission decision (documented).** The task allowed
`integrations.manage` *or* `analytics.view`. This app splits by least privilege:
the read-only FHIR facade requires `analytics.view` (owner/dentist/receptionist),
while connector *configuration* requires the owner-only `integrations.manage`.

## Connector protocol

`pull_patients(since)`, `pull_appointments(since)`, `push_appointment(appointment)`,
`push_status(appointment, status)`.

* `mode=simulator` (default) -> `SimulatedOpenDental` from `settings_json`
  (seed data + fault plan). Deterministic; never opens a socket.
* `mode=live` -> requires stored credentials **and** `CONNECTOR_ALLOW_LIVE=true`,
  otherwise `409 connector_not_configured`.

Fault injection (`settings_json.faults`): `timeout` / `error_500` are transient
counters (retried); `partial_write` is permanent (the appointment pull fails after
the patient phase); `duplicate_patients` makes the pull emit each record twice.

## SSRF guard

`validate_base_url`: https only, hostname allowlist (`CONNECTOR_ALLOWED_HOSTS`),
and rejection of loopback/private/link-local/reserved/metadata addresses. DNS is
re-resolved on every call so a rebinding host cannot pass once and be reused.

## Credentials

Fernet-encrypted with a key derived from `CONNECTOR_KEY` (a deterministic key
from `SECRET_KEY` is used when it is unset in dev/test). The ciphertext is never
returned by a serializer; the API exposes only `has_credentials`.

## Sync guarantees

Idempotent (`ExternalIdMap` per `(connector, entity_type, external_id)`),
resumable (a partial/failed run stores a `cursor` and the next run resumes from
it), accurate counts (`counts_json` with created/skipped/conflicted tallies and
every retried error), and conflict-safe (a mismatched external id is queued in
`SyncConflict` and the mapping is never overwritten).
