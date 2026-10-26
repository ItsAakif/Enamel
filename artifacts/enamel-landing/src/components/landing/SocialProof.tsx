import SectionContainer from "@/components/landing/SectionContainer";

/**
 * Placeholder strip.
 *
 * The previous version of this section listed real third-party institutions
 * (DENTSPLY SIRONA, MAYO CLINIC, HARVARD MEDICAL, STANFORD DENTAL, ORAL-B
 * ACADEMY) as if they endorsed the product. Nothing in this repository records
 * such an endorsement, so the strip now carries clearly-labelled placeholders
 * and says so. A real customer logo may only be added here together with a
 * written, on-record permission from that organisation.
 *
 * ``scripts/check_claims.py`` fails the build if a third-party institution name
 * reappears in the landing sources.
 */
const PLACEHOLDER_LABELS = ["Illustrative logo", "Illustrative logo", "Illustrative logo"];

export default function SocialProof() {
  return (
    <section className="py-20 border-y border-border/50 relative overflow-hidden">
      <div className="depth-blob w-[600px] h-[300px] top-0 left-[-100px] opacity-20" style={{ background: "radial-gradient(ellipse, rgba(70,71,211,0.15) 0%, transparent 70%)" }} />
      <SectionContainer>
        <p className="text-center text-xs font-bold uppercase tracking-[0.2em] text-muted-foreground mb-10">
          Placeholder marks &mdash; no endorsement
        </p>
        <div className="flex flex-wrap justify-center items-center gap-6 md:gap-10">
          {PLACEHOLDER_LABELS.map((label, index) => (
            <span
              key={`${label}-${index}`}
              className="glass-chip px-5 py-2.5 rounded-full font-display font-black text-base md:text-lg tracking-tighter text-foreground/40"
              data-testid="placeholder-logo"
            >
              {label}
            </span>
          ))}
        </div>
        <p className="text-center text-xs text-muted-foreground mt-8 max-w-2xl mx-auto">
          No clinic, vendor, university or institution is named, implied or
          endorsed on this page. Replace these marks only with a written
          permission from the organisation concerned.
        </p>
      </SectionContainer>
    </section>
  );
}
