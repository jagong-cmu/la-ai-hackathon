"use client";
// The money this plan saves against the prices on the owner's QuickBooks bills, item by item.
import { money, type JobView, type Plan } from "@/api/client";
import { useBills } from "@/hooks/useData";
import { each, int, itemShort, month, shortName } from "@/lib/format";
import { supplierNames } from "@/lib/job";
import { pct, planSavings } from "@/lib/savings";

function median(xs: number[]): number | null {
  if (xs.length === 0) return null;
  const s = [...xs].sort((a, b) => a - b);
  return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
}

export function SavingsBreakdown({ job, plan }: { job: JobView; plan: Plan }) {
  const bills = useBills();
  const s = planSavings(job, plan);
  if (!s) return null;
  const names = supplierNames(job);
  const top = Math.max(...s.rows.map((r) => Math.max(r.paidBefore, r.now)), 0.01);
  const cadence = median(s.rows.map((r) => r.item.orderCadenceDays ?? 0).filter((d) => d > 0));
  const yearly = cadence ? s.saved * (365 / cadence) : null;
  const ids = new Set(s.rows.map((r) => r.item.id));
  const mine = (bills ?? []).filter((b) => ids.has(b.itemId));
  const billCount = new Set(mine.map((b) => b.billId)).size;
  const up = s.saved >= 0;

  return (
    <section className="card savings" aria-label="Savings against your bills">
      <div className="savings-head">
        <div>
          <span className="label">{up ? "You save" : "Costs more"} against your QuickBooks bills</span>
          <p className={`savings-figure${up ? "" : " neg"}`}>
            {money(Math.abs(s.saved))}
            <span>{pct(s.pct)} {up ? "less" : "more"}</span>
          </p>
        </div>
        <dl className="savings-sum">
          <div><dt>Paid before</dt><dd>{money(s.paidBefore)}</dd></div>
          <div><dt>This plan</dt><dd>{money(s.now)}</dd></div>
          {yearly != null && up && <div><dt>A year at your order rate</dt><dd className="mint">~{money(yearly)}</dd></div>}
        </dl>
      </div>

      <div className="savings-rows">
        {s.rows.map((r) => {
          const saved = r.paidBefore - r.now;
          return (
            <div key={r.item.id} className="savings-row">
              <div className="savings-item">
                <b>{itemShort(r.item.name)}</b>
                <span className="small ink-2">{int(r.item.quantity)} · from {shortName(names[r.supplierId] ?? r.supplierId)}{r.source === "web" ? "" : " (phone)"}</span>
              </div>
              <div className="savings-bars" aria-hidden="true">
                <span className="was" style={{ width: `${(r.paidBefore / top) * 100}%` }} />
                <span className="now" style={{ width: `${(r.now / top) * 100}%` }} />
              </div>
              <div className="savings-unit small tnum">
                <span className="muted">{each(r.unitBefore)}</span> → <b>{each(r.unitNow)}</b> each
              </div>
              <div className={`savings-amt tnum${saved < 0 ? " neg" : ""}`}>
                {saved >= 0 ? "−" : "+"}{money(Math.abs(saved))}
              </div>
            </div>
          );
        })}
        {s.extras >= 0.005 && (
          <div className="savings-row extras">
            <div className="savings-item"><b>Shipping and tax</b></div>
            <div />
            <div />
            <div className="savings-amt tnum neg">+{money(s.extras)}</div>
          </div>
        )}
      </div>
    </section>
  );
}
