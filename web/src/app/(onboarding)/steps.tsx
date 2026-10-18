"use client";
// Shared pieces of the onboarding: the progress bars, staged entrances and the odometer.
import { useEffect, useState } from "react";

const reduced = () => typeof window === "undefined" || window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const STEPS = ["Connect", "Choose", "Quotes", "Result"];

export const ONBOARDED_KEY = "sourn.onboarded";

export function Steps({ at }: { at: number }) {
  return (
    <ol className="ob-steps" aria-label={`Step ${at + 1} of ${STEPS.length}: ${STEPS[at]}`}>
      {STEPS.map((s, i) => (
        <li key={s} className={i < at ? "done" : i === at ? "now" : undefined} aria-current={i === at ? "step" : undefined}>
          <i />
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

/** An odometer: each digit is a 0-9 column that slides to its value (400ms). A new value
 *  retargets the transition mid-flight. `fromZero` starts every digit at 0, so a mount rolls in. */
export function Roll({ text, fromZero = false }: { text: string; fromZero?: boolean }) {
  const [cur, setCur] = useState(fromZero ? text.replace(/\d/g, "0") : text);
  useEffect(() => {
    const t = requestAnimationFrame(() => setCur(text));
    return () => cancelAnimationFrame(t);
  }, [text]);
  const chars = cur.padStart(text.length, " ").split("");
  return (
    <span className="roll" aria-label={text}>
      {chars.map((c, i) => {
        const key = chars.length - i; // stable from the right, so cents keep their column
        if (!/\d/.test(c)) return <span key={key} aria-hidden="true" className="roll-ch">{c === " " ? "" : c}</span>;
        return (
          <span key={key} aria-hidden="true" className="roll-col">
            <span className="roll-strip" style={{ transform: `translateY(${-Number(c) * 10}%)` }}>
              {"0123456789".split("").map((d) => <span key={d}>{d}</span>)}
            </span>
          </span>
        );
      })}
    </span>
  );
}

/** Wait for an exit animation (150ms) before the next screen mounts. */
export const exitPause = () => new Promise<void>((r) => setTimeout(r, 150));
