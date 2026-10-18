"use client";
// Onboarding steps 3 and 4: watch the agents get quotes, then see the result in plain words.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect } from "react";
import { money } from "@/api/client";
import { ArrowRight } from "@/components/Icons";
import { BrowserPane } from "@/components/job/BrowserPane";
import { CallPanel, callFromEvents } from "@/components/job/CallStrip";
import { useJobEvents } from "@/hooks/useJobEvents";
import { cleanName, itemShort, shortName } from "@/lib/format";
import { supplierNames, supplierUrls } from "@/lib/job";
import { pct, planSavings, pricedPlan } from "@/lib/savings";
import { ONBOARDED_KEY, Steps } from "../../steps";

export default function OnboardingJobPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const s = useJobEvents(jobId);
  const job = s.job;
  const ready = !!job?.results;

  useEffect(() => {
    if (!ready) return;
    try {
      localStorage.setItem(ONBOARDED_KEY, "1");
    } catch {
      // Private mode: the flag is only a convenience.
    }
  }, [ready]);

  if (!job) {
    return (
      <main className="ob-center">
        {s.error ? <p className="alert" role="alert">{s.error}</p> : <div className="ob-card"><Steps at={2} /><h1>Starting</h1></div>}
      </main>
    );
  }

  const names = supplierNames(job);
  const urls = supplierUrls(job);
  const order = job.request.suppliers.map((x) => x.id);
  const lanes = Object.values(s.lanes).sort((a, b) => order.indexOf(a.supplierId) - order.indexOf(b.supplierId));
  const distributor = job.request.distributor ? shortName(job.request.distributor.name) : "Distributor";

  if (!ready) {
    const best = s.bestPlan?.total ?? null;
    const tiles = Math.max(1, lanes.length) + (job.request.distributor ? 1 : 0);
    return (
      <main className="ob-wide">
        <div className="ob-work-head">
          <div>
            <Steps at={2} />
            <h1>Getting your quotes</h1>
            <p className="ob-lead">
              {job.request.suppliers.length} websites{job.request.distributor ? ` and a call to ${distributor}` : ""}, all at once. This takes about two minutes.
            </p>
          </div>
          <div className="ob-best" aria-live="polite">
            <span>Best so far</span>
            <b className="tnum">{best != null ? money(best) : "–"}</b>
          </div>
        </div>
        <div className="tiles ob-tiles" style={{ "--tile-cols": tiles <= 4 ? 2 : 3 } as React.CSSProperties}>
          {lanes.map((l) => (
            <BrowserPane key={l.laneId} lane={l} name={names[l.supplierId] ?? l.supplierId} siteUrl={urls[l.supplierId]} job={job} />
          ))}
          {lanes.length === 0 && <div className="skeleton" />}
          {job.request.distributor && <CallPanel call={callFromEvents(s.events)} who={distributor} />}
        </div>
      </main>
    );
  }

  const plan = pricedPlan(job);
  const sv = planSavings(job, plan);
  const items = job.request.items.filter((i) => i.quantity > 0);
  const missing = items.filter((i) => !plan?.assignments.some((a) => a.itemId === i.id));

  return (
    <main className="ob-center">
      <div className="ob-card">
        <Steps at={3} />
        {plan ? (
          <>
            <h1>{sv && sv.saved > 0.005 ? "We found you a better price" : "Here is your best price"}</h1>
            {sv && sv.saved > 0.005 ? (
              <div className="ob-save">
                <b className="tnum">{money(sv.saved)}</b>
                <span>saved, {pct(sv.pct)} less than you paid before</span>
              </div>
            ) : (
              <div className="ob-save plain">
                <b className="tnum">{money(plan.total)}</b>
                <span>{sv ? `About the same as the ${money(sv.paidBefore)} you paid before` : "Total with shipping and tax"}</span>
              </div>
            )}
            <ul className="ob-result">
              {items.map((it) => {
                const a = plan.assignments.find((x) => x.itemId === it.id);
                return (
                  <li key={it.id}>
                    <span>{itemShort(it.name)}</span>
                    <span className="muted">{a ? cleanName(names[a.supplierId] ?? a.supplierId) : "No price found"}</span>
                    <b className="tnum">{a ? money(a.lineCost) : "–"}</b>
                  </li>
                );
              })}
              <li className="total">
                <span>Total</span>
                <span className="muted">{sv ? `was ${money(sv.paidBefore)}` : ""}</span>
                <b className="tnum">{money(plan.total)}</b>
              </li>
            </ul>
            {missing.length > 0 && (
              <p className="ob-fine">No supplier had {missing.map((i) => itemShort(i.name).toLowerCase()).join(" or ")} this time. You can try again from the dashboard.</p>
            )}
            <Link href={`/jobs/${job.jobId}/results`} className="btn btn-primary has-icon ob-go">Review and approve <ArrowRight /></Link>
            <Link href="/planner" className="ob-alt">Go to your dashboard</Link>
          </>
        ) : (
          <>
            <h1>We could not get prices this time</h1>
            <p className="ob-lead">The suppliers did not answer in time. Try again, or open the dashboard to see what happened.</p>
            <Link href="/start" className="btn btn-primary has-icon ob-go">Try again <ArrowRight /></Link>
            <Link href={`/jobs/${job.jobId}`} className="ob-alt">See what happened</Link>
          </>
        )}
      </div>
    </main>
  );
}
