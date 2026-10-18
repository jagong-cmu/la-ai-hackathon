"use client";
// Onboarding steps 1 and 2: connect the books, then choose what to price.
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { api, errorText, money, type BillLine, type PlannerView, type ShoppingRequest, type SpendInsights } from "@/api/client";
import { ArrowRight } from "@/components/Icons";
import { day, each, int, itemShort, shortName } from "@/lib/format";
import { Claim, exitPause, In, Steps } from "../steps";

type Books = { insights: SpendInsights; planner: PlannerView | null; draft: ShoppingRequest };

const plural = (unit: string, n: number) =>
  n === 1 || unit.endsWith("s") ? unit : /(x|ch|sh)$/.test(unit) ? `${unit}es` : `${unit}s`;

const Check = () => (
  <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M3 7.4l2.6 2.6L11 4.5" />
  </svg>
);

// Reading the books: the real bill lines stream in, then the supplies they form, then the forecast.
const ROW_MS = 45; // 58 bill lines take about 2.6 s
const FEED_ROWS = 6;
const PLANNER_WAIT_MS = 6000; // the first planner build can take a minute; the forecast is optional here
const CATEGORY: Record<string, string> = {
  food_ingredient: "Ingredient", packaging_disposables: "Packaging", cleaning_safety: "Cleaning",
  smallwares: "Smallwares", equipment: "Equipment", service: "Service", other: "Other",
};
const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

type Read = { insights: SpendInsights; bills: BillLine[]; shown: number; stage: "bills" | "supplies" | "forecast"; forecast: string };

function Connect({ onDone }: { onDone: (b: Books) => void }) {
  const [phase, setPhase] = useState<"idle" | "loading" | "reading" | "leaving">("idle");
  const [read, setRead] = useState<Read | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function connect() {
    setPhase("loading");
    setError(null);
    try {
      const plannerP = api.planner().catch(() => null);
      const draftP = api.shoppingDraft();
      const [insights, bills] = await Promise.all([api.insights(), api.bills()]);
      const fast = reducedMotion();
      setRead({ insights, bills, shown: fast ? bills.length : 0, stage: "bills", forecast: "" });
      setPhase("reading");
      for (let k = 1; !fast && k <= bills.length; k++) {
        await wait(ROW_MS);
        setRead((r) => r && { ...r, shown: k });
      }
      setRead((r) => r && { ...r, stage: "supplies" });
      await wait(fast ? 0 : 400 + insights.items.length * 250);
      const [planner, draft] = await Promise.all([Promise.race([plannerP, wait(PLANNER_WAIT_MS).then(() => null)]), draftP]);
      const due = planner?.supplies.filter((s) => s.status !== "ok" && s.recommendedQty > 0) ?? [];
      const first = due.map((s) => s.runOutDate).filter(Boolean).sort()[0];
      const forecast = !planner ? "Forecast not ready yet" : due.length ? `${due.length} run low · first on ${day(first)}` : "Nothing runs low soon";
      setRead((r) => r && { ...r, stage: "forecast", forecast });
      await wait(fast ? 300 : 1400);
      setPhase("leaving");
      await exitPause();
      onDone({ insights, planner, draft });
    } catch (e) {
      setError(errorText(e));
      setPhase("idle");
      setRead(null);
    }
  }

  return (
    <main className={`ob-stage${phase === "leaving" ? " ob-out" : ""}`}>
      <In i={0}><Steps at={0} /></In>
      <Claim text="Connect your books" start={100} />
      {read ? (
        <Reading read={read} />
      ) : (
        <In i={5} className="ob-row">
          <button className="btn btn-primary has-icon" onClick={connect} disabled={phase !== "idle"} aria-live="polite">
            {phase === "idle" ? "Connect QuickBooks" : "Connecting to QuickBooks"} <ArrowRight />
          </button>
          <button className="btn btn-on-black" onClick={connect} disabled={phase !== "idle"}>Connect other books</button>
        </In>
      )}
      {error && <p className="alert" role="alert">{error}</p>}
    </main>
  );
}

function Reading({ read }: { read: Read }) {
  const { insights, bills, shown, stage } = read;
  const names = Object.fromEntries(insights.items.map((i) => [i.itemId, i.billItemName]));
  const done = bills.slice(0, shown);
  const spend = done.reduce((n, l) => n + l.amount, 0);
  const billCount = new Set(done.map((l) => l.billId)).size;
  const feed = done.slice(-FEED_ROWS);
  const status = stage === "bills"
    ? (shown < bills.length ? `Reading ${bills[shown]?.billId ?? ""}` : "All bills read")
    : stage === "supplies" ? `Sorting ${int(bills.length)} lines into ${insights.items.length} supplies` : read.forecast;
  return (
    <>
      <In i={0} className="ob-device ob-read">
        <div className="ob-screen">
          <div className="ob-screen-head">
            <h2>QuickBooks bills</h2>
            <dl className="ob-read-stats" aria-live="off">
              <div><dt>Bills</dt><dd>{int(billCount)} / {int(insights.supplyOrders)}</dd></div>
              <div><dt>Supply spend</dt><dd>{money(spend)}</dd></div>
              <div><dt>Dates</dt><dd>{done.length ? `${day(done[0].txnDate)} – ${day(done[done.length - 1].txnDate)}` : "–"}</dd></div>
            </dl>
          </div>
          {stage === "bills" ? (
            <div className="ob-feed">
              <table className="tbl wide">
                <thead>
                  <tr><th className="l">Date</th><th className="l wide-only">Bill</th><th className="l wide-only">Vendor</th><th className="l">Supply</th><th>Qty · each</th><th>Total</th></tr>
                </thead>
                <tbody>
                  {feed.map((l, k) => (
                    <tr key={`${l.billId}-${l.itemId}`} className={k === feed.length - 1 ? "ob-in ob-new" : undefined}>
                      <td className="l">{day(l.txnDate)}</td>
                      <td className="l muted wide-only">{l.billId}</td>
                      <td className="l wide-only">{shortName(l.vendorName)}</td>
                      <td className="l strong">{itemShort(names[l.itemId] ?? l.itemId)}</td>
                      <td>{int(l.quantity)} · {each(l.unitPrice)}</td>
                      <td>{money(l.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="ob-feed">
              <table className="tbl wide">
                <thead>
                  <tr><th className="l">Supply</th><th className="l">Category</th><th>Orders</th><th>Paid each</th><th>Year spend</th></tr>
                </thead>
                <tbody>
                  {insights.items.map((it, k) => {
                    const p = (it.jev as { categoryProbs?: Record<string, number> }).categoryProbs?.[it.category];
                    return (
                      <tr key={it.itemId} className="ob-in" style={{ "--d": `${k * 250}ms` } as React.CSSProperties}>
                        <td className="l strong">{itemShort(it.billItemName)}</td>
                        <td className="l">
                          <span className="ob-chip">{CATEGORY[it.category] ?? it.category}</span>
                          {p != null && <span className="muted"> Jev {Math.round(p * 100)}%</span>}
                        </td>
                        <td>{int(it.orders)}</td>
                        <td className="muted">{each(it.pastUnitPrice)}</td>
                        <td className="best">{money(it.yearSpend)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <div className="ob-screen-foot">
            <p className="sum" aria-live="polite">{status}</p>
            <span className="ob-bar" aria-hidden="true">
              <i style={{ transform: `scaleX(${stage === "bills" ? shown / Math.max(1, bills.length) : 1})` }} />
            </span>
          </div>
        </div>
      </In>
      {insights.disclosure && <In i={2} as="p" className="ob-note">{insights.disclosure}</In>}
    </>
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

  return (
    <main className={`ob-stage${leaving ? " ob-out" : ""}`}>
      <In i={0}><Steps at={1} /></In>
      <Claim text="Pick what to price" start={100} />
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
