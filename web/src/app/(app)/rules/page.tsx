"use client";
import { useEffect, useState } from "react";
import { api, errorText, money, type JobView, type ShoppingRequest, type SpendingRules } from "@/api/client";
import { ErrorNote, Loading, PageHead } from "@/components/PageHead";
import { Status } from "@/components/Status";
import { cleanName, tsDay } from "@/lib/format";
import { headlinePlan, orderTitle } from "@/lib/job";

// Card policy: backend/app/stages/cards.py (LIMIT_MARGIN 1.02, LIFETIME 60 min).
const CARD_RULES: [string, string][] = [
  ["Issued", "One single-use card per store, at approval"],
  ["Limit", "Cart total + 2%"],
  ["Expires", "60 min after approval"],
];

export default function RulesPage() {
  const [rules, setRules] = useState<SpendingRules | null>(null);
  const [draft, setDraft] = useState<ShoppingRequest | null>(null);
  const [jobs, setJobs] = useState<JobView[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.rules().then(setRules).catch((e) => setError(errorText(e)));
    api.shoppingDraft().then(setDraft).catch(() => setDraft(null));
    api.jobs(20).then(setJobs).catch(() => setJobs([]));
  }, []);

  if (!rules) return error ? <ErrorNote error={error} /> : <Loading label="Loading rules" />;

  const names: Record<string, string> = {};
  for (const s of draft?.suppliers ?? []) names[s.id] = cleanName(s.name);
  if (draft?.distributor) names[draft.distributor.id] = cleanName(draft.distributor.name);
  const checks = jobs
    .map((j) => ({ j, plan: headlinePlan(j) }))
    .filter((x) => x.plan?.rulesCheck)
    .slice(0, 8);

  const big = { fontWeight: 600, fontSize: 20, lineHeight: "26px" } as const;

  return (
    <>
      <PageHead title="Rules" meta={rules.source === "derived from the books" ? "From your books" : rules.source} />
      <div className="tbl-wrap">
        <table className="tbl">
          <tbody>
            <tr>
              <td className="strong" style={{ width: 220 }}>Per-order cap</td>
              <td className="l"><span className="tnum" style={big}>{money(rules.perOrderCap)}</span></td>
            </tr>
            <tr>
              <td className="strong">Monthly budget left</td>
              <td className="l"><span className="tnum" style={big}>{money(rules.monthlyBudgetRemaining)}</span></td>
            </tr>
            <tr>
              <td className="strong">Approved suppliers</td>
              <td className="l">
                <span className="tnum" style={big}>{rules.approvedSuppliers.length}</span>
                <span className="sub">{rules.approvedSuppliers.map((id) => names[id] ?? id).join(" · ")}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="tbl-wrap">
        <div className="tbl-caption">Cards</div>
        <table className="tbl">
          <tbody>
            {CARD_RULES.map(([k, v]) => (
              <tr key={k}><td className="muted" style={{ width: 220 }}>{k}</td><td className="l">{v}</td></tr>
            ))}
          </tbody>
        </table>
      </div>

      {checks.length > 0 && (
        <div className="tbl-wrap">
          <div className="tbl-caption">Recent checks</div>
          <table className="tbl">
            <tbody>
              {checks.map(({ j, plan }) => (
                <tr key={j.jobId}>
                  <td className="muted" style={{ width: 120 }}>{tsDay(j.createdAt)}</td>
                  <td className="l">{orderTitle(j)}</td>
                  <td className="best">{money(plan!.total)}</td>
                  <td className="l" style={{ width: 260 }}>
                    {plan!.rulesCheck!.pass ? <Status tone="verified">Pass</Status> : <Status tone="failed">{plan!.rulesCheck!.failed?.[0]?.text ?? "Fail"}</Status>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
