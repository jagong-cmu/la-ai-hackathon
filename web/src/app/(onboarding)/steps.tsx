"use client";
// Shared pieces of the onboarding: the progress bars, staged entrances and the count-up.
import { useEffect, useState } from "react";

const STEPS = ["Connect", "Choose", "Quotes", "Result"];

export const ONBOARDED_KEY = "sourn.onboarded";

export function Steps({ at }: { at: number }) {
  return (
    <ol className="ob-steps" aria-label={`Step ${at + 1} of ${STEPS.length}: ${STEPS[at]}`}>
      {STEPS.map((s, i) => (
        <li key={s} className={i < at ? "done" : i === at ? "now" : undefined} aria-current={i === at ? "step" : undefined}>
          <i />
          <span>{s}</span>
        </li>
      ))}
    </ol>
  );
}

/** One chunk of a staged entrance; `i` is its place in the sequence (100ms apart). */
export function In({ i = 0, as: Tag = "div", className = "", children, ...rest }: {
  i?: number;
  as?: "div" | "p" | "h1" | "section" | "ul";
  className?: string;
  children: React.ReactNode;
} & React.HTMLAttributes<HTMLElement>) {
  return (
    <Tag className={`ob-in ${className}`} style={{ "--d": `${i * 100}ms` } as React.CSSProperties} {...rest}>
      {children}
    </Tag>
  );
}

/** A display claim that enters word by word, 80ms apart. Screen readers get the whole line. */
export function Claim({ text, start = 0 }: { text: string; start?: number }) {
  const words = text.split(" ");
  return (
    <h1 className="ob-claim" aria-label={text}>
      {words.map((w, k) => (
        <span key={k} aria-hidden="true" className="ob-word ob-in" style={{ "--d": `${start + k * 80}ms` } as React.CSSProperties}>
          {w}{k < words.length - 1 ? " " : ""}
        </span>
      ))}
    </h1>
  );
}

/** Counts from 0 to `to` once (700ms, ease-out). Reduced motion shows the value at once. */
export function useCountUp(to: number, delay = 0): number {
  const [v, setV] = useState(0);
  useEffect(() => {
    if (typeof window === "undefined" || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setV(to);
      return;
    }
    let raf = 0;
    const t0 = performance.now() + delay;
    const tick = (now: number) => {
      const p = Math.min(1, Math.max(0, (now - t0) / 700));
      setV(to * (1 - Math.pow(1 - p, 3)));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [to, delay]);
  return v;
}

/** Wait for an exit animation (150ms) before the next screen mounts. */
export const exitPause = () => new Promise<void>((r) => setTimeout(r, 150));
