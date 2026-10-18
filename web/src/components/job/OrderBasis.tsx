"use client";
// Why the order holds these quantities: the planner's demand forecast (from the QuickBooks sales) and
// the prices on the owner's past bills. Shown on the new-order and working screens.
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, type PlannerView, type ShoppingItem, type SupplyPlan } from "@/api/client";
import { day, each, int, itemShort } from "@/lib/format";

/** The planner, or null while it loads or when it fails (the order still shows past prices). */
export function usePlanner(): PlannerView | null {
  const [view, setView] = useState<PlannerView | null>(null);
  useEffect(() => {
    let live = true;
    api.planner().then((v) => live && setView(v)).catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);
  return view;
}

const plural = (unit: string, n: number) =>
  n === 1 || unit.endsWith("s") ? unit : /(x|ch|sh)$/.test(unit) ? `${unit}es` : `${unit}s`;

/** "~985 a week · runs out Oct 27", or "" when the planner has no plan for the item. */
export function forecastText(s: SupplyPlan | undefined): string {
  const week = s?.weeklyUseForecast[0]?.mean;
  if (!s || week == null) return "";
  return [`~${int(week)} a week`, s.runOutDate && `runs out ${day(s.runOutDate)}`].filter(Boolean).join(" · ");
}

function quantityNote(it: ShoppingItem, s: SupplyPlan | undefined): string {
  if (!s || s.recommendedQty <= 0) return "";
  if (it.quantity === s.recommendedQty) {
    const week = s.weeklyUseForecast[0]?.mean;
    const weeks = week ? Math.round(it.quantity / week) : 0;
    return weeks >= 1 ? `Forecast amount · covers ~${weeks} wk` : "Forecast amount";
  }
  return `Forecast said ${int(s.recommendedQty)}`;
}

export function OrderBasis({ items, planner }: { items: ShoppingItem[]; planner: PlannerView | null }) {
  const plans = Object.fromEntries((planner?.supplies ?? []).map((s) => [s.itemId, s]));
  const shown = items.filter((i) => i.quantity > 0);
  if (shown.length === 0) return null;
  return (
    <section className="card basis" aria-label="Why this order">
      <div className="basis-head">
        <span className="label">Why this order</span>
        <Link href="/planner" className="link small">Planner</Link>
      </div>
      <div className="basis-rows">
        {shown.map((it) => {
          const s = plans[it.id];
          const bills = it.sourceBillIds.length;
          return (
            <div key={it.id} className="basis-row">
              <p className="strong">{int(it.quantity)} {plural(it.unit, it.quantity)} · {itemShort(it.name).toLowerCase()}</p>
              <dl>
                <div>
                  <dt>Demand forecast</dt>
                  <dd>{forecastText(s) || (planner ? "No forecast for this item" : "Loading")}</dd>
                  {quantityNote(it, s) && <dd className="muted">{quantityNote(it, s)}</dd>}
                </div>
                <div>
                  <dt>Last paid</dt>
                  <dd>{it.pastUnitPrice != null ? `${each(it.pastUnitPrice)} each` : "No past bill"}</dd>
                  {bills > 0 && (
                    <dd className="muted">
                      {bills} past {bills === 1 ? "bill" : "bills"}
                      {it.orderCadenceDays != null && ` · every ${Math.max(1, Math.round(it.orderCadenceDays / 7))} wk`}
                    </dd>
                  )}
                </div>
              </dl>
            </div>
          );
        })}
      </div>
      {planner?.disclosure && <p className="small muted">{planner.disclosure}</p>}
    </section>
  );
}
