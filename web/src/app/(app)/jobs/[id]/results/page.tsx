"use client";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError, detailText, errorText, money, type JobView, type Plan } from "@/api/client";
import { ArrowRight } from "@/components/Icons";
import { ErrorNote, Loading, PageHead } from "@/components/PageHead";
import { Roll } from "@/components/Roll";
import { SavingsBreakdown } from "@/components/SavingsBreakdown";
import { Status, statusWord } from "@/components/Status";
import { useJobEvents } from "@/hooks/useJobEvents";
import { int, itemShort, shortName } from "@/lib/format";
import { orderTitle, supplierNames } from "@/lib/job";
import { planSavings } from "@/lib/savings";

/** The columns to compare: one per store ("everything from X"), the quote, and the chosen plan last. */
function columns(job: JobView, selected: Plan): Plan[] {
  const r = job.results!;
  const singles = r.plans.filter((p) => p.kind === "single_supplier").sort((a, b) => a.total - b.total);
  const quoted = r.plans.find((p) => p.planId === r.quotedPlanId);
  const rec = r.plans.find((p) => p.planId === r.recommendedPlanId);
  const extra = [rec, quoted].filter((p): p is Plan => !!p && p.kind !== "single_supplier");
  const rest = [...singles.slice(0, 2), ...extra].filter((p) => p.planId !== selected.planId);
  return [...rest.slice(0, 3), selected];
}

function cartWord(p: Plan): string {
  const web = p.carts.filter((c) => c.status !== "quoted").length;
  const quote = p.carts.length - web;
  return [web ? `${web} ${web === 1 ? "cart" : "carts"}` : "", quote ? "quote" : ""].filter(Boolean).join(" + ");
}

function header(p: Plan, job: JobView, names: Record<string, string>): string {
  const sups = [...new Set(p.assignments.map((a) => a.supplierId))];
  if (p.kind === "single_supplier" || sups.length === 1) return names[sups[0]] ?? sups[0];
  if (p.planId === job.results?.quotedPlanId) {
    const d = job.request.distributor;
    return d ? `With ${shortName(d.name)} quote` : "With quote";
  }
  return p.planId === job.results?.recommendedPlanId ? "Best online" : "Split";
}

export default function ResultsPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const s = useJobEvents(id);
  const job = s.job;
  const results = job?.results ?? null;

  const [acceptQuoted, setAcceptQuoted] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [init, setInit] = useState(false);

  useEffect(() => {
    if (!init && job && results) {
      setAcceptQuoted(!!job.request.acceptQuotedDefault);
      setInit(true);
    }
  }, [init, job, results]);

  if (!job) return s.error ? <ErrorNote error={s.error} /> : <Loading label="Loading results" />;
  if (!results) {
    return (
      <>
        <PageHead title="Best plan" crumb={{ href: "/orders", label: "Orders", current: orderTitle(job) }} />
        <div className="card empty">
          Still finding prices. <Link className="link" href={`/jobs/${id}`}>Watch the stores</Link>
        </div>
      </>
    );
  }

  const names = supplierNames(job);
  const rec = results.plans.find((p) => p.planId === results.recommendedPlanId) ?? null;
  const quoted = results.plans.find((p) => p.planId === results.quotedPlanId) ?? null;
  const chosenId = job.approval?.planId ?? picked ?? (acceptQuoted && quoted ? quoted.planId : rec?.planId ?? results.plans[0]?.planId);
  const selected = results.plans.find((p) => p.planId === chosenId) ?? results.plans[0];
  const cols = selected ? columns(job, selected) : [];
  const items = job.request.items.filter((i) => i.quantity > 0);
  const carts = selected?.carts.filter((c) => c.status !== "quoted").length ?? 0;
  const rulesFailed = selected?.rulesCheck && !selected.rulesCheck.pass ? selected.rulesCheck.failed ?? [] : [];
  const savings = planSavings(job, selected)?.saved ?? null;
  const decided = job.approval?.action;

  async function decide(action: "approve" | "reject") {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await api.approve(id, { action, planId: selected.planId, acceptQuoted, decidedBy: "owner", reason: null });
      await s.refetch();
      if (action === "approve") router.push(`/jobs/${id}/checkout`);
    } catch (e) {
      // 409: estimated plan, quoted plan without acceptQuoted, rules failed. Show detail.text.
      setError(e instanceof ApiError ? detailText(e.detail) || e.message : errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const line = (p: Plan, itemId: string) => p.assignments.find((a) => a.itemId === itemId);
  const extras = (p: Plan) => p.carts.reduce((n, c) => n + c.shipping + c.tax, 0);

  return (
    <>
      <PageHead
        title={decided === "reject" ? "Rejected" : "Best plan"}
        crumb={{ href: "/orders", label: "Orders", current: orderTitle(job) }}
        right={
          selected && (
            <div>
              <p className="figure"><Roll text={money(selected.total)} fromZero /></p>
              {savings != null && Math.abs(savings) >= 0.01 && (
                <p className="figure-sub" style={savings < 0 ? { color: "var(--color-muted)" } : undefined}>
                  {money(Math.abs(savings))} {savings > 0 ? "less" : "more"} than your bills
                </p>
              )}
            </div>
          )
        }
      />
      {!rec && <p className="alert" role="alert">{results.noRecommendationReason ?? "No plan covers every item."}</p>}
      {selected && <SavingsBreakdown job={job} plan={selected} />}

      {selected && (
        <div className="tbl-wrap">
          <table className="tbl wide">
            <thead>
              <tr>
                <th>Item</th>
                {cols.map((p) => {
                  const mine = p.planId === selected.planId;
                  return (
                    <th key={p.planId} className={mine ? "plan" : undefined}>
                      {mine || decided ? (
                        <b>{mine ? "Your plan" : header(p, job, names)}</b>
                      ) : (
                        <button
                          style={{ all: "unset", cursor: "pointer", display: "block", font: "600 15px/20px var(--font-sans)", color: "var(--color-ink)" }}
                          onClick={() => {
                            setPicked(p.planId);
                            setAcceptQuoted(p.planId === results.quotedPlanId);
                          }}
                          aria-label={`Choose ${header(p, job, names)}`}
                        >
                          {header(p, job, names)}
                        </button>
                      )}
                      <Status status={p.status}>
                        {mine ? cartWord(p) : statusWord(p.status)}
                      </Status>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id}>
                  <td>
                    {itemShort(it.name)}
                    <span className="sub">{int(it.quantity)}</span>
                  </td>
                  {cols.map((p) => {
                    const a = line(p, it.id);
                    const mine = p.planId === selected.planId;
                    const used = a && line(selected, it.id)?.supplierId === a.supplierId && line(selected, it.id)?.sku === a.sku;
                    if (!a) return <td key={p.planId} className={mine ? "plan dim" : "dim"}>Not sold</td>;
                    return (
                      <td key={p.planId} className={`${mine ? "plan best" : used ? "best" : "dim"}`}>
                        {money(a.lineCost)}
                        <span className="sub">
                          {mine
                            ? `from ${shortName(names[a.supplierId] ?? a.supplierId)}`
                            : a.source === "web" ? `${int(a.packQuantity)} packs` : "by phone"}
                        </span>
                      </td>
                    );
                  })}
                </tr>
              ))}
              <tr>
                <td>Shipping and tax</td>
                {cols.map((p) => {
                  const x = extras(p);
                  return (
                    <td key={p.planId} className={p.planId === selected.planId ? "plan best" : "dim"}>
                      {x < 0.005 ? "Free" : money(x)}
                    </td>
                  );
                })}
              </tr>
            </tbody>
            <tfoot>
              <tr>
                <td>Checkout total</td>
                {cols.map((p) => (
                  <td key={p.planId} className={p.planId === selected.planId ? "plan" : undefined}>{money(p.total)}</td>
                ))}
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      {quoted && !decided && (
        <div className="panel row" style={{ justifyContent: "space-between", padding: "16px 20px" }}>
          <div>
            <p>
              <span className="strong">{header(quoted, job, names)}</span> · <span className="strong tnum">{money(quoted.total)}</span>
            </p>
            {rec && (
              <p className="small ink-2 tnum" style={{ marginTop: 2 }}>
                {rec.total - quoted.total > 0 ? `${money(rec.total - quoted.total)} less` : `${money(quoted.total - rec.total)} more`}
              </p>
            )}
          </div>
          <label className="row" style={{ gap: 12 }}>
            <span className="small ink-2">Accept quote</span>
            <button
              type="button"
              role="switch"
              className="switch"
              aria-checked={acceptQuoted}
              onClick={() => {
                setAcceptQuoted(!acceptQuoted);
                setPicked(null);
              }}
            />
          </label>
        </div>
      )}

      {selected?.uncoveredItems && selected.uncoveredItems.length > 0 && (
        <p className="alert">Not covered: {selected.uncoveredItems.map((id) => itemShort(items.find((i) => i.id === id)?.name ?? id).toLowerCase()).join(", ")}</p>
      )}
      <ErrorNote error={error} />

      <div className="actions">
        {decided === "approve" ? (
          <Link href={`/jobs/${id}/checkout`} className="btn btn-primary has-icon">Open checkout <ArrowRight /></Link>
        ) : decided === "reject" ? (
          <Link href="/orders/new" className="btn btn-secondary">Start a new order</Link>
        ) : (
          <>
            <button className="btn btn-primary has-icon" disabled={busy || !selected || rulesFailed.length > 0} onClick={() => decide("approve")}>
              {carts > 0 ? `Approve and fill ${carts} ${carts === 1 ? "cart" : "carts"}` : "Approve"} <ArrowRight />
            </button>
            <button className="btn btn-secondary" disabled={busy} onClick={() => decide("reject")}>Reject</button>
          </>
        )}
        <span className="spacer" />
        {selected?.rulesCheck && (
          <span className="row" style={{ gap: 8 }}>
            {rulesFailed.length > 0 ? (
              <Status tone="failed">{rulesFailed[0].text}</Status>
            ) : (
              <Status tone="verified">Rules pass</Status>
            )}
            <Link className="link small" href="/rules">View rules</Link>
          </span>
        )}
      </div>
    </>
  );
}
