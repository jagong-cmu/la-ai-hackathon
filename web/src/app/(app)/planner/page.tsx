"use client";
// The planner overview: every supply's runway and order, the prices paid on the QuickBooks bills,
// the best prices the agents found, and the money saved. Each supply opens its own page.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, errorText, money, type PlannerView, type SupplyPlan } from "@/api/client";
import { ArrowRight } from "@/components/Icons";
import { ErrorNote, Loading, PageHead } from "@/components/PageHead";
import { Roll } from "@/components/Roll";
import { useBills, useJobs } from "@/hooks/useData";
import { day, daysBetween, dollars, each, int, tsDay } from "@/lib/format";
import { jobHref, orderTitle } from "@/lib/job";
import { bestQuote, itemQuotes, meanPaid, pct, planSavings, pricedPlan, savingsTotals } from "@/lib/savings";

const RUNWAY_DAYS = 42; // a full bar = six weeks of stock

function OrderCell({ s }: { s: SupplyPlan }) {
  if (s.status === "ok" || s.recommendedQty <= 0) return <span className="muted">Enough</span>;
  const when = s.status === "order_now" ? "order now" : `by ${day(s.orderByDate)}`;
  return <span className="strong">{int(s.recommendedQty)} · {when}</span>;
}

export default function PlannerPage() {
  const router = useRouter();
  const [view, setView] = useState<PlannerView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const jobs = useJobs();
  const bills = useBills();

  useEffect(() => {
    api.planner().then(setView).catch((e) => setError(errorText(e)));
  }, []);

  if (!view) return error ? <ErrorNote error={error} /> : <Loading label="Loading the planner" />;

  const supplies = [...view.supplies].sort((a, b) => (a.runOutDate ?? "9").localeCompare(b.runOutDate ?? "9"));
  const due = supplies.filter((s) => s.status !== "ok" && s.recommendedQty > 0);
  const paid = (itemId: string) => meanPaid((bills ?? []).filter((b) => b.itemId === itemId));
  const dueCost = due.reduce((n, s) => n + s.recommendedQty * (paid(s.itemId) ?? 0), 0);
  const totals = savingsTotals(jobs ?? []);
  const yearSpend = (bills ?? []).reduce((n, b) => n + b.amount, 0);
  const priced = (jobs ?? []).filter((j) => j.results && planSavings(j, pricedPlan(j))).slice(0, 6);
  const topSaved = Math.max(0.01, ...priced.map((j) => Math.abs(planSavings(j, pricedPlan(j))!.saved)));

  return (
    <>
      <PageHead
        title="Planner"
        right={
          due.length > 0 && (
            <Link href="/orders/new" className="btn btn-primary has-icon">
              Find best price for {due.length} <ArrowRight />
            </Link>
          )
        }
      />
      <ErrorNote error={error} />

      <div className="kpis">
        <div className="kpi hero">
          <span>Saved on approved orders</span>
          <b>{jobs ? <Roll text={dollars(totals.approved)} fromZero /> : "–"}</b>
          <em>{totals.approvedCount} {totals.approvedCount === 1 ? "order" : "orders"} against your bill prices</em>
        </div>
        <div className="kpi">
          <span>Found in quotes</span>
          <b>{jobs ? <Roll text={dollars(totals.found)} fromZero /> : "–"}</b>
          <em>{totals.foundCount > 0 ? `${pct(totals.pct)} under your bills · ${totals.foundCount} priced` : "No priced orders yet"}</em>
        </div>
        <div className="kpi">
          <span>Due to order</span>
          <b>{due.length} of {supplies.length}</b>
          <em>{dueCost > 0 ? `~${dollars(dueCost)} at your bill prices` : "Nothing due"}</em>
        </div>
        <div className="kpi">
          <span>Supply spend on bills</span>
          <b>{bills ? <Roll text={dollars(yearSpend)} fromZero /> : "–"}</b>
          <em>{bills ? `${new Set(bills.map((b) => b.billId)).size} QuickBooks bills` : ""}</em>
        </div>
      </div>

      <div className="tbl-wrap">
        <table className="tbl wide">
          <thead>
            <tr>
              <th>Supply</th>
              <th>On hand</th>
              <th>Use a week</th>
              <th className="l" style={{ width: 200 }}>Runs out</th>
              <th>Order</th>
              <th>Paid each</th>
              <th>Best found</th>
              <th><span className="sr-only">Open</span></th>
            </tr>
          </thead>
          <tbody>
            {supplies.map((s) => {
              const left = s.runOutDate ? daysBetween(view.asOf, s.runOutDate) : RUNWAY_DAYS;
              const ok = s.status === "ok";
              const before = paid(s.itemId);
              const best = bestQuote(itemQuotes(jobs ?? [], s.itemId));
              const href = `/planner/${encodeURIComponent(s.itemId)}`;
              return (
                <tr key={s.itemId} className={`click${ok ? " dim" : ""}`} onClick={() => router.push(href)}>
                  <td>
                    <Link href={href} style={{ textDecoration: "none", color: ok ? "var(--color-ink-2)" : undefined }}>{s.name}</Link>
                  </td>
                  <td>{int(s.onHand)}</td>
                  <td>~{int(s.weeklyUseForecast[0]?.mean)}</td>
                  <td className="l">
                    <span className="row" style={{ gap: 12, flexWrap: "nowrap" }}>
                      <span className={`bar${ok ? " soft" : ""}`} style={{ width: 80, flexShrink: 0 }}>
                        <i style={{ width: `${Math.max(4, Math.min(100, (left / RUNWAY_DAYS) * 100))}%` }} />
                      </span>
                      <span className={ok ? undefined : "strong"} style={{ color: ok ? "var(--color-muted)" : undefined }}>
                        {day(s.runOutDate)}
                      </span>
                    </span>
                  </td>
                  <td><OrderCell s={s} /></td>
                  <td className="muted">{each(before)}</td>
                  <td>
                    {best ? (
                      <>
                        <span className="strong">{each(best.unit)}</span>
                        {before != null && (
                          <span className={`sub ${best.unit <= before ? "save" : ""}`}>
                            {best.unit <= before ? `${pct(1 - best.unit / before)} less` : `${pct(best.unit / before - 1)} more`}
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="muted">{jobs ? "Not priced" : ""}</span>
                    )}
                  </td>
                  <td className="chev" aria-hidden="true"><ArrowRight /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <section className="tbl-wrap" aria-label="Recent quotes">
        <div className="tbl-caption">
          <b>Recent quotes against your bills</b>
          <Link href="/orders" className="link small">All orders</Link>
        </div>
        <table className="tbl wide">
          <thead>
            <tr>
              <th>Order</th>
              <th>Date</th>
              <th>Paid before</th>
              <th>Best plan</th>
              <th className="l" style={{ width: 220 }}>Saved</th>
            </tr>
          </thead>
          <tbody>
            {priced.map((j) => {
              const s = planSavings(j, pricedPlan(j))!;
              const href = jobHref(j);
              return (
                <tr key={j.jobId} className="click" onClick={() => router.push(href)}>
                  <td><Link href={href} style={{ textDecoration: "none" }}>{orderTitle(j)}</Link></td>
                  <td className="muted">{tsDay(j.createdAt)}</td>
                  <td className="muted">{money(s.paidBefore)}</td>
                  <td>{money(s.now)}</td>
                  <td className="l">
                    <span className="row" style={{ gap: 12, flexWrap: "nowrap" }}>
                      <span className={`bar${s.saved >= 0 ? " mint" : ""}`} style={{ width: 80, flexShrink: 0 }}>
                        <i style={{ width: `${Math.max(4, (Math.abs(s.saved) / topSaved) * 100)}%`, background: s.saved < 0 ? "var(--color-failed)" : undefined }} />
                      </span>
                      <span className={s.saved >= 0 ? "strong save" : "strong error-text"}>
                        {s.saved >= 0 ? "" : "−"}{money(Math.abs(s.saved))} · {pct(s.pct)}
                      </span>
                    </span>
                  </td>
                </tr>
              );
            })}
            {jobs && priced.length === 0 && (
              <tr><td colSpan={5} className="empty">No priced orders yet. Start one to compare prices with your bills.</td></tr>
            )}
          </tbody>
        </table>
      </section>

    </>
  );
}
