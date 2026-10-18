"use client";
// Onboarding steps 1 and 2: connect the books, then choose what to price.
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { api, errorText, money, type PlannerView, type ShoppingRequest, type SpendInsights } from "@/api/client";
import { ArrowRight } from "@/components/Icons";
import { day, each, int, itemShort } from "@/lib/format";
import { Claim, exitPause, In, Steps } from "../steps";

type Books = { insights: SpendInsights; planner: PlannerView | null; draft: ShoppingRequest };

const plural = (unit: string, n: number) =>
  n === 1 || unit.endsWith("s") ? unit : /(x|ch|sh)$/.test(unit) ? `${unit}es` : `${unit}s`;

const Check = () => (
  <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M3 7.4l2.6 2.6L11 4.5" />
  </svg>
);

function Connect({ onDone }: { onDone: (b: Books) => void }) {
  const [phase, setPhase] = useState<"idle" | "loading" | "leaving">("idle");
  const [error, setError] = useState<string | null>(null);

  async function connect() {
    setPhase("loading");
    setError(null);
    try {
      const [insights, planner, draft] = await Promise.all([api.insights(), api.planner().catch(() => null), api.shoppingDraft()]);
      setPhase("leaving");
      await exitPause();
      onDone({ insights, planner, draft });
    } catch (e) {
      setError(errorText(e));
      setPhase("idle");
    }
  }

  const busy = phase !== "idle";
  return (
    <main className={`ob-stage${phase === "leaving" ? " ob-out" : ""}`}>
      <In i={0}><Steps at={0} /></In>
      <Claim text="Connect your books" start={100} />
      <In i={4} as="p" className="ob-sub">Your supply bills set the prices to beat.</In>
      <In i={5} className="ob-row">
        <button className="btn btn-primary has-icon" onClick={connect} disabled={busy} aria-live="polite">
          {busy ? "Reading your bills" : "Connect QuickBooks"} <ArrowRight />
        </button>
        <button className="btn btn-on-black" onClick={connect} disabled={busy}>Connect other books</button>
      </In>
      {error && <p className="alert" role="alert">{error}</p>}
    </main>
  );
}

function Choose({ books }: { books: Books }) {
  const router = useRouter();
  const items = books.draft.items;
  const plans = Object.fromEntries((books.planner?.supplies ?? []).map((s) => [s.itemId, s]));
  const [picked, setPicked] = useState<Set<string>>(() => new Set(items.map((i) => i.id)));
  const [busy, setBusy] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = useRef<string | null>(null);
  const all = picked.size === items.length;
  const chosen = items.filter((i) => picked.has(i.id));
  const atBills = chosen.reduce((n, i) => n + i.quantity * (i.pastUnitPrice ?? 0), 0);

  function toggle(id: string) {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setPicked(next);
    key.current = null;
  }

  async function start() {
    setBusy(true);
    setError(null);
    key.current ??= crypto.randomUUID();
    try {
      const created = await api.createJob({ ...books.draft, items: chosen }, key.current);
      setLeaving(true);
      await exitPause();
      router.push(`/start/${created.jobId}`);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  const sites = books.draft.suppliers.length;
  return (
    <main className={`ob-stage${leaving ? " ob-out" : ""}`}>
      <In i={0}><Steps at={1} /></In>
      <Claim text="Pick what to price" start={100} />
      <In i={4} as="p" className="ob-sub">
        {int(books.insights.supplyOrders)} bills read · {sites} websites{books.draft.distributor ? " and one call" : ""}
      </In>
      <In i={5} className="ob-device">
        <div className="ob-screen">
          <div className="ob-screen-head">
            <h2>Supplies</h2>
            <div className="seg" role="group" aria-label="Which supplies">
              <button aria-pressed={all} onClick={() => setPicked(new Set(items.map((i) => i.id)))}>Everything</button>
              <button aria-pressed={!all} onClick={() => all && setPicked(new Set())}>Pick</button>
            </div>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table className="tbl wide ob-pick">
              <thead>
                <tr>
                  <th className="chk"><span className="sr-only">Price it</span></th>
                  <th className="l">Supply</th>
                  <th className="l">Runs out</th>
                  <th>Amount</th>
                  <th>Paid each</th>
                  <th>At bill price</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it, k) => {
                  const on = picked.has(it.id);
                  const s = plans[it.id];
                  return (
                    <tr key={it.id} className={`ob-in ${on ? "on" : "off"}`} style={{ "--d": `${600 + k * 100}ms` } as React.CSSProperties} onClick={() => toggle(it.id)}>
                      <td className="chk">
                        <span className="ob-check">
                          <input type="checkbox" checked={on} onChange={() => toggle(it.id)} onClick={(e) => e.stopPropagation()} aria-label={`Price ${itemShort(it.name).toLowerCase()}`} />
                          <Check />
                        </span>
                      </td>
                      <td className="l">
                        <span className="strong">{itemShort(it.name)}</span>
                      </td>
                      <td className="l">{s?.runOutDate ? day(s.runOutDate) : "–"}</td>
                      <td>{int(it.quantity)} {plural(it.unit, it.quantity)}</td>
                      <td className="muted">{each(it.pastUnitPrice)}</td>
                      <td className="best">{it.pastUnitPrice != null ? money(it.quantity * it.pastUnitPrice) : "–"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="ob-screen-foot">
            <p className="sum">
              At bill prices <b>{money(atBills)}</b>
            </p>
            <button className="btn btn-primary has-icon" disabled={busy || chosen.length === 0} onClick={start}>
              {busy ? "Starting" : `Get quotes for ${chosen.length} ${chosen.length === 1 ? "supply" : "supplies"}`} <ArrowRight />
            </button>
          </div>
        </div>
      </In>
      {error && <p className="alert" role="alert">{error}</p>}
    </main>
  );
}

export default function StartPage() {
  const [books, setBooks] = useState<Books | null>(null);
  return books ? <Choose books={books} /> : <Connect onDone={setBooks} />;
}
