import type { ReactNode } from "react";

export default function SectionContainer({ children }: { children: ReactNode }) {
  return <div className="max-w-screen-2xl mx-auto px-8 relative z-10">{children}</div>;
}
