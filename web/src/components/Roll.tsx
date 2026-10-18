"use client";
import { useEffect, useState } from "react";

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
