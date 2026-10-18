"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { money } from "@/api/client";
import { ArrowRight } from "@/components/Icons";
import { Roll } from "@/components/Roll";
import { ErrorNote, Loading, PageHead } from "@/components/PageHead";
import { BrowserPane } from "@/components/job/BrowserPane";
import { CallPanel, callFromEvents } from "@/components/job/CallStrip";
import { OrderBasis, usePlanner } from "@/components/job/OrderBasis";
import { useJobEvents } from "@/hooks/useJobEvents";
import { shortName } from "@/lib/format";
import { jobHref, orderTitle, planShort, supplierNames, supplierUrls } from "@/lib/job";

export default function WorkingPage() {
  const { id } = useParams<{ id: string }>();
  const s = useJobEvents(id);
  const [picked, setPicked] = useState<string | null>(null);
  const planner = usePlanner();
  const job = s.job;
  if (!job) return s.error ? <ErrorNote error={s.error} /> : <Loading label="Loading the order" />;

  const names = supplierNames(job);
  const urls = supplierUrls(job);
  const order = job.request.suppliers.map((x) => x.id);
  const lanes = Object.values(s.lanes).sort((a, b) => order.indexOf(a.supplierId) - order.indexOf(b.supplierId));
  // All tiles have the same size. The highlighted one is the lane the owner picked, else the first
  // lane with a live browser, else the first lane.
  const watched = lanes.find((l) => l.laneId === picked) ?? lanes.find((l) => l.liveUrl) ?? lanes[0] ?? null;
  const call = callFromEvents(s.events);
  const distributor = job.request.distributor ? shortName(job.request.distributor.name) : "Distributor";
  const ready = !!job.results;
  // After results, the recommended plan; before, the best plan the events report so far.
  const bestId = ready ? job.results!.recommendedPlanId : s.bestPlan?.planId;
  const best = bestId ? (job.results?.plans ?? job.plans).find((p) => p.planId === bestId) : null;
  const bestTotal = best?.total ?? s.bestPlan?.total ?? null;
  // The browsers and the call share one grid that fits the screen: 3 stores + the call = 2 x 2.
  const tiles = Math.max(1, lanes.length) + (job.request.distributor ? 1 : 0);

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

      <div className="work fit">
        <div className="tiles" style={{ "--tile-cols": tiles <= 4 ? 2 : 3 } as React.CSSProperties}>
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
          {lanes.length === 0 && <div className="skeleton" />}
          {job.request.distributor && <CallPanel call={call} who={distributor} />}
        </div>

        <aside className="work-side">
          {bestTotal != null && (
            <div className="bestbox" aria-live="polite">
              <div>
                <p className="k">{ready ? "Best plan" : "Best so far"}</p>
                {best && <p className="v">{planShort(best, job)}</p>}
              </div>
              <span className="n"><Roll text={money(bestTotal)} /></span>
            </div>
          )}
          <OrderBasis items={job.request.items} planner={planner} />
        </aside>
      </div>
    </>
  );
}
