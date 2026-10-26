import type { ComponentProps, ReactNode } from "react";
import { motion } from "framer-motion";
import Reveal from "@/components/landing/Reveal";
import SectionContainer from "@/components/landing/SectionContainer";

type FeatureSplitProps = {
  eyebrow: string;
  title: ReactNode;
  copy: ReactNode;
  media: ReactNode;
  blobs?: ReactNode;
  reverse?: boolean;
  mediaClassName?: string;
  copyClassName?: string;
  mediaViewport?: ComponentProps<typeof motion.div>["viewport"];
};

export default function FeatureSplit({
  eyebrow,
  title,
  copy,
  media,
  blobs,
  reverse = false,
  mediaClassName,
  copyClassName,
  mediaViewport,
}: FeatureSplitProps) {
  const copyColumn = (
    <Reveal className={copyClassName}>
      <span className="font-sans text-primary uppercase tracking-[0.2em] text-xs font-bold mb-6 block">{eyebrow}</span>
      <h2 className="font-display text-5xl md:text-6xl font-extrabold leading-tight tracking-tight text-foreground mb-8">{title}</h2>
      {copy}
    </Reveal>
  );

  const mediaColumn = (
    <Reveal className={mediaClassName} viewport={mediaViewport}>
      {media}
    </Reveal>
  );

  return (
    <section className="py-32 relative overflow-hidden">
      {blobs}
      <SectionContainer>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-20 items-center">
          {reverse ? (
            <>
              {copyColumn}
              {mediaColumn}
            </>
          ) : (
            <>
              {mediaColumn}
              {copyColumn}
            </>
          )}
        </div>
      </SectionContainer>
    </section>
  );
}
