"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { money } from "@/api/client";
import { ArrowRight } from "@/components/Icons";
import { ErrorNote, Loading, PageHead } from "@/components/PageHead";
import { BrowserPane, LiveStage } from "@/components/job/BrowserPane";
import { CallStrip, callFromEvents } from "@/components/job/CallStrip";
import { useJobEvents } from "@/hooks/useJobEvents";
import { shortName } from "@/lib/format";
import { jobHref, orderTitle, planShort, supplierNames, supplierUrls } from "@/lib/job";

export default function WorkingPage() {
  const { id } = useParams<{ id: string }>();
  const s = useJobEvents(id);
  const [picked, setPicked] = useState<string | null>(null);
  const job = s.job;
  if (!job) return s.error ? <ErrorNote error={s.error} /> : <Loading label="Loading the order" />;

  const names = supplierNames(job);
  const urls = supplierUrls(job);
  const order = job.request.suppliers.map((x) => x.id);
  const lanes = Object.values(s.lanes).sort((a, b) => order.indexOf(a.supplierId) - order.indexOf(b.supplierId));
  // The stage shows the lane the owner picked, else the first lane with a live browser.
  const withLive = lanes.filter((l) => l.liveUrl);
  const watched = withLive.find((l) => l.laneId === picked) ?? withLive[0] ?? null;
  const call = callFromEvents(s.events);
  const distributor = job.request.distributor ? shortName(job.request.distributor.name) : "Distributor";
  const ready = !!job.results;
  // After results, the recommended plan; before, the best plan the events report so far.
  const bestId = ready ? job.results!.recommendedPlanId : s.bestPlan?.planId;
  const best = bestId ? (job.results?.plans ?? job.plans).find((p) => p.planId === bestId) : null;
  const bestTotal = best?.total ?? s.bestPlan?.total ?? null;

  return (
    <>
      <PageHead
        title={ready ? "Prices found" : "Finding the best price"}
        crumb={{ href: "/orders", label: "Orders", current: orderTitle(job) }}
        right={
          ready && (
            <Link href={jobHref(job)} className="btn btn-primary has-icon">
              {job.approval ? "Open checkout" : "See best plan"} <ArrowRight />
            </Link>
          )
        }
      />
      <ErrorNote error={s.error} />

      {watched && (
        <LiveStage
          key={watched.laneId}
          lane={watched}
          name={names[watched.supplierId] ?? watched.supplierId}
          siteUrl={urls[watched.supplierId]}
        />
      )}

      <div className="panes">
        {lanes.map((l) => (
          <BrowserPane
            key={l.laneId}
            lane={l}
            name={names[l.supplierId] ?? l.supplierId}
            siteUrl={urls[l.supplierId]}
            job={job}
            watched={watched?.laneId === l.laneId}
            onWatch={() => setPicked(l.laneId)}
          />
        ))}
        {lanes.length === 0 && <div className="skeleton" style={{ minHeight: 320 }} />}
      </div>

      <div className="callrow">
        {call ? <CallStrip call={call} who={distributor} /> : <div />}
        {bestTotal != null && (
          <div className="bestbox" aria-live="polite">
            <div>
              <p className="k">{ready ? "Best plan" : "Best so far"}</p>
              {best && <p className="v">{planShort(best, job)}</p>}
            </div>
            <span className="n">{money(bestTotal)}</span>
          </div>
        )}
      </div>
    </>
  );
}
