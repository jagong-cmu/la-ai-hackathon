"use client";
// One supply in depth: its use and demand forecast, the products that drive it, the prices on the
// QuickBooks bills, the quotes the agents found, and the owner's notes.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  errorText,
  money,
  type ForecastAdjustment,
  type HistoryPoint,
  type PlannerView,
  type SupplyPlan,
} from "@/api/client";
import { ForecastChart } from "@/components/ForecastChart";
import { ArrowRight } from "@/components/Icons";
import { ErrorNote, Loading, PageHead } from "@/components/PageHead";
import { PriceHistoryChart } from "@/components/PriceHistoryChart";
import { Status } from "@/components/Status";
import { useBills, useJobs } from "@/hooks/useData";
import { cleanName, day, daysBetween, dollars, each, int, itemShort, tsDay, weekday } from "@/lib/format";
import { jobHref } from "@/lib/job";
import { bestQuote, itemQuotes, meanPaid, pct } from "@/lib/savings";

/** Weekly supply use in the past: the driver products' real sales times their usage ratios. */
function usageHistory(view: PlannerView, s: SupplyPlan): HistoryPoint[] {
  const weeks = new Map<string, { actual: number; low: number; high: number; drivers: number; banded: number }>();
  const drivers = Object.entries(s.drivers);
  for (const [pid, ratio] of drivers) {
    const p = view.products.find((x) => x.itemId === pid);
    for (const h of p?.history ?? []) {
      const w = weeks.get(h.weekStart) ?? { actual: 0, low: 0, high: 0, drivers: 0, banded: 0 };
      w.actual += h.actual * ratio;
      w.drivers += 1;
      if (h.low != null && h.high != null) {
        w.low += h.low * ratio;
        w.high += h.high * ratio;
        w.banded += 1;
      }
      weeks.set(h.weekStart, w);
    }
  }
  return [...weeks.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([weekStart, w]) => ({
      weekStart,
      actual: w.actual,
      ...(w.banded === w.drivers ? { low: w.low, high: w.high } : {}),
      miss: false,
    }));
}

const UNIT_PLURAL = (u: string) => (u.endsWith("s") ? u : /(x|ch|sh)$/.test(u) ? `${u}es` : `${u}s`);

function adjustmentText(a: ForecastAdjustment, names: Record<string, string>): string {
  const qty = a.quantity != null ? `${a.quantity > 0 ? "+" : ""}${int(a.quantity)}` : "";
  const what = a.itemId ? itemShort(names[a.itemId] ?? a.itemId).toLowerCase() : "";
  const when = a.startDate ? `${weekday(a.startDate)}${a.kind === "one_time_extra" ? " only" : ""}` : "";
  return [[qty, what].filter(Boolean).join(" "), when].filter(Boolean).join(" · ") || a.message;
}

function Notes({ view, supply, onChange }: { view: PlannerView; supply: SupplyPlan; onChange: () => Promise<void> }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [last, setLast] = useState<ForecastAdjustment | null>(null);
  const names = Object.fromEntries(view.products.map((p) => [p.itemId, p.name]));
  const drivers = new Set(Object.keys(supply.drivers));
  const notes = view.adjustments.filter((a) => a.itemId && drivers.has(a.itemId) && a.status !== "ignored");

  async function run(fn: () => Promise<ForecastAdjustment>) {
    setBusy(true);
    setError(null);
    try {
      setLast(await fn());
      await onChange();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card pad" aria-label="Notes">
      <div>
        <h2>Notes</h2>
        <p className="small muted" style={{ marginTop: 2 }}>
          Tell the planner about events that change sales. A note about {[...drivers].map((d) => itemShort(names[d] ?? d).toLowerCase()).slice(0, 2).join(" or ")} changes this forecast.
        </p>
      </div>
      <form
        className="row"
        style={{ gap: 8, flexWrap: "nowrap" }}
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) void run(() => api.ownerMessage(text.trim())).then(() => setText(""));
        }}
      >
        <label className="sr-only" htmlFor="note">Note</label>
        <input id="note" className="input" style={{ flex: 1 }} placeholder="Wedding Saturday, 300 extra buns" value={text} onChange={(e) => setText(e.target.value)} />
        <button className="btn btn-secondary" type="submit" disabled={busy || !text.trim()}>Add note</button>
      </form>
      <ErrorNote error={error} />
      {last && (
        <div className="panel" aria-live="polite">
          <p className="strong">{adjustmentText(last, names)}</p>
          {last.status === "needs_confirmation" ? (
            <div className="row" style={{ gap: 8, marginTop: 12 }}>
              <button className="btn btn-secondary sm" disabled={busy} onClick={() => run(() => api.confirmAdjustment(last.adjustmentId, true))}>Use it</button>
              <button className="btn btn-ghost sm" disabled={busy} onClick={() => run(() => api.confirmAdjustment(last.adjustmentId, false))}>Not right</button>
            </div>
          ) : (
            <p className="small muted" style={{ marginTop: 4 }}>
              {last.status === "applied" ? "Added to the forecast" : last.status === "rejected" ? "Not used" : "No change to the forecast"}
            </p>
          )}
        </div>
      )}
      {notes.length > 0 ? (
        <ul className="notes">
          {notes.map((a) => (
            <li key={a.adjustmentId}>
              <p>{a.message}</p>
              <span className="small muted">{adjustmentText(a, names)} · {a.status === "applied" ? "in the forecast" : a.status === "rejected" ? "not used" : "waiting for you"}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="small muted">No notes for this supply yet.</p>
      )}
    </section>
  );
}

export default function SupplyPage() {
  const { itemId } = useParams<{ itemId: string }>();
  const id = decodeURIComponent(itemId);
  const [view, setView] = useState<PlannerView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bills = useBills(id);
  const jobs = useJobs();

  const load = useCallback(async () => {
    try {
      setView(await api.planner());
    } catch (e) {
      setError(errorText(e));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const supply = view?.supplies.find((s) => s.itemId === id);
  const history = useMemo(() => (view && supply ? usageHistory(view, supply) : []), [view, supply]);

  if (!view) return error ? <ErrorNote error={error} /> : <Loading label="Loading the supply" />;
  if (!supply) {
    return (
      <>
        <PageHead title="Not found" crumb={{ href: "/planner", label: "Planner" }} />
        <p className="muted">The planner has no supply with this id. <Link className="link" href="/planner">Back to the planner</Link></p>
      </>
    );
  }

  const unit = UNIT_PLURAL(supply.unit);
  const week = supply.weeklyUseForecast[0];
  const left = supply.runOutDate ? daysBetween(view.asOf, supply.runOutDate) : null;
  const avg = meanPaid(bills ?? []);
  const lastBill = bills?.at(-1) ?? null;
  const quotes = itemQuotes(jobs ?? [], id);
  const best = bestQuote(quotes);
  const due = supply.status !== "ok" && supply.recommendedQty > 0;
  const saveEach = best && avg != null ? avg - best.unit : null;
  const spec = supply.name.split(",").slice(1).join(",").trim();

  return (
    <>
      <PageHead
        title={itemShort(supply.name)}
        crumb={{ href: "/planner", label: "Planner", current: itemShort(supply.name) }}
        meta={[spec, `as of ${weekday(view.asOf)}`].filter(Boolean).join(" · ")}
        right={
          due && (
            <Link href="/orders/new" className="btn btn-primary has-icon">Find best price <ArrowRight /></Link>
          )
        }
      />
      <ErrorNote error={error} />

      <div className="kpis">
        <div className="kpi">
          <span>On hand</span>
          <b>{int(supply.onHand)}</b>
          <em>{unit}</em>
        </div>
        <div className="kpi">
          <span>Use this week</span>
          <b>~{int(week?.mean)}</b>
          <em>{week ? `likely ${int(week.low)}–${int(week.high)}` : ""}</em>
        </div>
        <div className={`kpi${supply.status === "order_now" ? " warn" : ""}`}>
          <span>Runs out</span>
          <b>{supply.runOutDate ? day(supply.runOutDate) : "Not soon"}</b>
          <em>{left != null ? (left <= 0 ? "already short" : `in ${left} days`) : "more than 6 weeks"}</em>
        </div>
        <div className="kpi hero">
          <span>Suggested order</span>
          <b>{due ? int(supply.recommendedQty) : "None"}</b>
          <em>
            {due
              ? `${supply.status === "order_now" ? "order now" : `by ${day(supply.orderByDate)}`}${avg != null ? ` · ~${dollars(supply.recommendedQty * avg)} at bill prices` : ""}`
              : "Stock covers the forecast"}
          </em>
        </div>
      </div>

      <section className="card pad" aria-label="Use and demand forecast">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div>
            <h2>Weekly use and forecast</h2>
            <p className="small muted" style={{ marginTop: 2 }}>{unit.charAt(0).toUpperCase() + unit.slice(1)} used a week, from the sales of the products below</p>
          </div>
          <span className="legend small muted">
            <i className="lg-line" />Used <i className="lg-dash" />Forecast <i className="lg-band" />Likely range
          </span>
        </div>
        <ForecastChart name={supply.name} history={history} forecast={supply.weeklyUseForecast} unit="used" />
        <div className="cols" style={{ "--cols": "minmax(0, 1.3fr) minmax(0, 1fr)" } as React.CSSProperties}>
          <table className="tbl mini">
            <thead>
              <tr><th>Week of</th><th>Forecast</th><th>Likely range</th></tr>
            </thead>
            <tbody>
              {supply.weeklyUseForecast.map((f) => (
                <tr key={f.weekStart}>
                  <td>{day(f.weekStart)}</td>
                  <td className="best">~{int(f.mean)}</td>
                  <td className="muted">{int(f.low)}–{int(f.high)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <dl className="facts">
            <div><dt>Order up to</dt><dd>{int(supply.orderUpTo)} {unit}</dd></div>
            <div><dt>Safety stock</dt><dd>{int(supply.safetyStock)} {unit}</dd></div>
            <div><dt>On hand</dt><dd>{int(supply.onHand)} {unit}</dd></div>
            <div><dt>Pack size</dt><dd>{int(supply.pack)} {unit}</dd></div>
            <div>
              <dt>Suggested order</dt>
              <dd>
                {due
                  ? `${int(supply.orderUpTo)} − ${int(supply.onHand)} on hand, up to whole packs = ${int(supply.recommendedQty)}`
                  : "None: on hand is above the order point"}
              </dd>
            </div>
          </dl>
        </div>
      </section>

      <section className="card pad" aria-label="Demand by product">
        <div>
          <h2>Demand by product</h2>
          <p className="small muted" style={{ marginTop: 2 }}>
            Weekly sales from your QuickBooks invoices, and the {unit} each sale uses{supply.ratiosAssumed ? " (assumed ratios)" : ""}
          </p>
        </div>
        <div className="drivers">
          {Object.entries(supply.drivers).map(([pid, ratio]) => {
            const p = view.products.find((x) => x.itemId === pid);
            if (!p) return null;
            const misses = p.history.filter((h) => h.miss).length;
            return (
              <div key={pid} className="driver">
                <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
                  <b>{p.name}</b>
                  <span className="small muted tnum">{ratio} {ratio === 1 ? supply.unit : unit} a sale · ~{int(p.forecast[0]?.mean)} sales next week</span>
                </div>
                <ForecastChart name={p.name} history={p.history} forecast={p.forecast} width={440} height={160} />
                {misses > 0 && <p className="small muted">{misses} {misses === 1 ? "week" : "weeks"} outside the expected range (red)</p>}
              </div>
            );
          })}
        </div>
      </section>

      <section className="card pad" aria-label="Prices paid">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <h2>Prices paid and found</h2>
            <p className="small muted" style={{ marginTop: 2 }}>
              The unit price on each QuickBooks bill (dot size = quantity) against the best price the agents found
            </p>
          </div>
          {saveEach != null && saveEach > 0 && (
            <div className="save-callout">
              <b>{each(saveEach)} less each</b>
              <span className="small">
                {pct(saveEach / avg!)} under your average
                {due && ` · ${money(saveEach * supply.recommendedQty)} on the suggested ${int(supply.recommendedQty)}`}
              </span>
            </div>
          )}
        </div>
        {bills === null ? <div className="skeleton" style={{ minHeight: 200 }} /> : <PriceHistoryChart lines={bills} avg={avg} best={best?.unit ?? null} />}
        {bills && bills.length > 0 && (
          <dl className="facts row4">
            <div><dt>Average paid</dt><dd>{each(avg)}</dd></div>
            <div><dt>Last bill</dt><dd>{lastBill ? `${each(lastBill.unitPrice)} · ${day(lastBill.txnDate)}` : ""}</dd></div>
            <div><dt>Range on bills</dt><dd>{each(Math.min(...bills.map((b) => b.unitPrice)))}–{each(Math.max(...bills.map((b) => b.unitPrice)))}</dd></div>
            <div><dt>Spent on bills</dt><dd>{money(bills.reduce((n, b) => n + b.amount, 0))} · {bills.length} bills</dd></div>
          </dl>
        )}
      </section>

      <div className="cols">
        <section className="tbl-wrap" aria-label="Quotes found">
          <div className="tbl-caption"><b>Quotes found</b><span>{quotes.length} from {new Set(quotes.map((q) => q.jobId)).size === 1 ? "1 order" : `${new Set(quotes.map((q) => q.jobId)).size} orders`}</span></div>
          <table className="tbl">
            <thead>
              <tr><th>Store</th><th className="l">Type</th><th>Each</th><th>vs avg paid</th></tr>
            </thead>
            <tbody>
              {quotes.slice(0, 12).map((q) => (
                <tr key={`${q.jobId}-${q.supplierId}`}>
                  <td>
                    <Link href={jobHref((jobs ?? []).find((j) => j.jobId === q.jobId)!)} style={{ textDecoration: "none" }}>{cleanName(q.supplier)}</Link>
                    <span className="sub">{tsDay(q.at)} · {int(q.quantity)} for {money(q.lineCost)}{q.used ? " · in plan" : ""}</span>
                  </td>
                  <td className="l"><Status status={q.status} /></td>
                  <td className={q === best ? "best" : undefined}>{each(q.unit)}</td>
                  <td className={avg != null && q.unit <= avg ? "save" : "muted"}>
                    {avg != null ? (q.unit <= avg ? `${pct(1 - q.unit / avg)} less` : `${pct(q.unit / avg - 1)} more`) : ""}
                  </td>
                </tr>
              ))}
              {jobs && quotes.length === 0 && <tr><td colSpan={4} className="empty">No quotes yet</td></tr>}
            </tbody>
          </table>
        </section>

        <section className="tbl-wrap" aria-label="Bills">
          <div className="tbl-caption"><b>QuickBooks bills</b><span>{bills?.[0]?.vendorName ? cleanName(bills[0].vendorName) : ""}</span></div>
          <table className="tbl">
            <thead>
              <tr><th>Date</th><th>Quantity</th><th>Each</th><th>Amount</th></tr>
            </thead>
            <tbody>
              {[...(bills ?? [])].reverse().slice(0, 12).map((b) => (
                <tr key={b.billId}>
                  <td>{day(b.txnDate)}<span className="sub">{b.billId}</span></td>
                  <td>{int(b.quantity)}</td>
                  <td>{each(b.unitPrice)}</td>
                  <td>{money(b.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>

      <Notes view={view} supply={supply} onChange={load} />

      {view.disclosure && <p className="small muted">{view.disclosure}</p>}
    </>
  );
}
