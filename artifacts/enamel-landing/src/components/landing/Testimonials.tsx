import { motion } from "framer-motion";
import SectionContainer from "@/components/landing/SectionContainer";

/**
 * Demo profiles.
 *
 * Enamel has not collected publishable testimonials. These three cards are
 * fictional personas written for the demonstration build: the people and the
 * clinics do not exist, and the quotations describe the product's own
 * workflows rather than any measured result. A real quotation may only be
 * added here with the written permission of the person named, and only when
 * the person is real.
 */
const DEMO_PROFILES = [
  {
    name: "Dr. Maya Chen",
    initials: "MC",
    role: "Clinical Director",
    clinic: "Northline Dental",
    quote:
      "The dashboard and the charting feel like one workflow. My plan for the visit sits on the same screen as the notes, so nothing has to be found twice.",
  },
  {
    name: "Rhea Malhotra",
    initials: "RM",
    role: "Front Office Lead",
    clinic: "Cedar Room Dental",
    quote:
      "Scheduling clicked after one afternoon: appointment types, reminders and the waitlist all live in the same view, and moving a patient around takes seconds.",
  },
  {
    name: "Dr. Arjun Patel",
    initials: "AP",
    role: "Principal Dentist",
    clinic: "Harbour Lane Dental",
    quote:
      "People answer the routine questions in the patient portal before they arrive, so handoffs at the front desk are calmer and less is left to memory.",
  },
];

export default function Testimonials() {
  return (
    <section className="py-32 border-y border-border/50 relative overflow-hidden">
      <div className="depth-blob w-[500px] h-[400px] top-[10%] left-[20%] opacity-15" style={{ background: "radial-gradient(circle, rgba(70,71,211,0.12) 0%, transparent 70%)" }} />
      <div className="depth-blob w-[400px] h-[300px] bottom-[10%] right-[10%] opacity-12" style={{ background: "radial-gradient(circle, rgba(94,74,179,0.12) 0%, transparent 70%)" }} />
      <SectionContainer>
        <div className="text-center mb-20">
          <h2 className="font-display text-4xl md:text-5xl font-extrabold tracking-tight">What clinics could say</h2>
          <p className="text-sm text-muted-foreground mt-4 max-w-2xl mx-auto">
            Illustrative demo profiles. Names and clinics are fictional; these
            are not real customer testimonials or endorsements.
          </p>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
          {DEMO_PROFILES.map((t, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.1 }}
              className="glass-card p-8 rounded-[2rem] shadow-sm hover:shadow-lg transition-all duration-300 flex flex-col"
            >
              <p className="text-foreground leading-relaxed mb-8 font-medium grow">"{t.quote}"</p>
              <div className="flex items-center gap-4">
                <div className="w-12 h-12 rounded-full bg-primary/10 text-primary flex items-center justify-center font-bold text-sm font-display">
                  {t.initials}
                </div>
                <div>
                  <h4 className="font-bold text-sm text-foreground">{t.name}</h4>
                  <p className="text-xs text-muted-foreground">{t.role}, {t.clinic}</p>
                  <p className="text-[10px] uppercase tracking-wide text-muted-foreground/70 mt-1">
                    Illustrative demo profile
                  </p>
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      </SectionContainer>
    </section>
  );
}
