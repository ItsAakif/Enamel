import { useState } from "react";
import { Link } from "wouter";
import { motion, useScroll, useMotionValueEvent, AnimatePresence } from "framer-motion";
import { X, Menu } from "lucide-react";

// Every entry points at a real route with real content. The guide is the
// publication-facing doc page; the legal pages describe the product honestly.
const NAV_LINKS = [
  { label: "Guide", href: "/guide" },
  { label: "Book a slot", href: "/book/enamel-central" },
  { label: "Terms", href: "/legal/terms" },
];

export default function ResizableNavbar() {
  const { scrollY } = useScroll();
  const [scrolled, setScrolled] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  useMotionValueEvent(scrollY, "change", (latest) => {
    setScrolled(latest > 60);
  });

  return (
    <>
      <motion.nav
        initial={false}
        animate={scrolled ? "pill" : "full"}
        variants={{
          full: {
            top: 0,
            left: 0,
            right: 0,
            width: "100%",
            maxWidth: "100%",
            x: 0,
            borderRadius: 0,
            paddingTop: "20px",
            paddingBottom: "20px",
            paddingLeft: "32px",
            paddingRight: "32px",
            backgroundColor: "rgba(255,255,255,0)",
            borderColor: "rgba(255,255,255,0)",
            boxShadow: "none",
          },
          pill: {
            top: 16,
            left: "50%",
            right: "auto",
            width: "min(760px, calc(100% - 48px))",
            maxWidth: "760px",
            x: "-50%",
            borderRadius: "9999px",
            paddingTop: "10px",
            paddingBottom: "10px",
            paddingLeft: "20px",
            paddingRight: "20px",
            backgroundColor: "rgba(255,255,255,0.72)",
            borderColor: "rgba(255,255,255,0.5)",
            boxShadow: "0 8px 32px rgba(70,71,211,0.10), 0 1px 0 rgba(255,255,255,0.9) inset",
          },
        }}
        transition={{ type: "spring", stiffness: 280, damping: 32 }}
        style={{
          position: "fixed",
          zIndex: 50,
          backdropFilter: "blur(20px)",
          WebkitBackdropFilter: "blur(20px)",
          borderWidth: 1,
          borderStyle: "solid",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <motion.div
          animate={scrolled ? { scale: 0.9 } : { scale: 1 }}
          transition={{ type: "spring", stiffness: 280, damping: 32 }}
          className="text-2xl font-black tracking-tighter text-foreground font-display shrink-0"
        >
          ENAMEL
        </motion.div>

        <motion.div
          className="hidden md:flex items-center"
          animate={scrolled ? { gap: "24px" } : { gap: "40px" }}
          transition={{ type: "spring", stiffness: 280, damping: 32 }}
        >
          {NAV_LINKS.map((link) => (
            <Link
              key={link.label}
              href={link.href}
              className="text-muted-foreground font-sans text-xs uppercase tracking-widest font-semibold hover:text-foreground transition-colors whitespace-nowrap"
            >
              {link.label}
            </Link>
          ))}
        </motion.div>

        <div className="flex gap-3 items-center">
          <Link
            href="/dashboard"
            className="hidden md:block text-sm font-bold text-foreground hover:text-primary transition-colors"
          >
            Sign In
          </Link>
          <Link
            href="/dashboard"
            className="bg-primary hover:opacity-90 text-primary-foreground px-5 py-2 rounded-full text-xs uppercase tracking-widest font-bold transition-all active:scale-95"
          >
            Get Access
          </Link>
          <button
            className="md:hidden p-2 text-foreground hover:bg-black/5 rounded-full transition-colors"
            onClick={() => setMobileOpen(true)}
            aria-label="Open menu"
          >
            <Menu size={20} />
          </button>
        </div>
      </motion.nav>

      <AnimatePresence>
        {mobileOpen && (
          <>
            <motion.div
              key="overlay"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-50 bg-black/30 backdrop-blur-sm md:hidden"
              onClick={() => setMobileOpen(false)}
            />
            <motion.div
              key="drawer"
              initial={{ opacity: 0, y: -20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              transition={{ type: "spring", stiffness: 320, damping: 32 }}
              className="fixed top-4 left-4 right-4 z-50 glass-nav rounded-3xl p-6 md:hidden"
            >
              <div className="flex justify-between items-center mb-6">
                <span className="text-xl font-black tracking-tighter text-foreground font-display">ENAMEL</span>
                <button
                  onClick={() => setMobileOpen(false)}
                  className="p-2 text-muted-foreground hover:bg-black/5 rounded-full transition-colors"
                >
                  <X size={20} />
                </button>
              </div>
              <nav className="space-y-2 mb-6">
                {NAV_LINKS.map((link) => (
                  <Link
                    key={link.label}
                    href={link.href}
                    onClick={() => setMobileOpen(false)}
                    className="block px-4 py-3 text-foreground font-semibold hover:bg-primary/5 rounded-xl transition-colors text-sm"
                  >
                    {link.label}
                  </Link>
                ))}
              </nav>
              <div className="flex flex-col gap-3">
                <Link
                  href="/dashboard"
                  onClick={() => setMobileOpen(false)}
                  className="text-center py-3 font-bold text-foreground border border-border rounded-full text-sm hover:bg-muted transition-colors"
                >
                  Sign In
                </Link>
                <Link
                  href="/dashboard"
                  onClick={() => setMobileOpen(false)}
                  className="text-center py-3 bg-primary text-primary-foreground rounded-full font-bold text-sm hover:opacity-90 transition-all"
                >
                  Get Access
                </Link>
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </>
  );
}
