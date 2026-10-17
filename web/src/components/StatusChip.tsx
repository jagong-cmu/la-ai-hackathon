// One color per state on every screen: verified (green), quoted (amber),
// estimated (blue-grey), blocked or failed (red). Every other status is neutral grey,
// so the four colors keep one meaning.
export type StatusTone = "verified" | "quoted" | "estimated" | "blocked" | "neutral";

export function statusTone(status: string | null | undefined): StatusTone {
  switch (status) {
    case "verified":
      return "verified";
    case "quoted":
    case "quoted_bundle":
    case "quoted_confirm_by_phone":
      return "quoted";
    case "estimated":
      return "estimated";
    case "blocked":
    case "failed":
      return "blocked";
    default:
      return "neutral";
  }
}

export function StatusChip({ status, label }: { status: string | null | undefined; label?: string }) {
  return <span className={`chip ${statusTone(status)}`}>{label ?? status ?? "unknown"}</span>;
}
