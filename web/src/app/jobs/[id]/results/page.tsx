"use client";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError, apiUrl, detailText, errorText, money, type Plan } from "@/api/client";
import { StatusChip } from "@/components/StatusChip";
import { useJobEvents } from "@/hooks/useJobEvents";

function RulesBadge({ plan }: { plan: Plan }) {
  const rc = plan.rulesCheck;
  if (!rc) return <span className="chip neutral">rules: not checked</span>;
  if (rc.pass) return <span className="chip neutral">rules: pass</span>;
  return (
    <span className="chip blocked" title={(rc.failed ?? []).map((f) => f.text).join("\n")}>
      rules: fail ({(rc.failed ?? []).map((f) => f.code).join(", ")})
    </span>
  );
}

export default function ResultsPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const s = useJobEvents(id);
  const job = s.job;
  const results = job?.results ?? null;

  const [selected, setSelected] = useState<string | null>(null);
  const [acceptQuoted, setAcceptQuoted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [initDone, setInitDone] = useState(false);

  useEffect(() => {
    if (!initDone && job && results) {
      setSelected(results.recommendedPlanId ?? results.plans[0]?.planId ?? null);
      setAcceptQuoted(!!job.request.acceptQuotedDefault);
      setInitDone(true);
    }
  }, [initDone, job, results]);

  async function decide(action: "approve" | "reject") {
    setBusy(true);
    setError(null);
    try {
      await api.approve(id, { action, planId: selected, acceptQuoted, decidedBy: "owner", reason: null });
      await s.refetch();
      if (action === "approve") router.push(`/jobs/${id}/checkout`);
    } catch (e) {
      // 409: estimated plan, quoted plan without acceptQuoted, rules failed, ... Show detail.text.
      setError(e instanceof ApiError ? detailText(e.detail) || e.message : errorText(e));
    } finally {
      setBusy(false);
    }
  }

  if (!job) return <p>Loading job...</p>;
  if (!results) {
    return (
      <>
        <h1>Results</h1>
        <p>No results yet (job state: {s.jobState}). <Link href={`/jobs/${id}`}>Back to the working screen</Link></p>
      </>
    );
  }

  const screenshots = new Map((job.carts ?? []).map((c) => [c.cartKey, c.screenshotUrl]));
  const transcriptUrl = (job.offers ?? []).find((o) => o.evidence?.transcriptUrl)?.evidence?.transcriptUrl;

  return (
    <>
      <h1>Results</h1>
      <p className="row">
        <span>Job {id}: {results.status}{results.deadlineHit ? " (deadline hit)" : ""}</span>
        <span>State: <strong>{s.jobState}</strong></span>
        {job.replayOf && <span className="disclosure">Replay of {job.replayOf}</span>}
        <Link href={`/jobs/${id}`}>Working screen</Link>
      </p>
      <p>{results.summary}</p>
      <p>
        Savings vs paid before: <strong>{money(results.savingsVsPaidBefore)}</strong> this order,{" "}
        <strong>{money(results.projectedYearlySavings)}</strong> a year (projected).
      </p>
      {!results.recommendedPlanId && (
        <p className="error">No recommended plan: {results.noRecommendationReason ?? "no plan qualifies"}</p>
      )}

      {(results.warnings?.length ?? 0) > 0 && (
        <>
          <h2>Warnings</h2>
          <ul>
            {results.warnings!.map((w, i) => (
              <li key={i}><code>{w.code}</code> {w.text}</li>
            ))}
          </ul>
        </>
      )}

      <h2>Plans</h2>
      <fieldset style={{ border: "none", padding: 0 }}>
        <legend className="sr-only">Choose a plan</legend>
        {results.plans.map((p) => (
          <section key={p.planId} className="card" style={{ marginBottom: 8 }}>
            <label className="row">
              <input type="radio" name="plan" checked={selected === p.planId} onChange={() => setSelected(p.planId)} />
              <strong>{p.label}</strong> <span className="muted">({p.planId}, {p.kind})</span>
              <strong>{money(p.total)}</strong>
              <StatusChip status={p.status} />
              <RulesBadge plan={p} />
              {p.planId === results.recommendedPlanId && <span className="chip neutral">recommended</span>}
              {p.planId === results.quotedPlanId && <span className="chip quoted">distributor quote</span>}
            </label>
            {p.uncoveredItems && p.uncoveredItems.length > 0 && (
              <p className="error">Not covered: {p.uncoveredItems.join(", ")}</p>
            )}
            {p.rulesCheck && !p.rulesCheck.pass && (
              <ul className="error">{(p.rulesCheck.failed ?? []).map((f, i) => <li key={i}>{f.text}</li>)}</ul>
            )}
            <table>
              <thead>
                <tr><th>Cart</th><th>Lines</th><th>Subtotal</th><th>Shipping</th><th>Tax</th><th>Total</th><th>Status</th><th /></tr>
              </thead>
              <tbody>
                {p.carts.map((c) => (
                  <tr key={c.cartKey}>
                    <td>{c.supplierId}</td>
                    <td>{c.lines.map((l) => `${l.itemId} ${l.sku} x${l.packQuantity}`).join(", ")}</td>
                    <td>{money(c.subtotal)}</td>
                    <td>{money(c.shipping)}{c.shippingIsEstimate ? " (est.)" : ""}</td>
                    <td>{money(c.tax)}</td>
                    <td>{money(c.total)}</td>
                    <td><StatusChip status={c.status} /></td>
                    <td>
                      {screenshots.get(c.cartKey) && (
                        <a href={apiUrl(screenshots.get(c.cartKey))} target="_blank" rel="noreferrer">screenshot</a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        ))}
      </fieldset>

      {transcriptUrl && (
        <p><a href={apiUrl(transcriptUrl)} target="_blank" rel="noreferrer">Distributor call transcript</a></p>
      )}

      {(results.laneOutcomes?.length ?? 0) > 0 && (
        <>
          <h2>Lane outcomes</h2>
          <ul>
            {results.laneOutcomes!.map((o) => (
              <li key={o.supplierId}>
                {o.supplierId}: <StatusChip status={o.state === "failed" || o.state === "blocked" ? "failed" : null} label={o.state} />
                {o.reason && ` (${o.reason})`}
              </li>
            ))}
          </ul>
        </>
      )}

      <h2>Decision</h2>
      {job.approval ? (
        <p>
          Decision: <strong>{job.approval.action}</strong> {job.approval.planId}
          {job.approval.action === "approve" && <> - <Link href={`/jobs/${id}/checkout`}>Checkout</Link></>}
        </p>
      ) : null}
      <p>
        <label>
          <input type="checkbox" checked={acceptQuoted} onChange={(e) => setAcceptQuoted(e.target.checked)} /> Accept quoted offer
        </label>
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="row">
        <button disabled={busy || !selected} onClick={() => decide("approve")}>Approve</button>
        <button disabled={busy} onClick={() => decide("reject")}>Reject</button>
      </div>
    </>
  );
}
