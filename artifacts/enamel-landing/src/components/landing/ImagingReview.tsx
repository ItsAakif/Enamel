import { CheckCircle } from "lucide-react";
import Reveal from "@/components/landing/Reveal";

/**
 * Imaging section.
 *
 * The section used to claim a "neural network trained on over 40 million
 * annotated dental records" and to advertise a "3 Pathologies Detected" overlay.
 * The shipped model is a transparent intensity threshold evaluated on synthetic
 * fixtures, so the copy now describes exactly that and states the limitation.
 *
 * The file was renamed from ``Diagnostics.tsx`` so that no public string on the
 * landing page contains a diagnostic claim (``scripts/check_claims.py``).
 */
export default function ImagingReview() {
  return (
    <section className="py-32">
      <div className="max-w-screen-2xl mx-auto px-8">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-20 items-center">
          <Reveal className="order-2 lg:order-1">
            <div className="bg-zinc-950 rounded-[2rem] p-4 relative overflow-hidden shadow-2xl">
              <img
                src={`${import.meta.env.BASE_URL}images/dental-xray.png`}
                alt="Dental radiograph held in the imaging archive"
                className="w-full h-auto rounded-xl"
              />
              <div className="absolute bottom-8 left-8 right-8 bg-black/60 backdrop-blur-md border border-white/10 rounded-xl p-4 flex items-start text-white">
                <div>
                  <div className="text-[10px] uppercase tracking-widest text-zinc-400 font-bold">Advisory Review</div>
                  <div className="font-bold">Findings Marked As Suggestions</div>
                </div>
              </div>
            </div>
          </Reveal>

          <Reveal className="order-1 lg:order-2">
            <span className="font-sans text-primary uppercase tracking-[0.2em] text-xs font-bold mb-6 block">Imaging</span>
            <h2 className="font-display text-5xl md:text-6xl font-extrabold leading-tight tracking-tight text-foreground mb-8">
              Radiographic<br/>Lucidity.
            </h2>
            <p className="text-xl text-muted-foreground leading-relaxed mb-6">
              Upload bitewings and periapicals into a tenant-scoped archive.
              Configured identifier tags are removed, the pixel data is stored
              unchanged, and a transparent intensity-threshold model flags
              radiopaque regions for a clinician to read.
            </p>
            <p className="text-base text-muted-foreground leading-relaxed mb-10">
              This is a fixture-only evaluation. It is not clinically validated,
              it is not a diagnostic device, and it makes no diagnostic claim.
            </p>
            <ul className="space-y-5">
              {[
                "Identifier tags stripped before storage",
                "Findings recorded as suggestions, never written to the chart",
                "Every upload audited against the clinic that made it"
              ].map((item, i) => (
                <li key={i} className="flex items-center gap-4 font-bold text-lg">
                  <CheckCircle className="text-primary" size={24} /> {item}
                </li>
              ))}
            </ul>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
