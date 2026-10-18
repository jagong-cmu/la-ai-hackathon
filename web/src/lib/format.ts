// Display helpers shared by every screen.

/** The demo business. The backend config holds the same values (backend/app/config.py). */
export const BUSINESS = { name: "Hearthloaf Bakeries", place: "Kalamazoo · 49007" };

/** "Alpha Packaging (fixture)" -> "Alpha Packaging". The run mode shows on Admin instead. */
export function cleanName(name: string | null | undefined): string {
  if (!name) return "";
  return name.replace(/\s*\((fixture|fictional|demo)[^)]*\)\s*$/i, "").trim();
}

/** "Larkmoor Foodservice Supply" -> "Larkmoor". For tight column headers. */
export function shortName(name: string | null | undefined): string {
  return cleanName(name).split(" ")[0] ?? "";
}

export function host(url: string | null | undefined): string {
  if (!url) return "";
  try {
    const u = new URL(url);
    return u.host + (u.pathname === "/" ? "" : u.pathname);
  } catch {
    return url;
  }
}

const DAY = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const WEEKDAY = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
const TIME = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" });

function parse(d: string): Date {
  // "2026-10-05" is a calendar date: read it as UTC so it never shifts a day.
  return new Date(/^\d{4}-\d{2}-\d{2}$/.test(d) ? d + "T00:00:00Z" : d);
}

/** "Oct 17" */
export const day = (d: string | null | undefined) => (d ? DAY.format(parse(d)) : "");
/** "Mon, Oct 5" */
export const weekday = (d: string | null | undefined) => (d ? WEEKDAY.format(parse(d)) : "");
/** "9:13 AM" in the viewer's time zone (timestamps only). */
export const clock = (ts: string | null | undefined) => (ts ? TIME.format(new Date(ts)) : "");
/** Local calendar day of a timestamp, "Oct 5". */
export const tsDay = (ts: string | null | undefined) =>
  ts ? new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(new Date(ts)) : "";

export const int = (n: number | null | undefined) => (n == null ? "" : Math.round(n).toLocaleString("en-US"));

/** Whole days from a to b (calendar dates). */
export function daysBetween(a: string, b: string): number {
  return Math.round((parse(b).getTime() - parse(a).getTime()) / 86_400_000);
}

/** "1:12" from seconds. */
export function mmss(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Short product name: the part before the first comma, in sentence case. */
export function itemShort(name: string | null | undefined): string {
  if (!name) return "";
  return name.split(",")[0].trim();
}

/** Unit price: cents need a third decimal below $1 ("$0.146"), else two. */
export function each(n: number | null | undefined): string {
  if (n == null) return "";
  return n < 1 ? `$${n.toFixed(3)}` : `$${n.toFixed(2)}`;
}
