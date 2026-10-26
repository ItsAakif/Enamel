import { Link } from "wouter";
import {
  Activity,
  ArrowUpRight,
  Building2,
  CalendarCheck,
  CheckCircle2,
  Code2,
  KeyRound,
  ShieldCheck,
  Users,
} from "lucide-react";

/**
 * The public user guide (/guide).
 *
 * It describes the surfaces of this repository exactly as they ship: what is
 * real, what is a sandbox and what the demo deployment reveals. The copy is
 * scanned by scripts/check_claims.py like every other public source, so it
 * makes no clinical, compliance or percentage claim.
 */

const SURFACES: { icon: typeof Users; title: string; where: string; body: string }[] = [
  {
    icon: Users,
    title: "Staff workspace",
    where: "/app",
    body: "The clinical workspace for your team: scheduling, patient records, the imaging archive, charting, billing, analytics, automations and settings. Needs a staff account - sign up at /register for a new clinic, sign in at /login.",
  },
  {
    icon: KeyRound,
    title: "Patient portal",
    where: "/portal",
    body: "What your patients use. There is no portal password: the patient enters their clinic and their registered email or mobile, requests a one-time code, and the code signs them in. They can read their own records, appointments, treatment plans, invoices and documents.",
  },
  {
    icon: CalendarCheck,
    title: "Public booking",
    where: "/book/:slug",
    body: "A shareable self-service booking page per clinic. Patients pick a bookable service, a provider and a free slot; the slot is claimed inside the same availability engine the staff calendar uses.",
  },
  {
    icon: Building2,
    title: "Platform admin",
    where: "operator-only",
    body: "A separately permissioned service for the operator of a deployment (Django admin on its own loopback port, reached through SSH or a VPN). It is deliberately not linked anywhere in the app and is not reachable through the public tier.",
  },
  {
    icon: Code2,
    title: "The API",
    where: "/api/v1",
    body: "Every surface above runs on one versioned API with a typed contract, so the staff workspace, the portal and public booking all read and write the same data model.",
  },
];

const REAL: string[] = [
  "The scheduling engine: working hours, bookable slots, conflict detection, cancellations and rescheduling",
  "Patient records, dental charting, treatment plans, consent templates, invoices and payments data - all tenant-scoped with row-level security and an audit log",
  "Analytics computed live over your own seeded data",
  "The public booking flow and the portal one-time-code flow end to end",
];

const SANDBOX: string[] = [
  "Payments: the gateway adapter is a sandbox - no real money moves and no card network is contacted",
  "Messaging: the development adapter writes outgoing SMS/email/WhatsApp to logs instead of a live provider",
  "E-signature: consent signatures are recorded in the database; there is no qualified e-signature provider",
  "PMS connector: the adapter is a mock; no external practice-management system is contacted",
  "AI provider: the assistant runs on the built-in mock provider; no external AI service is called",
  "One-time-code delivery: the demo adapter logs the code and the demo response shows it; a production deployment would deliver it to the patient contact",
];

function Section({
  title,
  eyebrow,
  children,
}: {
  title: string;
  eyebrow: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-6">
      <div>
        <span className="text-[10px] font-bold tracking-[0.2em] text-primary uppercase">{eyebrow}</span>
        <h2 className="text-3xl font-extrabold tracking-tighter text-foreground font-display mt-2">{title}</h2>
      </div>
      {children}
    </section>
  );
}

export default function GuidePage() {
  return (
    <div
      className="min-h-screen text-foreground"
      style={{ background: "linear-gradient(135deg, #f3f0ff 0%, #f9f6f5 40%, #f0f4ff 70%, #fdf4fb 100%)" }}
    >
      <div className="max-w-5xl mx-auto px-6 py-16 md:py-24 space-y-20">
        {/* --- header ------------------------------------------------------ */}
        <header className="space-y-6">
          <div className="flex items-center justify-between gap-4">
            <Link href="/" data-testid="guide-back-link" className="text-xs font-bold uppercase tracking-widest text-muted-foreground hover:text-foreground transition-colors">
              Back to the site
            </Link>
            <Link href="/app" className="text-xs font-bold uppercase tracking-widest text-primary hover:opacity-80 transition-opacity">
              Open the workspace
            </Link>
          </div>
          <div>
            <span className="text-2xl font-black uppercase tracking-tighter text-foreground font-display">ENAMEL</span>
            <h1 data-testid="guide-title" className="text-4xl md:text-5xl font-extrabold font-display tracking-tighter text-foreground mt-4 leading-none">
              How to use Enamel.
            </h1>
            <p className="text-lg text-muted-foreground mt-4 max-w-2xl leading-relaxed">
              Enamel is a complete clinic platform with an offline-capable patient demo. This
              page explains each surface, the demo entrances, and - honestly - which parts are
              real and which are sandboxes.
            </p>
          </div>
        </header>

        {/* --- surfaces ---------------------------------------------------- */}
        <Section eyebrow="The map" title="The surfaces, and what each one is for.">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {SURFACES.map((surface) => {
              const Icon = surface.icon;
              return (
                <div key={surface.title} className="glass-card rounded-[1.5rem] p-7 shadow-sm">
                  <div className="flex items-center gap-3 mb-4">
                    <div className="w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center shrink-0">
                      <Icon size={18} className="text-primary" />
                    </div>
                    <h3 className="font-bold text-lg font-display">{surface.title}</h3>
                  </div>
                  <p className="text-[11px] font-bold uppercase tracking-widest text-primary mb-3">{surface.where}</p>
                  <p className="text-sm text-muted-foreground leading-relaxed">{surface.body}</p>
                </div>
              );
            })}
          </div>
        </Section>

        {/* --- walkthrough -------------------------------------------------- */}
        <Section eyebrow="Five minutes" title="A walkthrough of each surface.">
          <div className="space-y-6">
            <div className="glass-card rounded-[1.5rem] p-7 shadow-sm space-y-3">
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 rounded-xl bg-primary/10 flex items-center justify-center shrink-0"><Users size={16} className="text-primary" /></div>
                <h3 className="font-bold font-display">Staff workspace (start: /app)</h3>
              </div>
              <ol className="space-y-2 text-sm text-muted-foreground leading-relaxed list-decimal list-inside">
                <li>Sign in at /login with a demo account. The dashboard shows this month's metrics and today's schedule.</li>
                <li>Create a patient: the "New Patient" button on the dashboard opens the create form in Patients, or use the sidebar.</li>
                <li>Scheduling: pick a provider, pick the appointment type, drag or click a free slot; confirm it with "New appointment".</li>
                <li>
                  Charting: open a patient record and record tooth findings on the odontogram. Findings are clinical
                  observations written by a clinician, not machine results.
                </li>
                <li>
                  Imaging: upload a study in the imaging area of the workspace. The archive strips identifier
                  tags and flags radiopaque regions as suggestions only - not a diagnostic tool.
                </li>
                <li>Billing: raise an invoice from an accepted treatment plan and record a sandbox payment.</li>
              </ol>
            </div>

            <div className="glass-card rounded-[1.5rem] p-7 shadow-sm space-y-3">
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 rounded-xl bg-primary/10 flex items-center justify-center shrink-0"><KeyRound size={16} className="text-primary" /></div>
                <h3 className="font-bold font-display">Patient portal (start: /portal/login)</h3>
              </div>
              <ol className="space-y-2 text-sm text-muted-foreground leading-relaxed list-decimal list-inside">
                <li>Enter the clinic: <code className="font-mono font-bold">enamel-central</code>.</li>
                <li>Enter the contact on file: <code className="font-mono font-bold">patient@enamel.demo</code>.</li>
                <li>Request a code. On a demo deployment the server runs with DEMO_MODE and shows the code in a labelled panel and pre-fills it; otherwise the code is delivered to that contact only.</li>
                <li>Verify the code and read the appointments, records, plans, invoices and documents of that patient - and nothing else.</li>
              </ol>
            </div>

            <div className="glass-card rounded-[1.5rem] p-7 shadow-sm space-y-3">
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 rounded-xl bg-primary/10 flex items-center justify-center shrink-0"><CalendarCheck size={16} className="text-primary" /></div>
                <h3 className="font-bold font-display">Public booking (start: /book/enamel-central)</h3>
              </div>
              <ol className="space-y-2 text-sm text-muted-foreground leading-relaxed list-decimal list-inside">
                <li>Pick a bookable service and a provider.</li>
                <li>Choose any free slot in the next weeks; the same availability engine the staff calendar uses confirms it.</li>
                <li>Leave the patient's name and mobile number - the record appears in the clinic's staff workspace as an online booking.</li>
              </ol>
            </div>
          </div>
        </Section>

        {/* --- real vs sandbox --------------------------------------------- */}
        <Section eyebrow="Honesty" title="What is real, and what is a sandbox.">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            <div className="glass-card rounded-[1.5rem] p-7 shadow-sm">
              <div className="flex items-center gap-3 mb-5">
                <div className="w-9 h-9 rounded-xl bg-emerald-50 flex items-center justify-center shrink-0"><CheckCircle2 size={16} className="text-emerald-600" /></div>
                <h3 className="font-bold font-display">Real in this build</h3>
              </div>
              <ul className="space-y-3 text-sm text-muted-foreground leading-relaxed">
                {REAL.map((item) => (
                  <li key={item} className="flex gap-3"><Activity size={14} className="text-primary mt-1 shrink-0" />{item}</li>
                ))}
              </ul>
            </div>
            <div className="glass-card rounded-[1.5rem] p-7 shadow-sm">
              <div className="flex items-center gap-3 mb-5">
                <div className="w-9 h-9 rounded-xl bg-muted flex items-center justify-center shrink-0"><ShieldCheck size={16} className="text-muted-foreground" /></div>
                <h3 className="font-bold font-display">Sandbox or simulator only</h3>
              </div>
              <ul className="space-y-3 text-sm text-muted-foreground leading-relaxed">
                {SANDBOX.map((item) => (
                  <li key={item} className="flex gap-3"><Activity size={14} className="text-muted-foreground mt-1 shrink-0" />{item}</li>
                ))}
              </ul>
            </div>
          </div>
          <p className="text-xs text-muted-foreground leading-relaxed rounded-xl border border-border/70 bg-muted/40 px-4 py-3">
            Not claimed here: no clinical validation, no regulatory clearance and no compliance
            certification. The imaging model is a transparent intensity threshold evaluated on
            fixtures; the flagship claim of this repository is engineering scope, not medicine.
          </p>
        </Section>

        {/* --- one-time code ----------------------------------------------- */}
        <Section eyebrow="Security note" title="The portal one-time code: demo vs production.">
          <div className="glass-card rounded-[1.5rem] p-7 shadow-sm text-sm text-muted-foreground leading-relaxed space-y-3">
            <p>
              The portal has no password. Each sign-in resolves a fresh six-digit code that expires
              in ten minutes and tolerates five wrong attempts. The code is hashed at rest; the
              contact that requested it is hashed too, and the clinic plus the registered contact
              are the only way to resolve a patient.
            </p>
            <p>
              <strong className="text-foreground">Demo mode:</strong> a deployment that runs with
              <code className="font-mono font-bold mx-1">DEMO_MODE</code>
              (the hosted demo overlay) includes the generated code in the request-code response
              under <code className="font-mono font-bold">demo_code</code> and shows it in a labelled panel, so the
              walkthrough works without a mail or SMS provider.
            </p>
            <p>
              <strong className="text-foreground">Production mode:</strong> the settings refuse to start with
              DEMO_MODE. Nothing is echoed; the code reaches the patient contact only, through the
              delivery channels a real deployment configures.
            </p>
          </div>
        </Section>

        {/* --- contact / buy ------------------------------------------------ */}
        <Section eyebrow="Work with us" title="Want this for your business?">
          <div className="glass-card rounded-[1.5rem] p-8 shadow-sm flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
            <p className="text-sm text-muted-foreground leading-relaxed max-w-2xl">
              Enamel is a complete clinic platform: scheduling, patient records, charting, the
              imaging archive, treatment plans, billing, the patient portal and analytics. If you
              tried the demo and would like it tailored to your practice, or want a walkthrough
              before you decide, get in touch.
            </p>
            <a
              href="https://aakifmudel.xyz/contact"
              target="_blank"
              rel="noreferrer"
              data-testid="guide-contact-cta"
              className="inline-flex shrink-0 items-center justify-center gap-2 rounded-full bg-gradient-to-br from-primary to-primary-dim px-7 py-3.5 font-display text-sm font-bold text-white shadow-lg shadow-primary/20 transition-all hover:opacity-90 active:scale-95"
            >
              Contact the builder
              <ArrowUpRight size={16} />
            </a>
          </div>
        </Section>

        <footer className="border-t border-white/10 pt-8 text-[11px] text-muted-foreground uppercase tracking-[0.1em]">
          Enamel user guide - describes the evaluation build. No clinical validation or compliance
          certification is claimed.
        </footer>
      </div>
    </div>
  );
}
