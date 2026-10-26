import type { ChartTooth } from "@workspace/api-client-react";

type Tone = "healthy" | "caries" | "planned" | "restored" | "missing";

const TONE_CLASS: Record<Tone, string> = {
  healthy: "bg-background border-border text-muted-foreground hover:border-primary",
  caries: "bg-destructive/10 border-destructive text-destructive",
  planned: "bg-primary/10 border-primary text-primary",
  restored: "bg-emerald-50 border-emerald-400 text-emerald-700",
  missing: "bg-muted border-muted-foreground/40 text-muted-foreground/70 line-through",
};

const TONE_DOT: Record<Tone, string> = {
  healthy: "bg-transparent",
  caries: "bg-destructive",
  planned: "bg-primary",
  restored: "bg-emerald-500",
  missing: "bg-muted-foreground/50",
};

function toneFor(tooth: ChartTooth): Tone {
  const states = tooth.states;
  if (states.length === 0) return "healthy";
  if (states.some((state) => state.condition_code === "missing")) return "missing";
  if (states.some((state) => state.status === "planned")) return "planned";
  if (states.some((state) => state.condition_code === "caries")) return "caries";
  return "restored";
}

/** FDI display order: patient's right quadrant descending, left quadrant ascending. */
function orderQuadrant(teeth: ChartTooth[], quadrant: number, direction: "asc" | "desc"): ChartTooth[] {
  const filtered = teeth.filter((tooth) => tooth.info.quadrant === quadrant);
  filtered.sort((a, b) => (direction === "asc" ? a.tooth - b.tooth : b.tooth - a.tooth));
  return filtered;
}

interface OdontogramProps {
  teeth: ChartTooth[];
  selectedTooth: number | null;
  onSelect: (tooth: number) => void;
}

function ToothButton({
  tooth,
  selected,
  onSelect,
  shape,
}: {
  tooth: ChartTooth;
  selected: boolean;
  onSelect: (tooth: number) => void;
  shape: string;
}) {
  const tone = toneFor(tooth);
  return (
    <button
      type="button"
      onClick={() => onSelect(tooth.tooth)}
      aria-pressed={selected}
      aria-label={`Tooth ${tooth.tooth}: ${tooth.info.label}`}
      title={tooth.info.label}
      data-testid={`tooth-${tooth.tooth}`}
      className={`relative min-w-[44px] min-h-[44px] w-11 h-14 border-2 flex items-center justify-center transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary ${shape} ${
        TONE_CLASS[tone]
      } ${selected ? "ring-2 ring-primary ring-offset-1 ring-offset-background" : ""}`}
    >
      <span className="text-[10px] font-bold">{tooth.tooth}</span>
      {tone !== "healthy" ? (
        <span className={`absolute -top-1 -right-1 w-2 h-2 rounded-full ${TONE_DOT[tone]}`} aria-hidden="true" />
      ) : null}
    </button>
  );
}

/**
 * FDI odontogram rendered from the API's `teeth` array.
 *
 * Quadrant layout follows ISO 3950: quadrant 1 (upper right) and 4 (lower
 * right) render on the viewer's left, 2/3 on the right; primary quadrants 5-8
 * slot into the same positions. Every tooth is a 44px button so the chart is
 * keyboard and touch accessible.
 */
export function Odontogram({ teeth, selectedTooth, onSelect }: OdontogramProps) {
  if (teeth.length === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-10">
        The API returned no teeth for this dentition.
      </p>
    );
  }

  const upperRight = [...orderQuadrant(teeth, 1, "desc"), ...orderQuadrant(teeth, 5, "desc")];
  const upperLeft = [...orderQuadrant(teeth, 2, "asc"), ...orderQuadrant(teeth, 6, "asc")];
  const lowerRight = [...orderQuadrant(teeth, 4, "desc"), ...orderQuadrant(teeth, 8, "desc")];
  const lowerLeft = [...orderQuadrant(teeth, 3, "asc"), ...orderQuadrant(teeth, 7, "asc")];

  const upperRow = [...upperRight, ...upperLeft];
  const lowerRow = [...lowerRight, ...lowerLeft];

  return (
    <div className="flex flex-col items-center gap-6">
      <div className="flex items-end gap-1.5 flex-wrap justify-center">
        {upperRow.map((tooth) => (
          <ToothButton
            key={tooth.tooth}
            tooth={tooth}
            selected={selectedTooth === tooth.tooth}
            onSelect={onSelect}
            shape="rounded-t-full rounded-b-md"
          />
        ))}
      </div>
      <div className="w-full max-w-md h-px bg-border/60" aria-hidden="true" />
      <div className="flex items-start gap-1.5 flex-wrap justify-center">
        {lowerRow.map((tooth) => (
          <ToothButton
            key={tooth.tooth}
            tooth={tooth}
            selected={selectedTooth === tooth.tooth}
            onSelect={onSelect}
            shape="rounded-b-full rounded-t-md"
          />
        ))}
      </div>
    </div>
  );
}
