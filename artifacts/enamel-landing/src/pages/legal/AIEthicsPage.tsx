import LegalPage, { LegalSection } from "@/pages/legal/LegalPage";

export default function AIEthicsPage() {
  return (
    <LegalPage title="AI Ethics" updated="2026-10-19">
      <LegalSection heading="What the AI in this product is allowed to do">
        <p>
          The assistant answers from a clinic's own knowledge base, with the
          clinic filter applied inside the database query, so another clinic's
          document can never be a candidate. It refuses, with a fixed message,
          when the retrieved material does not support an answer.
        </p>
        <p>
          The voice assistant may book, reschedule and cancel an appointment.
          It must identify the caller and it must obtain an explicit
          confirmation before any consequential tool runs. An unrecognised
          instruction inside a document or a caller utterance is treated as
          data and refused.
        </p>
      </LegalSection>

      <LegalSection heading="What the AI is never allowed to do">
        <p>
          It may not write to the clinical chart without a clinician acting on
          the finding, and it may not make a diagnosis. The imaging pipeline
          records its findings as suggestions; nothing it produces is a
          diagnostic result.
        </p>
        <p>
          The assistant never invents patient data, and it never answers from
          outside the retrieved clinic material.
        </p>
      </LegalSection>

      <LegalSection heading="Claims this product does not make">
        <p>
          The imaging model is a transparent intensity threshold evaluated on
          synthetic fixtures. This is a fixture-only evaluation. It is not
          clinically validated, and this product claims no clinical accuracy,
          no diagnostic performance and no regulatory clearance.
        </p>
        <p>
          No accuracy, speed or savings figure is published on this site
          unless this repository contains the measurement that produced it.
          The bundled evaluation is the only measurement, and it is a
          fixture-only one.
        </p>
        <p>
          No clinic, vendor, university or institution is named, implied or
          endorsed here.
        </p>
      </LegalSection>

      <LegalSection heading="Human oversight">
        <p>
          Every assistive output is advisory. A qualified clinician remains
          responsible for the record, for the judgement that follows from it,
          and for the treatment. Where a workflow would change a confirmed
          appointment, a staff member approves the change first.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
