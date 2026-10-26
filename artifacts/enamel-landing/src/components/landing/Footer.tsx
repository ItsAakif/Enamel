import { Link } from "wouter";

export default function Footer() {
  return (
    <footer className="bg-zinc-950 text-white pt-16 pb-10">
      <div className="max-w-screen-2xl mx-auto px-8 text-center flex flex-col items-center">
        <div className="text-4xl font-black tracking-tighter mb-8 font-display text-white opacity-90">ENAMEL</div>
        <div className="flex flex-wrap justify-center gap-8 md:gap-16 mb-10">
          <Link className="text-zinc-500 hover:text-white transition-colors font-sans text-xs uppercase tracking-widest font-bold" to="/guide">User Guide</Link>
          <Link className="text-zinc-500 hover:text-white transition-colors font-sans text-xs uppercase tracking-widest font-bold" to="/book/enamel-central">Book a slot</Link>
          <Link className="text-zinc-500 hover:text-white transition-colors font-sans text-xs uppercase tracking-widest font-bold" to="/legal/privacy">Privacy Protocol</Link>
          <Link className="text-zinc-500 hover:text-white transition-colors font-sans text-xs uppercase tracking-widest font-bold" to="/legal/terms">Terms of Service</Link>
          <Link className="text-zinc-500 hover:text-white transition-colors font-sans text-xs uppercase tracking-widest font-bold" to="/legal/ai-ethics">AI Ethics</Link>
          <Link className="text-zinc-500 hover:text-white transition-colors font-sans text-xs uppercase tracking-widest font-bold" to="/legal/contact">Contact</Link>
          <a
            className="text-primary hover:text-white transition-colors font-sans text-xs uppercase tracking-widest font-bold"
            href="https://aakifmudel.xyz/contact"
            target="_blank"
            rel="noreferrer"
            data-testid="footer-buy-link"
          >
            Want this? Get in touch
          </a>
        </div>
        <div className="text-zinc-600 font-sans text-xs uppercase tracking-[0.1em] border-t border-white/10 pt-6 w-full">
          © 2026 ENAMEL. Software for dental clinics. No clinical validation,
          regulatory clearance or compliance certification is claimed.
        </div>
      </div>
    </footer>
  );
}
