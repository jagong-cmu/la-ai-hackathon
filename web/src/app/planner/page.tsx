"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, errorText, type ForecastAdjustment, type PlannerCheck, type PlannerView } from "@/api/client";
import { ForecastChart } from "@/components/ForecastChart";
import { StatusChip } from "@/components/StatusChip";

// J11 causes (backend app/judgments.py MISS_CAUSES).
const CAUSES = [
  "one_time_event",
  "lasting_shift_up",
  "lasting_shift_down",
  "customer_lost",
  "customer_gained",
  "supply_shortage",
  "product_substitution",
  "noise",
];

export default function PlannerPage() {
  const [view, setView] = useState<PlannerView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [lastAdjustment, setLastAdjustment] = useState<ForecastAdjustment | null>(null);
  const [check, setCheck] = useState<PlannerCheck | null>(null);

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

  async function answer(missId: string, cause: string) {
    const v = await run(() => api.answerMiss(missId, cause));
    if (v) setView(v);
  }

  async function sendMessage(e: React.FormEvent) {
    e.preventDefault();
    if (!message.trim()) return;
    const adj = await run(() => api.ownerMessage(message.trim()));
    if (adj) {
      setLastAdjustment(adj);
      setMessage("");
      await load();
    }
  }

  async function confirm(adjustmentId: string, accept: boolean) {
    const adj = await run(() => api.confirmAdjustment(adjustmentId, accept));
    if (adj) {
      setLastAdjustment(adj);
      await load();
    }
  }

  async function runCheck(autostart: boolean) {
    const c = await run(() => api.plannerCheck(autostart));
    if (c) setCheck(c);
  }

  if (!view) return <>{error ? <p className="error" role="alert">{error}</p> : <p>Loading planner...</p>}</>;

  const waiting = view.misses.filter((m) => m.status === "needs_owner");
  const other = view.misses.filter((m) => m.status !== "needs_owner");

  return (
    <>
      <h1>Restock planner</h1>
      <p className="muted">
        {view.businessId} - as of {view.asOf}
        {view.cashLowPoint != null && <> - cash low point ${view.cashLowPoint.toLocaleString()}</>}
      </p>
      <p className="disclosure">{view.disclosure}</p>
      {error && <p className="error" role="alert">{error}</p>}

      <h2>Supply plans</h2>
      <div className="row">
        <button disabled={busy} onClick={() => runCheck(false)}>Check what is due</button>
        <button disabled={busy} onClick={() => runCheck(true)}>Check and start sourcing job</button>
      </div>
      {check && (
        <p role="status">
          {check.message} ({check.due.length} due)
          {check.jobId && <> - <Link href={`/jobs/${check.jobId}`}>open job {check.jobId}</Link></>}
        </p>
      )}
      <table>
        <thead>
          <tr>
            <th>Supply</th><th>On hand</th><th>Weekly use (next)</th><th>Recommended qty</th>
            <th>Run-out</th><th>Order by</th><th>Status</th>
          </tr>
        </thead>
        <tbody>
          {view.supplies.map((s) => (
            <tr key={s.itemId}>
              <td>
                {s.name}
                {s.ratiosAssumed !== false && <div className="muted" style={{ fontSize: "0.8em" }}>usage ratios assumed</div>}
              </td>
              <td>{Math.round(s.onHand).toLocaleString()} {s.unit}</td>
              <td>
                {s.weeklyUseForecast[0]
                  ? `${Math.round(s.weeklyUseForecast[0].mean)} (${Math.round(s.weeklyUseForecast[0].low)}-${Math.round(s.weeklyUseForecast[0].high)})`
                  : "-"}
              </td>
              <td>{s.recommendedQty.toLocaleString()} (pack {s.pack})</td>
              <td>{s.runOutDate ?? "-"}</td>
              <td>{s.orderByDate ?? "-"}</td>
              <td><StatusChip status={s.status} /></td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Misses that wait for the owner ({waiting.length})</h2>
      {waiting.length === 0 && <p className="muted">None.</p>}
      {waiting.map((m) => (
        <section key={m.missId} className="card" style={{ marginBottom: 8 }} aria-label={`Miss ${m.itemName} ${m.weekStart}`}>
          <strong>{m.itemName}</strong>, week of {m.weekStart}: actual {m.actual}, range {Math.round(m.low)}-{Math.round(m.high)} ({m.bucket})
          <ul>{m.facts.map((f, i) => <li key={i}>{f}</li>)}</ul>
          <p className="muted">
            Best guess: {m.cause ?? "none"} ({m.band}{m.confidence != null ? `, p=${m.confidence.toFixed(2)}` : ""}, {m.source})
          </p>
          <div className="row" role="group" aria-label="Owner answer">
            {CAUSES.map((c) => (
              <button key={c} disabled={busy} onClick={() => answer(m.missId, c)} aria-pressed={m.cause === c}>
                {c.replaceAll("_", " ")}
              </button>
            ))}
          </div>
        </section>
      ))}

      <h2>Owner message</h2>
      <form onSubmit={sendMessage}>
        <label htmlFor="owner-msg">Message (for example: &quot;wedding Saturday, 300 extra buns&quot;)</label>
        <div className="row">
          <input id="owner-msg" style={{ flex: 1, minWidth: 240 }} value={message} onChange={(e) => setMessage(e.target.value)} />
          <button type="submit" disabled={busy || !message.trim()}>Send</button>
        </div>
      </form>
      {lastAdjustment && (
        <p role="status">
          Result: <StatusChip status={lastAdjustment.status} /> {lastAdjustment.kind}
          {lastAdjustment.itemId && <> for {lastAdjustment.itemId}</>}
          {lastAdjustment.quantity != null && <>, qty {lastAdjustment.quantity}</>}
          {lastAdjustment.startDate && <>, from {lastAdjustment.startDate}</>}
          {lastAdjustment.notes?.length ? <> - {lastAdjustment.notes.join("; ")}</> : null}
        </p>
      )}

      <h2>Adjustments ({view.adjustments.length})</h2>
      {view.adjustments.length === 0 && <p className="muted">None.</p>}
      <ul>
        {view.adjustments.map((a) => (
          <li key={a.adjustmentId}>
            &quot;{a.message}&quot; - {a.kind} {a.itemId ?? ""} {a.quantity ?? ""} <StatusChip status={a.status} />
            {a.status === "needs_confirmation" && (
              <>
                {" "}
                <button disabled={busy} onClick={() => confirm(a.adjustmentId, true)}>Confirm</button>{" "}
                <button disabled={busy} onClick={() => confirm(a.adjustmentId, false)}>Reject</button>
              </>
            )}
          </li>
        ))}
      </ul>

      <h2>Products</h2>
      {view.products.map((p) => (
        <section key={p.itemId} style={{ marginBottom: 16 }}>
          <h3 style={{ margin: "0.4em 0" }}>{p.name}</h3>
          <ForecastChart product={p} />
        </section>
      ))}

      <h2>Other misses ({other.length})</h2>
      <table>
        <thead>
          <tr><th>Product</th><th>Week</th><th>Actual / range</th><th>Cause</th><th>Status</th></tr>
        </thead>
        <tbody>
          {other.map((m) => (
            <tr key={m.missId}>
              <td>{m.itemName}</td>
              <td>{m.weekStart}</td>
              <td>{m.actual} / {Math.round(m.low)}-{Math.round(m.high)}</td>
              <td>{m.cause ?? "-"} <span className="muted">({m.source})</span></td>
              <td>{m.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
