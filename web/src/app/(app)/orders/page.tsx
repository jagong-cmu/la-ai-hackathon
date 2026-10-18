"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, errorText, money, type JobView } from "@/api/client";
import { Plus } from "@/components/Icons";
import { ErrorNote, Loading, PageHead } from "@/components/PageHead";
import { Status } from "@/components/Status";
import { tsDay } from "@/lib/format";
import { headlinePlan, jobHref, jobStateWord, orderTitle, reasonWord, supplierNames } from "@/lib/job";

function suppliersOf(job: JobView): string {
  const plan = headlinePlan(job);
  const names = supplierNames(job);
  if (!plan) return "";
  return [...new Set(plan.assignments.map((a) => names[a.supplierId] ?? a.supplierId))].join(", ");
}

export default function OrdersPage() {
  const router = useRouter();
  const [jobs, setJobs] = useState<JobView[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.jobs().then(setJobs).catch((e) => setError(errorText(e)));
  }, []);

  if (!jobs) return error ? <ErrorNote error={error} /> : <Loading label="Loading orders" />;

  return (
    <>
      <PageHead
        title="Orders"
        right={<Link href="/orders/new" className="btn btn-secondary"><Plus strokeWidth={2} /> New order</Link>}
      />
      <div className="tbl-wrap">
        <table className="tbl wide">
          <thead>
            <tr>
              <th>Order</th>
              <th className="l">Suppliers</th>
              <th>Total</th>
              <th className="l">Price</th>
              <th className="l">Status</th>
              <th>Date</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => {
              const plan = headlinePlan(j);
              const blocked = j.state === "failed" ? j.lanes.find((l) => l.state === "failed" || l.state === "blocked") : null;
              const href = jobHref(j);
              return (
                <tr key={j.jobId} className="click" onClick={() => router.push(href)}>
                  <td><Link href={href} style={{ textDecoration: "none" }}>{orderTitle(j)}</Link></td>
                  <td className="l ink-2">{suppliersOf(j)}</td>
                  <td className="best">{plan ? money(plan.total) : <span className="muted">—</span>}</td>
                  <td className="l">{j.state === "failed" ? <Status tone="failed">Failed</Status> : plan ? <Status status={plan.status} /> : null}</td>
                  <td className={`l${["rejected", "failed"].includes(j.state) ? " dim" : ""}`}>
                    {blocked ? reasonWord(blocked.reason) : jobStateWord(j.state)}
                  </td>
                  <td className="muted">{tsDay(j.createdAt)}</td>
                </tr>
              );
            })}
            {jobs.length === 0 && (
              <tr><td colSpan={6} className="empty">No orders yet</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
