// The four price states, each a 14px glyph with one color. The word beside it stays ink-2.
// Every other state is plain text, so the four colors keep one meaning.
export type Tone = "verified" | "quoted" | "estimated" | "failed";

export function toneOf(status: string | null | undefined): Tone | null {
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
      return "failed";
    default:
      return null;
  }
}

export function Glyph({ tone }: { tone: Tone }) {
  return (
    <svg viewBox="0 0 14 14" aria-hidden="true">
      {tone === "verified" && (
        <>
          <rect width="14" height="14" rx="4" fill="currentColor" />
          <path d="M4 7.2l2 2 4-4.4" fill="none" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </>
      )}
      {tone === "quoted" && (
        <>
          <rect x="0.75" y="0.75" width="12.5" height="12.5" rx="3.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M4 0.75h3v12.5H4a3.25 3.25 0 0 1-3.25-3.25V4A3.25 3.25 0 0 1 4 0.75z" fill="currentColor" />
        </>
      )}
      {tone === "estimated" && (
        <rect x="0.75" y="0.75" width="12.5" height="12.5" rx="3.25" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="2.5 2" />
      )}
      {tone === "failed" && (
        <>
          <rect width="14" height="14" rx="4" fill="currentColor" />
          <path d="M4.8 4.8l4.4 4.4M9.2 4.8l-4.4 4.4" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" />
        </>
      )}
    </svg>
  );
}

/** Glyph + word. `tone` overrides the tone derived from `status`. */
export function Status({ status, tone, children }: { status?: string | null; tone?: Tone | null; children?: React.ReactNode }) {
  const t = tone === undefined ? toneOf(status) : tone;
  return (
    <span className={`status${t ? ` st-${t}` : ""}`}>
      {t && <Glyph tone={t} />}
      {children ?? statusWord(status)}
    </span>
  );
}

const WORDS: Record<string, string> = {
  verified: "Cart",
  quoted: "Quote",
  quoted_bundle: "Quote",
  quoted_confirm_by_phone: "Quote",
  estimated: "Estimate",
  failed: "Failed",
  blocked: "Blocked",
};

export function statusWord(status: string | null | undefined): string {
  if (!status) return "";
  return WORDS[status] ?? sentence(status);
}

export function sentence(s: string): string {
  const t = s.replaceAll("_", " ");
  return t.charAt(0).toUpperCase() + t.slice(1);
}
