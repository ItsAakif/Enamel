import { Brain, Users } from "lucide-react";
import FeatureSplit from "@/components/landing/FeatureSplit";

export default function Transformation() {
  return (
    <FeatureSplit
      eyebrow="The Transformation"
      title="From front-desk chaos to clinical calm."
      mediaViewport={{ once: true, margin: "-100px" }}
      mediaClassName="relative"
      blobs={
        <div className="depth-blob w-[500px] h-[400px] top-[10%] right-[-150px] opacity-20" style={{ background: "radial-gradient(circle, rgba(70,71,211,0.18) 0%, transparent 70%)" }} />
      }
      media={
        <>
          <div className="rounded-[2rem] shadow-2xl aspect-[4/3] bg-gradient-to-br from-zinc-900 to-primary/80 flex items-center justify-center relative overflow-hidden">
            <img
              src="/images/dashboard-preview.png"
              alt="Enamel staff workspace: clinical overview with appointment, completion, cancellation, no-show and revenue metrics"
              className="absolute inset-0 w-full h-full object-cover"
              loading="lazy"
            />
          </div>
          <div className="absolute -bottom-8 -right-8 glass-card p-8 rounded-[2rem] shadow-xl max-w-xs hidden md:block">
            <span className="font-display text-5xl font-extrabold text-primary mb-2 block">Queue</span>
            <p className="text-sm font-medium text-foreground leading-relaxed">Front-desk steps Enamel runs for you: reminders, waitlist offers and gap backfill.</p>
          </div>
        </>
      }
      copy={
        <div className="space-y-8">
          <div className="flex gap-5">
            <div className="mt-1"><Users className="text-primary" size={28} /></div>
            <div>
              <h3 className="text-xl font-bold mb-2">Workflow Automation</h3>
              <p className="text-muted-foreground leading-relaxed">Reminders, recall lists, waitlist offers and invoice notifications are scheduled jobs. Anything that changes a confirmed appointment is proposed first and applied only after a staff member approves it.</p>
            </div>
          </div>
          <div className="flex gap-5">
            <div className="mt-1"><Brain className="text-primary" size={28} /></div>
            <div>
              <h3 className="text-xl font-bold mb-2">Assistive Imaging Review</h3>
              <p className="text-muted-foreground leading-relaxed">Upload a DICOM study and the archive strips the configured identifier tags, then flags radiopaque regions for a clinician to read. The bundled model is a transparent intensity threshold, not a trained network: this is a fixture-only evaluation, it is not clinically validated, and it makes no diagnostic claim.</p>
            </div>
          </div>
        </div>
      }
    />
  );
}
