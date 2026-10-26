import LegalPage, { LegalSection } from "@/pages/legal/LegalPage";

export default function PrivacyPage() {
  return (
    <LegalPage title="Privacy Protocol" updated="2026-10-19">
      <LegalSection heading="What this deployment does">
        <p>
          Enamel is a multi-tenant clinic platform. Every request is resolved
          against the clinic the caller belongs to, and a request that names a
          foreign clinic or a foreign object is answered as if the object did
          not exist.
        </p>
        <p>
          Patient data is written to a tenant-scoped database, an append-only
          audit log records who changed what, and uploaded DICOM studies have
          the configured identifier tags removed before they are stored.
        </p>
      </LegalSection>

      <LegalSection heading="What this deployment does not claim">
        <p>
          This software is <strong>not</strong> certified, and no part of this
          repository claims HIPAA, DPDP, GDPR, PCI or any other compliance
          regime. The controls above are engineering controls, not a
          certification, and a deployment remains responsible for its own
          hosting, contracts, key management, retention policy and legal
          basis.
        </p>
        <p>
          It also makes no clinical claim. The imaging analysis is a
          fixture-only evaluation and is not clinically validated.
        </p>
      </LegalSection>

      <LegalSection heading="Data that leaves the deployment">
        <p>
          The default configuration runs entirely offline: the AI provider is a
          deterministic mock, the payment gateway is a sandbox, the messaging
          adapter is a development adapter and the claims module is disabled.
          None of them opens a network connection until an operator supplies
          live credentials and switches the relevant mode on.
        </p>
        <p>
          When a live provider is configured, the request payload and any
          prompt text travel to that provider. The invocation log records
          counts, purposes and latencies; it does not store prompt or patient
          bodies.
        </p>
      </LegalSection>

      <LegalSection heading="Retention and deletion">
        <p>
          Retention defaults are configuration values, not guarantees. Audit
          rows, idempotency records and finished job rows are purged by
          scheduled maintenance; clinical records follow the retention policy
          the clinic configures. Ask the operator of this deployment for the
          values actually in force.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
