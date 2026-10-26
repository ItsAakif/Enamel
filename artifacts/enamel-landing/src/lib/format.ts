import { format, parseISO } from "date-fns";
import type { Metric } from "@workspace/api-client-react";

/** Format an ISO date/datetime string for display; `""` for missing values. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  try {
    return format(parseISO(value), "d MMM yyyy");
  } catch {
    return value;
  }
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  try {
    return format(parseISO(value), "d MMM yyyy, HH:mm");
  } catch {
    return value;
  }
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  try {
    return format(parseISO(value), "HH:mm");
  } catch {
    return value;
  }
}

/** `yyyy-MM-dd` for a Date in local time (matches Django `DateField`). */
export function isoDate(date: Date): string {
  return format(date, "yyyy-MM-dd");
}

/** `yyyy-MM-ddTHH:mm` for a `datetime-local` input in local time. */
export function isoLocalDateTime(date: Date): string {
  return format(date, "yyyy-MM-dd'T'HH:mm");
}

/** Two uppercase initials for an avatar bubble. */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
}

/**
 * Format integer money (minor units: paise/cents) with a currency symbol.
 *
 * Every amount in the API is an integer minor unit plus an ISO-4217 currency
 * (`apps.core.money`), so this divides by 100 for the two-decimal currencies the
 * platform uses. An unknown currency falls back to the bare amount plus its code.
 */
export function formatMinorUnits(
  minor: number | null | undefined,
  currency: string | null | undefined,
): string {
  if (minor === null || minor === undefined) return "—";
  const code = (currency ?? "").toUpperCase();
  const symbol = CURRENCY_SYMBOLS[code];
  const amount = (minor / 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  if (symbol) return `${symbol}${amount}`;
  return code ? `${amount} ${code}` : amount;
}

const CURRENCY_SYMBOLS: Record<string, string> = {
  INR: "₹",
  USD: "$",
  EUR: "€",
  GBP: "£",
};

/**
 * Render an analytics metric honouring its declared `unit`.
 *
 * Ratios are shown as percentages so a 0..1 value reads as a rate, matching the
 * `definition` text the API ships alongside it.
 */
export function formatMetricValue(metric: Metric): string {
  const value = metric.value;
  switch (metric.unit) {
    case "ratio":
    case "percent":
      return `${(value * 100).toFixed(1)}%`;
    case "currency": {
      const symbol = metric.currency ? CURRENCY_SYMBOLS[metric.currency] ?? "" : "";
      const prefix = symbol || (metric.currency ? `${metric.currency} ` : "");
      return `${prefix}${value.toLocaleString(undefined, {
        minimumFractionDigits: 0,
        maximumFractionDigits: 2,
      })}`;
    }
    case "minutes":
      return `${value.toLocaleString()} min`;
    case "minor_units":
      return `${value.toLocaleString()}${metric.currency ? ` ${metric.currency}` : ""}`;
    case "count":
      return value.toLocaleString();
    default:
      return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(2);
  }
}
