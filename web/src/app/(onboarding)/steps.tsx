// The four onboarding steps, shown as a thin progress row at the top of each screen.
const STEPS = ["Connect", "Choose", "Quotes", "Result"];

export const ONBOARDED_KEY = "sourn.onboarded";

export function Steps({ at }: { at: number }) {
  return (
    <ol className="ob-steps" aria-label={`Step ${at + 1} of ${STEPS.length}`}>
      {STEPS.map((s, i) => (
        <li key={s} className={i < at ? "done" : i === at ? "now" : undefined} aria-current={i === at ? "step" : undefined}>
          <i />
          <span>{s}</span>
        </li>
      ))}
    </ol>
  );
}
