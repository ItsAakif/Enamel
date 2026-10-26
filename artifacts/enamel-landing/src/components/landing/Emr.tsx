import { Stethoscope } from "lucide-react";
import FeatureSplit from "@/components/landing/FeatureSplit";
import { PRIMARY_BLOB_GRADIENT } from "@/components/landing/depthBlobGradients";

export default function Emr() {
  return (
    <FeatureSplit
      eyebrow="Electronic Medical Records"
      title={<>Patient history,<br/>at a glance.</>}
      reverse
      mediaClassName="relative"
      blobs={
        <div className="depth-blob w-[400px] h-[400px] top-[10%] right-[-80px] opacity-18" style={{ background: PRIMARY_BLOB_GRADIENT }} />
      }
      copy={
        <>
          <p className="text-xl text-muted-foreground leading-relaxed mb-10">
            Every chart, scan, and note — instantly accessible. Enamel's EMR surfaces the right information at the right moment, so you never chase context again.
          </p>
          <div className="flex gap-5">
            <div className="mt-1"><Stethoscope className="text-primary" size={28} /></div>
            <div>
              <h3 className="text-xl font-bold mb-2">Unified Patient View</h3>
              <p className="text-muted-foreground leading-relaxed">Medical history, allergies, insurance, and clinical timeline unified in one interface.</p>
            </div>
          </div>
        </>
      }
      media={
        <div className="glass-card rounded-[2rem] p-8 shadow-2xl">
          <div className="flex items-center gap-4 mb-6 pb-6 border-b border-white/30">
            <div className="w-14 h-14 rounded-full bg-gradient-to-br from-primary/20 to-primary-dim/10 flex items-center justify-center text-primary text-base font-black font-display">EV</div>
            <div>
              <h4 className="font-display font-bold text-lg">Eleanor Vance</h4>
              <p className="text-xs text-primary font-bold">ID: #EN-98210 · Active Record</p>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4 mb-6">
            {[["Age", "32 Years"], ["Blood Type", "O Positive"], ["Last Visit", "Oct 12, 2023"], ["Allergies", "Penicillin"]].map(([l, v]) => (
              <div key={l}>
                <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">{l}</p>
                <p className="text-sm font-bold text-foreground mt-0.5">{v}</p>
              </div>
            ))}
          </div>
          <div className="flex gap-2 flex-wrap">
            {["Latex Allergy", "Hypertension", "Penicillin Sensitive"].map((tag) => (
              <span key={tag} className={`text-[10px] font-bold px-3 py-1.5 rounded-full border ${tag === "Penicillin Sensitive" ? "bg-destructive/10 text-destructive border-destructive/20" : "bg-muted text-foreground border-border"}`}>{tag}</span>
            ))}
          </div>
        </div>
      }
    />
  );
}
