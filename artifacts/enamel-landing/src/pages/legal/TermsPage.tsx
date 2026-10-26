import LegalPage, { LegalSection } from "@/pages/legal/LegalPage";

export default function TermsPage() {
  return (
    <LegalPage title="Terms of Service" updated="2026-10-19">
      <LegalSection heading="What this software is">
        <p>
          Enamel is software for running a dental clinic: records, scheduling,
          reminders, treatment plans, billing, a patient portal, a public
          booking page, imaging storage and assistive tooling. It is provided
          as-is, without a warranty of any kind.
        </p>
        <p>
          It is a tool, not a clinician. Nothing it produces is a diagnosis, and
          no output of this software may be used as the sole basis for a
          clinical decision.
        </p>
      </LegalSection>

      <LegalSection heading="No clinical validation, no clearance">
        <p>
          The imaging analysis shipped with this repository is a transparent
          intensity-threshold baseline evaluated on synthetic fixtures. It is
          not clinically validated, it is not a medical device, and it carries
          no regulatory clearance of any kind. The same applies to the machine
          learning models and to the AI assistant: they are assistive and
          require a qualified person to check their output.
        </p>
      </LegalSection>

      <LegalSection heading="Responsibilities of the operator">
        <p>
          The operator of a deployment is responsible for the legal basis on
          which data is processed, for the correctness of the data entered, for
          access control inside the clinic, for retention and deletion, for
          the configuration of every live integration, and for the supervision
          of anyone who uses the assistive outputs.
        </p>
        <p>
          The software must not be offered to patients as a diagnostic service,
          and the landing copy of any deployment must not claim clinical
          validation, endorsement by a third party, or a measured performance
          figure that the deployment has not itself measured.
        </p>
      </LegalSection>

      <LegalSection heading="Availability">
        <p>
          No service level is promised by this document. The reference
          deployment is a rehearsal stack, not a managed service.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
