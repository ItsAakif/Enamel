import { CheckCircle } from "lucide-react";
import FeatureSplit from "@/components/landing/FeatureSplit";
import { PRIMARY_BLOB_GRADIENT } from "@/components/landing/depthBlobGradients";

export default function Scheduling() {
  return (
    <FeatureSplit
      eyebrow="Scheduling Intelligence"
      title="A calendar that thinks for itself."
      reverse
      mediaClassName="relative"
      blobs={
        <>
          <div className="depth-blob w-[450px] h-[450px] top-[5%] left-[-100px] opacity-20" style={{ background: "radial-gradient(circle, rgba(94,74,179,0.18) 0%, transparent 70%)" }} />
          <div className="depth-blob w-[350px] h-[350px] bottom-[5%] right-[-80px] opacity-15" style={{ background: PRIMARY_BLOB_GRADIENT }} />
        </>
      }
      copy={
        <>
          <p className="text-xl text-muted-foreground leading-relaxed mb-10">
            Enamel's scheduling engine reads availability rules, provider
            schedules and cancellations to propose waitlist offers for open
            slots. A staff member approves every change to a confirmed
            appointment.
          </p>
          <ul className="space-y-5">
            {["Waitlist scoring and timed offers", "Scheduled appointment reminders", "Gap detection and backfill"].map((item, i) => (
              <li key={i} className="flex items-center gap-4 font-bold text-lg">
                <CheckCircle className="text-primary shrink-0" size={24} /> {item}
              </li>
            ))}
          </ul>
        </>
      }
      media={
        <>
          <div className="glass-card rounded-[2rem] p-8 shadow-2xl">
            <div className="flex justify-between items-center mb-6">
              <h4 className="font-display font-bold text-lg">Wednesday, May 15</h4>
              <div className="flex gap-2">
                <div className="w-8 h-8 rounded-full border border-border flex items-center justify-center text-muted-foreground hover:bg-muted transition-colors cursor-pointer">‹</div>
                <div className="w-8 h-8 rounded-full border border-border flex items-center justify-center text-muted-foreground hover:bg-muted transition-colors cursor-pointer">›</div>
              </div>
            </div>
            <div className="space-y-3">
              {[
                { time: "09:00 AM", name: "Jonathan Vance", tag: "In Surgery", color: "bg-primary/10 text-primary border-l-4 border-primary" },
                { time: "10:30 AM", name: "Sarah Miller", tag: "Scheduled", color: "bg-muted text-muted-foreground border-l-4 border-muted-foreground/30" },
                { time: "12:00 PM", name: "— Open Slot —", tag: "Available", color: "bg-emerald-50 text-emerald-700 border-l-4 border-emerald-400 border-dashed" },
                { time: "01:30 PM", name: "Marcus Chen", tag: "High Priority", color: "bg-destructive/8 text-destructive border-l-4 border-destructive" },
              ].map((row) => (
                <div key={row.time} className="grid grid-cols-[80px_1fr] gap-4 items-center">
                  <span className="text-xs font-bold text-muted-foreground">{row.time}</span>
                  <div className={`px-4 py-3 rounded-xl flex justify-between items-center ${row.color}`}>
                    <span className="text-sm font-semibold">{row.name}</span>
                    <span className="text-[10px] font-bold uppercase tracking-wide">{row.tag}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="absolute -bottom-6 -left-6 bg-gradient-to-br from-primary to-primary-dim text-white p-6 rounded-2xl shadow-xl hidden md:block">
            <div className="text-3xl font-black">Waitlist</div>
            <div className="text-xs font-bold opacity-80 mt-1">Offers For Cancelled Slots</div>
          </div>
        </>
      }
    />
  );
}
