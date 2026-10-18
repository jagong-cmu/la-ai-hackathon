"use client";
// Landing. Black ground, one plain claim, then the real Orders table from the API.
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, money, type JobView } from "@/api/client";
import { ArrowRight } from "@/components/Icons";
import { Status } from "@/components/Status";
import { tsDay } from "@/lib/format";
import { headlinePlan, jobStateWord, orderTitle } from "@/lib/job";
import "./landing.css";

export default function Landing() {
  const [jobs, setJobs] = useState<JobView[] | null>(null);
  useEffect(() => {
    api.jobs(6).then(setJobs).catch(() => setJobs([]));
  }, []);

  return (
    <div className="land">
      <header className="land-top">
        <span className="land-mark">SOURN</span>
        <Link href="/planner" className="btn btn-primary">Open app</Link>
      </header>
      <section className="land-hero">
        <h1>Restocking that runs itself for bakeries</h1>
        <p>Forecasts what runs out, prices it at every supplier, and orders when you approve.</p>
        <div className="row" style={{ justifyContent: "center", gap: 8 }}>
          <Link href="/planner" className="btn btn-primary has-icon">Open planner <ArrowRight /></Link>
          <Link href="/orders" className="btn btn-on-black">See orders</Link>
        </div>
      </section>
      {jobs && jobs.length > 0 && (
        <div className="land-device">
          <div className="land-screen">
            <div className="land-screen-head">
              <h2>Orders</h2>
            </div>
            <div style={{ overflowX: "auto" }}>
              <table className="tbl wide">
                <thead>
                  <tr><th>Order</th><th>Total</th><th className="l">Price</th><th className="l">Status</th><th>Date</th></tr>
                </thead>
                <tbody>
                  {jobs.map((j) => {
                    const plan = headlinePlan(j);
                    return (
                      <tr key={j.jobId}>
                        <td>{orderTitle(j)}</td>
                        <td className="best">{plan ? money(plan.total) : "—"}</td>
                        <td className="l">{plan && <Status status={plan.status} />}</td>
                        <td className="l ink-2">{jobStateWord(j.state)}</td>
                        <td className="muted">{tsDay(j.createdAt)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
