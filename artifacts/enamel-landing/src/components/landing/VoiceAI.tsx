import { Mic } from "lucide-react";
import Reveal from "@/components/landing/Reveal";
import SectionContainer from "@/components/landing/SectionContainer";

export default function VoiceAI() {
  return (
    <section className="py-32 bg-zinc-950 text-white relative overflow-hidden">
      <div className="absolute inset-0 opacity-20 pointer-events-none mix-blend-screen">
        <img src={`${import.meta.env.BASE_URL}images/voice-ai-wave.png`} className="w-full h-full object-cover" alt="" />
      </div>

      <SectionContainer>
        <div className="flex flex-col lg:flex-row gap-20 items-center">
          <Reveal className="lg:w-1/2">
            <span className="text-primary-foreground bg-primary/20 px-3 py-1 rounded-full uppercase tracking-widest text-[10px] font-bold mb-6 inline-block">Voice Intelligence</span>
            <h2 className="font-display text-5xl md:text-6xl font-extrabold leading-tight tracking-tight mb-8">
              Appointments by voice,<br/>end to end.
            </h2>
            <p className="text-xl text-zinc-400 leading-relaxed mb-10">
              A voice assistant that books, reschedules and cancels appointments.
              It identifies the caller, asks for confirmation before anything
              changes, and hands the call to a staff member when it is unsure.
            </p>

            <div className="grid grid-cols-2 gap-6 mb-10">
              <div className="border border-white/10 bg-white/5 p-6 rounded-2xl backdrop-blur-sm">
                <div className="text-4xl font-black text-primary mb-1">3</div>
                <div className="text-xs font-bold uppercase tracking-widest text-zinc-500">Supported Actions</div>
              </div>
              <div className="border border-white/10 bg-white/5 p-6 rounded-2xl backdrop-blur-sm">
                <div className="text-4xl font-black text-primary mb-1">1</div>
                <div className="text-xs font-bold uppercase tracking-widest text-zinc-500">Confirmation Before Any Change</div>
              </div>
            </div>
          </Reveal>

          <Reveal className="lg:w-1/2 w-full">
            <div className="border border-white/10 bg-black/40 backdrop-blur-xl rounded-[2rem] p-8 shadow-2xl relative">
              <div className="flex items-center gap-3 mb-8">
                <div className="w-3 h-3 bg-emerald-500 rounded-full animate-pulse"></div>
                <span className="text-xs font-bold uppercase tracking-widest text-emerald-500">Live &middot; Conversation</span>
              </div>

              <div className="flex justify-center mb-10">
                <div className="w-24 h-24 rounded-full bg-primary/20 flex items-center justify-center voice-glow relative">
                  <Mic size={40} className="text-primary" />
                  <div className="absolute inset-0 border border-primary/50 rounded-full animate-ping"></div>
                </div>
              </div>

              <div className="space-y-4 max-h-[200px] overflow-hidden relative">
                <div className="absolute bottom-0 w-full h-20 bg-gradient-to-t from-black/80 to-transparent z-10"></div>
                <div className="bg-white/5 p-4 rounded-xl text-sm border border-white/5">
                  <span className="font-bold text-primary mr-2">Assistant:</span>
                  <span className="text-zinc-300">I can move your appointment to Tuesday at 10:00. Shall I confirm the change?</span>
                </div>
                <div className="bg-white/5 p-4 rounded-xl text-sm border border-white/5">
                  <span className="font-bold text-zinc-400 mr-2">Caller:</span>
                  <span className="text-zinc-300">Yes, please confirm it and let me know if anything else changes.</span>
                </div>
              </div>
            </div>
          </Reveal>
        </div>
      </SectionContainer>
    </section>
  );
}
