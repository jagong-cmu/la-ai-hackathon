"use client";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, errorText, type ForecastAdjustment, type PlannerView, type SupplyPlan } from "@/api/client";
import { ForecastChart } from "@/components/ForecastChart";
import { ArrowRight } from "@/components/Icons";
import { ErrorNote, Loading, PageHead } from "@/components/PageHead";
import { day, daysBetween, int, itemShort, weekday } from "@/lib/format";

// J11 causes (backend app/judgments.py MISS_CAUSES), in the words an owner uses.
const CAUSES: [string, string][] = [
  ["customer_lost", "Lost a customer"],
  ["customer_gained", "New customer"],
  ["product_substitution", "Customers switched product"],
  ["one_time_event", "One-time event"],
  ["supply_shortage", "We ran short"],
  ["lasting_shift_up", "Lasting rise"],
  ["lasting_shift_down", "Lasting drop"],
  ["noise", "Normal ups and downs"],
];
const CAUSE_WORD = Object.fromEntries(CAUSES);
const RUNWAY_DAYS = 42; // a full bar = six weeks of stock

function OrderCell({ s }: { s: SupplyPlan }) {
  if (s.status === "ok" || s.recommendedQty <= 0) return <span className="muted">Enough</span>;
  const when = s.status === "order_now" ? "order now" : `by ${day(s.orderByDate)}`;
  return <span className="strong">{int(s.recommendedQty)} · {when}</span>;
}

function adjustmentText(a: ForecastAdjustment, names: Record<string, string>): string {
  const qty = a.quantity != null ? `${a.quantity > 0 ? "+" : ""}${int(a.quantity)}` : "";
  const what = a.itemId ? itemShort(names[a.itemId] ?? a.itemId).toLowerCase() : "";
  const when = a.startDate ? `${weekday(a.startDate)}${a.kind === "one_time_extra" ? " only" : ""}` : "";
  return [[qty, what].filter(Boolean).join(" "), when].filter(Boolean).join(" · ") || a.message;
}

export default function PlannerPage() {
  const [view, setView] = useState<PlannerView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [adj, setAdj] = useState<ForecastAdjustment | null>(null);
  const [q, setQ] = useState(0);
  const [chartItem, setChartItem] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setView(await api.planner());
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const names = useMemo(() => Object.fromEntries((view?.products ?? []).map((p) => [p.itemId, p.name])), [view]);

  if (!view) return error ? <ErrorNote error={error} /> : <Loading label="Loading the planner" />;

  const waiting = view.misses.filter((m) => m.status === "needs_owner");
  const miss = waiting[Math.min(q, waiting.length - 1)];
  const due = view.supplies.filter((s) => s.status !== "ok" && s.recommendedQty > 0);
  const supplies = [...view.supplies].sort((a, b) => (a.runOutDate ?? "9").localeCompare(b.runOutDate ?? "9"));
  const product = view.products.find((p) => p.itemId === (chartItem ?? miss?.itemId)) ?? view.products[0];

  async function answer(cause: string) {
    if (!miss) return;
    const v = await run(() => api.answerMiss(miss.missId, cause));
    if (v) setView(v);
  }
  async function addNote(e: React.FormEvent) {
    e.preventDefault();
    if (!note.trim()) return;
    const a = await run(() => api.ownerMessage(note.trim()));
    if (a) {
      setAdj(a);
      setNote("");
      await load();
    }
  }
  async function confirm(accept: boolean) {
    if (!adj) return;
    const a = await run(() => api.confirmAdjustment(adj.adjustmentId, accept));
    if (a) {
      setAdj(a);
      await load();
    }
  }

  return (
    <>
      <PageHead
        title="Planner"
        meta={`As of ${weekday(view.asOf)}`}
        right={
          due.length > 0 && (
            <Link href="/orders/new" className="btn btn-primary has-icon">
              Find best price for {due.length} <ArrowRight />
            </Link>
          )
        }
      />
      <ErrorNote error={error} />

      <div className="tbl-wrap">
        <table className="tbl wide">
          <thead>
            <tr>
              <th>Supply</th>
              <th>On hand</th>
              <th>Use a week</th>
              <th className="l" style={{ width: 240 }}>Runs out</th>
              <th>Order</th>
            </tr>
          </thead>
          <tbody>
            {supplies.map((s) => {
              const left = s.runOutDate ? daysBetween(view.asOf, s.runOutDate) : RUNWAY_DAYS;
              const ok = s.status === "ok";
              return (
                <tr key={s.itemId} className={ok ? "dim" : undefined}>
                  <td style={{ color: ok ? "var(--color-ink-2)" : undefined }}>{s.name}</td>
                  <td>{int(s.onHand)}</td>
                  <td>~{int(s.weeklyUseForecast[0]?.mean)}</td>
                  <td className="l">
                    <span className="row" style={{ gap: 12, flexWrap: "nowrap" }}>
                      <span className={`bar${ok ? " soft" : ""}`} style={{ width: 100, flexShrink: 0 }}>
                        <i style={{ width: `${Math.max(4, Math.min(100, (left / RUNWAY_DAYS) * 100))}%` }} />
                      </span>
                      <span className={ok ? undefined : "strong"} style={{ color: ok ? "var(--color-muted)" : undefined }}>
                        {day(s.runOutDate)}
                      </span>
                    </span>
                  </td>
                  <td><OrderCell s={s} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="split">
        {miss ? (
          <section className="card pad" aria-label="Forecast question">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="label">Forecast question</span>
              {waiting.length > 1 && (
                <span className="row small muted" style={{ gap: 4 }}>
                  <button className="btn btn-ghost sm" disabled={q === 0} onClick={() => setQ(q - 1)} aria-label="Previous question">‹</button>
                  {Math.min(q, waiting.length - 1) + 1} of {waiting.length}
                  <button className="btn btn-ghost sm" disabled={q >= waiting.length - 1} onClick={() => setQ(q + 1)} aria-label="Next question">›</button>
                </span>
              )}
            </div>
            <div>
              <p style={{ fontWeight: 600, fontSize: 18, lineHeight: "24px" }}>
                Week of {day(miss.weekStart)} · {itemShort(miss.itemName).toLowerCase()}
              </p>
              <p className="ink-2 tnum" style={{ marginTop: 4 }}>
                {int(miss.actual)} sold · expected {int(miss.low)}–{int(miss.high)}
              </p>
            </div>
            {miss.facts.length > 0 && (
              <ul className="small ink-2" style={{ margin: 0, paddingLeft: 0, listStyle: "none", display: "grid", gap: 4 }}>
                {miss.facts.map((f, i) => <li key={i}>{f}</li>)}
              </ul>
            )}
            {miss.cause && <p className="small muted">Best guess: {(CAUSE_WORD[miss.cause] ?? miss.cause).toLowerCase()}</p>}
            <div className="row" style={{ gap: 8 }} role="group" aria-label="Your answer">
              {CAUSES.map(([c, label]) => (
                <button key={c} className="chip-btn" disabled={busy} onClick={() => answer(c)}>
                  {label}
                </button>
              ))}
            </div>
          </section>
        ) : (
          <section className="card pad">
            <span className="label">Forecast questions</span>
            <p className="ink-2">None open</p>
          </section>
        )}

        <section className="card pad" aria-label="Add a note">
          <span className="label">Add a note</span>
          <form className="row" style={{ gap: 8, flexWrap: "nowrap" }} onSubmit={addNote}>
            <label className="sr-only" htmlFor="note">Note</label>
            <input
              id="note"
              className="input"
              style={{ flex: 1 }}
              placeholder="Wedding Saturday, 300 extra buns"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <button className="btn btn-secondary" type="submit" disabled={busy || !note.trim()}>Add</button>
          </form>
          {adj && (
            <div className="panel" aria-live="polite">
              <p className="strong">{adjustmentText(adj, names)}</p>
              {adj.status === "needs_confirmation" ? (
                <div className="row" style={{ gap: 8, marginTop: 12 }}>
                  <button className="btn btn-secondary sm" disabled={busy} onClick={() => confirm(true)}>Use it</button>
                  <button className="btn btn-ghost sm" disabled={busy} onClick={() => confirm(false)}>Not right</button>
                </div>
              ) : (
                <p className="small muted" style={{ marginTop: 4 }}>
                  {adj.status === "applied" ? "Added to the forecast" : adj.status === "rejected" ? "Not used" : "No change to the forecast"}
                </p>
              )}
            </div>
          )}
          {view.adjustments.length > 0 && (
            <ul className="small ink-2" style={{ margin: 0, padding: 0, listStyle: "none", display: "grid", gap: 6 }}>
              {view.adjustments.filter((a) => a.status === "applied").map((a) => (
                <li key={a.adjustmentId}>{adjustmentText(a, names)}</li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {product && (
        <section className="card pad" aria-label="Weekly sales">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <label className="row" style={{ gap: 8 }}>
              <span className="sr-only">Product</span>
              <select
                className="input"
                style={{ height: 36, fontWeight: 600 }}
                value={product.itemId}
                onChange={(e) => setChartItem(e.target.value)}
              >
                {view.products.map((p) => <option key={p.itemId} value={p.itemId}>{p.name}</option>)}
              </select>
              <span className="small muted">weekly sales</span>
            </label>
            <span className="small muted">Actual · Expected range · Miss</span>
          </div>
          <ForecastChart product={product} />
        </section>
      )}
    </>
  );
}
