import { Link } from "wouter";
import AppLayout from "@/layouts/AppLayout";
import { motion } from "framer-motion";

export default function NotFound() {
  return (
    <AppLayout>
      <div className="flex flex-col items-center justify-center min-h-[60vh] text-center px-8">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
        >
          <span className="font-sans text-primary uppercase tracking-[0.3em] text-xs font-bold mb-6 block">
            Navigation Error
          </span>
          <h1 className="font-display text-[8rem] font-extrabold leading-none tracking-tighter text-foreground/10 mb-0 select-none">
            404
          </h1>
          <h2 className="font-display text-3xl font-extrabold tracking-tight text-foreground -mt-6 mb-4">
            Screen Not Found
          </h2>
          <p className="text-muted-foreground text-lg mb-10 max-w-sm mx-auto leading-relaxed">
            This screen doesn't exist in the Enamel system. Navigate back using the sidebar.
          </p>
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-2 bg-gradient-to-br from-primary to-primary-dim text-white px-8 py-3.5 rounded-full font-bold text-sm hover:opacity-90 transition-all shadow-lg shadow-primary/20 active:scale-95"
          >
            Return to Dashboard
          </Link>
        </motion.div>
      </div>
    </AppLayout>
  );
}
