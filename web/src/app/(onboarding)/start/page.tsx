"use client";
// Onboarding steps 1 and 2: connect the books, then choose what to get quotes for.
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { api, errorText, type PlannerView, type ShoppingRequest, type SpendInsights } from "@/api/client";
import { ArrowRight } from "@/components/Icons";
import { day, int, itemShort } from "@/lib/format";
import { Steps } from "../steps";

type Books = { insights: SpendInsights; planner: PlannerView | null; draft: ShoppingRequest };

// The data loads in well under a second; each checklist line stays long enough to read.
const pause = () => new Promise((r) => setTimeout(r, 450));

const plural = (unit: string, n: number) =>
  n === 1 || unit.endsWith("s") ? unit : /(x|ch|sh)$/.test(unit) ? `${unit}es` : `${unit}s`;

function Connect({ onDone }: { onDone: (b: Books) => void }) {
  const [phase, setPhase] = useState<"idle" | "loading" | "done">("idle");
  const [lines, setLines] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function connect() {
    setPhase("loading");
    setError(null);
    setLines(["Connected to QuickBooks"]);
    try {
      const insightsP = api.insights();
      const plannerP = api.planner().catch(() => null);
      const draftP = api.shoppingDraft();
      const insights = await insightsP;
      await pause();
      setLines((l) => [...l, `Read ${int(insights.supplyOrders)} supply bills`]);
      await pause();
      setLines((l) => [...l, `Found ${insights.items.length} supplies you buy`]);
      const [planner, draft] = await Promise.all([plannerP, draftP]);
      await pause();
      const due = planner?.supplies.filter((s) => s.status !== "ok" && s.recommendedQty > 0).length ?? 0;
      setLines((l) => [...l, planner ? `${due} ${due === 1 ? "runs" : "run"} low in the next weeks` : "Forecast not ready"]);
      setPhase("done");
      setTimeout(() => onDone({ insights, planner, draft }), 900);
    } catch (e) {
      setError(errorText(e));
      setPhase("idle");
      setLines([]);
    }
  }

  return (
    <div className="ob-card">
      <Steps at={0} />
      <h1>Connect your books</h1>
      <p className="ob-lead">We read your past supply bills to learn what you buy and what you pay. Nothing is ordered without you.</p>
      {phase === "idle" ? (
        <div className="ob-actions">
          <button className="ob-connect" onClick={connect}>
            <span className="qb" aria-hidden="true">qb</span>
            Connect QuickBooks
            <ArrowRight />
          </button>
          <button className="ob-alt" onClick={connect}>I use other accounting software</button>
        </div>
      ) : (
        <ul className="ob-progress" aria-live="polite">
          {lines.map((l) => (
            <li key={l}><i className="ok" />{l}</li>
          ))}
          {phase === "loading" && <li className="muted"><i className="spin" />Reading</li>}
        </ul>
      )}
      {error && <p className="alert" role="alert">{error}</p>}
      <p className="ob-fine">Demo: this loads the books of Hearthloaf Bakeries from the QuickBooks sample data.</p>
    </div>
  );
}

function Choose({ books }: { books: Books }) {
  const router = useRouter();
  const items = books.draft.items;
  const plans = Object.fromEntries((books.planner?.supplies ?? []).map((s) => [s.itemId, s]));
  const [picked, setPicked] = useState<Set<string>>(() => new Set(items.map((i) => i.id)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = useRef<string | null>(null);
  const all = picked.size === items.length;

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
      const body: ShoppingRequest = { ...books.draft, items: items.filter((i) => picked.has(i.id)) };
      const created = await api.createJob(body, key.current);
      router.push(`/start/${created.jobId}`);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  const sources = books.draft.suppliers.length + (books.draft.distributor ? 1 : 0);
  return (
    <div className="ob-card">
      <Steps at={1} />
      <h1>What should we get quotes for?</h1>
      <p className="ob-lead">These are the supplies you reorder. We set each amount from your sales.</p>
      <div className="ob-seg" role="group" aria-label="Which items">
        <button aria-pressed={all} onClick={() => setPicked(new Set(items.map((i) => i.id)))}>Everything</button>
        <button aria-pressed={!all} onClick={() => all && setPicked(new Set())}>Pick items</button>
      </div>
      <ul className="ob-items">
        {items.map((it) => {
          const s = plans[it.id];
          const on = picked.has(it.id);
          return (
            <li key={it.id}>
              <label className={on ? "on" : undefined}>
                <input type="checkbox" checked={on} onChange={() => toggle(it.id)} />
                <span className="box" aria-hidden="true" />
                <span className="name">
                  {itemShort(it.name)}
                  {s?.runOutDate && <em>Runs out {day(s.runOutDate)}</em>}
                </span>
                <span className="qty tnum">{int(it.quantity)} {plural(it.unit, it.quantity)}</span>
              </label>
            </li>
          );
        })}
      </ul>
      {error && <p className="alert" role="alert">{error}</p>}
      <button className="btn btn-primary has-icon ob-go" disabled={busy || picked.size === 0} onClick={start}>
        {busy ? "Starting" : `Get quotes for ${picked.size} ${picked.size === 1 ? "item" : "items"}`} <ArrowRight />
      </button>
      <p className="ob-fine">Agents check {sources} suppliers at once: {books.draft.suppliers.length} websites and a phone call.</p>
    </div>
  );
}

export default function StartPage() {
  const [books, setBooks] = useState<Books | null>(null);
  return <main className="ob-center">{books ? <Choose books={books} /> : <Connect onDone={setBooks} />}</main>;
}
