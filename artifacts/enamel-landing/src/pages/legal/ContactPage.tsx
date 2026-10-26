import LegalPage, { LegalSection } from "@/pages/legal/LegalPage";

export default function ContactPage() {
  return (
    <LegalPage title="Contact" updated="2026-10-19">
      <LegalSection heading="About this page">
        <p>
          This build does not publish a contact address, and inventing one
          would be just as dishonest as the marketing claims this repository
          was corrected for. A deployment of Enamel should replace the content
          of this page with the support channel that the operator really
          offers, and keep it accurate.
        </p>
        <p>
          Until then, the fastest route to the people who run this deployment
          is the channel the clinic itself publishes to its patients.
        </p>
      </LegalSection>

      <LegalSection heading="What to include in a real support page">
        <ul className="list-disc pl-6 space-y-2">
          <li>the name of the organisation operating the deployment,</li>
          <li>a monitored support address or phone number,</li>
          <li>the data-protection contact for a privacy request, and</li>
          <li>
            the identity of the hosting provider and the country in which the
            data is stored.
          </li>
        </ul>
      </LegalSection>

      <LegalSection heading="Security reports">
        <p>
          A security issue in the software should be reported to the operator
          of the deployment. This repository is not a monitored security
          response process, and no response time is promised here.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
