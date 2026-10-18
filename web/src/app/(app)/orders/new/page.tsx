"use client";
// The order to price: one line per supply (quantity, why, and its cost at the bill price), and a
// summary column with where the agents look and the start button.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api, errorText, money, type ShoppingRequest } from "@/api/client";
import { ArrowRight, Minus, Phone, Plus } from "@/components/Icons";
import { ErrorNote, Loading, PageHead } from "@/components/PageHead";
import { forecastText, usePlanner } from "@/components/job/OrderBasis";
import { cleanName, each, host, int, itemShort } from "@/lib/format";

function Stepper({ value, step, label, onChange }: { value: number; step: number; label: string; onChange: (v: number) => void }) {
  return (
    <span className="step">
      <button type="button" aria-label={`Fewer ${label}`} onClick={() => onChange(Math.max(0, value - step))}><Minus /></button>
      <input
        aria-label={`Quantity of ${label}`}
        inputMode="numeric"
        value={value.toLocaleString("en-US")}
        onChange={(e) => onChange(Math.max(0, Math.floor(Number(e.target.value.replace(/[^\d]/g, "")) || 0)))}
      />
      <button type="button" aria-label={`More ${label}`} onClick={() => onChange(value + step)}><Plus /></button>
    </span>
  );
}

export default function NewOrderPage() {
  const router = useRouter();
  const [req, setReq] = useState<ShoppingRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const planner = usePlanner();
  // One key per submit attempt on this screen: a retry after a network error reuses it.
  const idemKey = useRef<string | null>(null);

  useEffect(() => {
    api.shoppingDraft().then(setReq).catch((e) => setError(errorText(e)));
  }, []);

  const plans = Object.fromEntries((planner?.supplies ?? []).map((s) => [s.itemId, s]));
  if (!req) return error ? <ErrorNote error={error} /> : <Loading label="Loading the order" />;

  function update(next: ShoppingRequest) {
    setReq(next);
    idemKey.current = null; // the request changed, so it is a new job
  }
  const setQty = (i: number, q: number) => update({ ...req!, items: req!.items.map((it, j) => (j === i ? { ...it, quantity: q } : it)) });
  const remove = (i: number) => update({ ...req!, items: req!.items.filter((_, j) => j !== i) });

  async function submit() {
    if (!req) return;
    setBusy(true);
    setError(null);
    idemKey.current ??= crypto.randomUUID();
    try {
      const body: ShoppingRequest = { ...req, items: req.items.filter((it) => it.quantity > 0) };
      const created = await api.createJob(body, idemKey.current);
      router.push(`/jobs/${created.jobId}`);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  const step = (unit: string, q: number) => (q >= 1000 ? 500 : q >= 100 ? 50 : unit === "case" ? 1 : 10);
  const live = req.items.filter((it) => it.quantity > 0);
  const atBills = live.reduce((n, it) => n + it.quantity * (it.pastUnitPrice ?? 0), 0);
  const sources = req.suppliers.length + (req.distributor ? 1 : 0);

  return (
    <>
      <PageHead title="New order" crumb={{ href: "/orders", label: "Orders", current: "New order" }} />

      <div className="order">
        <section className="order-lines" aria-label="Items">
          {req.items.map((it, i) => {
            const plan = plans[it.id];
            const suggested = plan && plan.recommendedQty > 0 ? plan.recommendedQty : null;
            const why = forecastText(plan);
            const label = itemShort(it.name).toLowerCase();
            return (
              <div key={it.id} className={`oline${it.quantity <= 0 ? " off" : ""}`}>
                <div className="oline-what">
                  <Link href={`/planner/${encodeURIComponent(it.id)}`} className="oline-name">{itemShort(it.name)}</Link>
                  <span className="oline-spec">{it.name.split(",").slice(1).join(",").trim()}</span>
                  <span className="oline-why">
                    {why || (planner ? "No forecast" : "")}
                    {suggested != null && it.quantity !== suggested && (
                      <>
                        {" · "}
                        <button type="button" className="textbtn" onClick={() => setQty(i, suggested)}>Use suggested {int(suggested)}</button>
                      </>
                    )}
                  </span>
                </div>
                <Stepper value={it.quantity} step={step(it.unit, it.quantity)} label={label} onChange={(v) => setQty(i, v)} />
                <div className="oline-cost">
                  <b className="tnum">{it.pastUnitPrice != null ? money(it.quantity * it.pastUnitPrice) : "–"}</b>
                  <span>{it.pastUnitPrice != null ? `${each(it.pastUnitPrice)} each on bills` : "No past bill"}</span>
                </div>
                <button type="button" className="oline-x" onClick={() => remove(i)} aria-label={`Remove ${label}`}>
                  <Plus />
                </button>
              </div>
            );
          })}
          {req.items.length === 0 && <p className="empty">No items</p>}
          <div className="order-foot">
            <span>At your bill prices</span>
            <b className="tnum">{money(atBills)}</b>
          </div>
        </section>

        <aside className="order-side" aria-label="Summary">
          <div className="order-card">
            <p className="label">The agents check {sources} {sources === 1 ? "source" : "sources"}</p>
            <ul className="sources">
              {req.suppliers.map((s) => (
                <li key={s.id}>
                  <span className="src-mark" aria-hidden="true">{cleanName(s.name)[0]}</span>
                  <span className="src-name">
                    {cleanName(s.name)}
                    <span>{host(s.url).replace(/^www\./, "").replace(/\/$/, "")}{s.adapter ? "" : " · new site"}</span>
                  </span>
                </li>
              ))}
              {req.distributor && (
                <li>
                  <span className="src-mark phone" aria-hidden="true"><Phone /></span>
                  <span className="src-name">
                    {cleanName(req.distributor.name)}
                    <span>Phone call for a quote</span>
                  </span>
                </li>
              )}
            </ul>
            <label className="zip">
              <span>Deliver to</span>
              <input className="input" inputMode="numeric" value={req.zipCode} onChange={(e) => update({ ...req, zipCode: e.target.value })} />
            </label>
            <div className="order-total">
              <span>{live.length} {live.length === 1 ? "item" : "items"} · at bill prices</span>
              <b className="tnum">{money(atBills)}</b>
            </div>
            <ErrorNote error={error} />
            <button className="btn btn-primary has-icon" style={{ width: "100%" }} onClick={submit} disabled={busy || live.length === 0}>
              {busy ? "Starting" : "Find best price"} <ArrowRight />
            </button>
          </div>
        </aside>
      </div>
    </>
  );
}
