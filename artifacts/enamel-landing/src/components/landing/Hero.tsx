import { Link } from "wouter";
import { motion } from "framer-motion";
import { ArrowRight } from "lucide-react";

export default function Hero() {
  return (
    <section className="relative min-h-[90vh] pt-40 pb-24 flex items-center overflow-hidden">
      {/* Video background */}
      <video
        autoPlay
        muted
        loop
        playsInline
        className="absolute inset-0 w-full h-full object-cover z-0"
        src={`${import.meta.env.BASE_URL}videos/hero-bg.mp4`}
      />
      {/* Semi-transparent overlay for text legibility */}
      <div className="absolute inset-0 z-[1] bg-gradient-to-b from-black/50 via-black/30 to-black/60" />

      <div className="max-w-screen-2xl mx-auto px-8 relative z-10 w-full flex flex-col items-center text-center">
        <motion.h1
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3, duration: 0.7 }}
          className="font-display text-[clamp(3.5rem,7vw,6.5rem)] leading-[0.95] font-extrabold tracking-tighter text-white mb-8 uppercase"
        >
          Your Dental<br/>Practice.<br/>
          <span className="text-primary text-glow">One System.</span>
        </motion.h1>

        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.5 }}
          className="text-xl md:text-2xl text-white/80 max-w-2xl font-medium leading-relaxed mb-10"
        >
          Enamel brings charting, scheduling, imaging, billing and the patient
          portal into one multi-tenant clinic platform. Every request is scoped
          to the clinic it belongs to.
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.7 }}
          className="flex flex-wrap items-center justify-center gap-6"
        >
          <Link href="/login" className="bg-gradient-to-br from-primary to-primary-dim text-white px-10 py-5 rounded-full font-bold text-lg hover:shadow-[0_10px_30px_rgba(70,71,211,0.3)] transition-all active:scale-95 inline-flex items-center gap-2">
            Open the workspace <ArrowRight size={20} />
          </Link>
          <Link href="/book/enamel-central" className="flex items-center gap-3 text-white font-semibold group px-6 py-4 rounded-full border-2 border-white/40 hover:bg-white/10 transition-colors">
            Book an appointment
          </Link>
          <Link href="/legal/ai-ethics" className="flex items-center gap-3 text-white font-semibold group px-6 py-4 rounded-full border-2 border-white/40 hover:bg-white/10 transition-colors">
            How the AI is used
          </Link>
        </motion.div>
      </div>
    </section>
  );
}
