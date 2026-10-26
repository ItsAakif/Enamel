import { Link } from "wouter";

export default function FinalCTA() {
  return (
    <section className="py-40 bg-background text-center">
      <div className="max-w-4xl mx-auto px-8">
        <span className="font-sans text-primary uppercase tracking-[0.3em] text-xs font-bold mb-8 block">Run it yourself</span>
        <h2 className="font-display text-[clamp(3rem,6vw,5rem)] font-extrabold tracking-tighter text-foreground mb-10 uppercase leading-none">
          Start With<br/>Your Own Clinic.
        </h2>
        <p className="text-xl text-muted-foreground font-medium leading-relaxed mb-12 max-w-2xl mx-auto">
          This build ships with offline-safe adapters: a deterministic AI
          provider, a sandbox payment gateway and a development messaging
          adapter. Connect real providers, and read what is and is not claimed,
          before you use it with live patient data.
        </p>
        <div className="flex flex-col sm:flex-row items-center justify-center gap-6">
          <Link href="/login" className="w-full sm:w-auto bg-gradient-to-br from-primary to-primary-dim text-white px-12 py-5 rounded-full font-bold text-lg hover:shadow-[0_20px_40px_rgba(70,71,211,0.25)] transition-all scale-100 hover:scale-105">
            Open the workspace
          </Link>
          <Link href="/legal/ai-ethics" className="w-full sm:w-auto border-2 border-border px-12 py-5 rounded-full font-bold text-lg hover:bg-muted transition-all">
            AI &amp; privacy notes
          </Link>
        </div>
      </div>
    </section>
  );
}
