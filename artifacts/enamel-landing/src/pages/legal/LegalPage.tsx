import { Link } from "wouter";
import type { ReactNode } from "react";

/**
 * Shared shell for the public legal pages.
 *
 * These pages exist because the footer used to point at ``href="#"``. They
 * state what the software actually does and, just as importantly, what it does
 * *not* claim: Enamel is not certified, no compliance regime is claimed, and
 * the imaging model is not clinically validated.
 */
export function LegalPage({
  title,
  updated,
  children,
}: {
  title: string;
  updated: string;
  children: ReactNode;
}) {
  return (
    <div className="min-h-screen bg-background font-sans">
      <header className="border-b border-border/60">
        <div className="max-w-3xl mx-auto px-8 py-8 flex items-center justify-between">
          <Link to="/" className="font-display text-2xl font-black tracking-tighter">
            ENAMEL
          </Link>
          <Link to="/" className="text-sm font-semibold text-muted-foreground hover:text-foreground transition-colors">
            Back to the overview
          </Link>
        </div>
      </header>
      <main className="max-w-3xl mx-auto px-8 py-16">
        <h1 className="font-display text-4xl md:text-5xl font-extrabold tracking-tight mb-4">{title}</h1>
        <p className="text-sm text-muted-foreground mb-12">Last reviewed: {updated}</p>
        <div className="space-y-10 text-base leading-relaxed text-foreground/90">{children}</div>
      </main>
      <footer className="border-t border-border/60">
        <div className="max-w-3xl mx-auto px-8 py-10 text-sm text-muted-foreground space-y-2">
          <p>
            <Link to="/legal/privacy" className="hover:text-foreground transition-colors">Privacy</Link>
            {" · "}
            <Link to="/legal/terms" className="hover:text-foreground transition-colors">Terms</Link>
            {" · "}
            <Link to="/legal/ai-ethics" className="hover:text-foreground transition-colors">AI ethics</Link>
            {" · "}
            <Link to="/legal/contact" className="hover:text-foreground transition-colors">Contact</Link>
          </p>
          <p>No clinical validation, regulatory clearance or compliance certification is claimed.</p>
        </div>
      </footer>
    </div>
  );
}

export function LegalSection({ heading, children }: { heading: string; children: ReactNode }) {
  return (
    <section>
      <h2 className="font-display text-2xl font-extrabold tracking-tight mb-3">{heading}</h2>
      <div className="space-y-4 text-muted-foreground">{children}</div>
    </section>
  );
}

export default LegalPage;
