import { motion, type Variants } from "framer-motion";
import type { ComponentProps, ReactNode } from "react";

const fadeIn: Variants = {
  hidden: { opacity: 0, y: 30 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.6 } }
};

type RevealProps = {
  children: ReactNode;
  className?: string;
  viewport?: ComponentProps<typeof motion.div>["viewport"];
};

export default function Reveal({ children, className, viewport = { once: true } }: RevealProps) {
  return (
    <motion.div initial="hidden" whileInView="visible" viewport={viewport} variants={fadeIn} className={className}>
      {children}
    </motion.div>
  );
}
