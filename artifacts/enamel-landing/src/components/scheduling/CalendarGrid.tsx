import type { ReactNode } from "react";
import { motion } from "framer-motion";

interface CalendarGridProps {
  /** Tailwind `grid-cols-*` template for the gutter plus day columns. */
  columns: string;
  rowMinHeight: string;
  gutterClassName: string;
  header: ReactNode;
  /** Hour labels, one per row (e.g. `08:00`). */
  hours: string[];
  renderCells: (rowIdx: number) => ReactNode;
}

/**
 * Presentational calendar grid. Hour labels are supplied by the page (they come
 * from the real schedule window, not a constant), so this stays mock-free.
 */
export function CalendarGrid({ columns, rowMinHeight, gutterClassName, header, hours, renderCells }: CalendarGridProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.25 }}
      className="glass-card rounded-[2rem] overflow-hidden shadow-sm"
    >
      <div className={`grid ${columns} border-b border-white/30 bg-white/20`}>
        <div className="p-4" />
        {header}
      </div>
      <div className="relative">
        {hours.map((hour, rowIdx) => (
          <div key={hour} className={`grid ${columns} border-b border-border/50 ${rowMinHeight}`}>
            <div className={gutterClassName}>
              <span className="text-[10px] font-bold text-muted-foreground font-sans">{hour}</span>
            </div>
            {renderCells(rowIdx)}
          </div>
        ))}
      </div>
    </motion.div>
  );
}
