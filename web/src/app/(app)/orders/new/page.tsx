"use client";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api, errorText, type ShoppingRequest } from "@/api/client";
import { ArrowRight, Minus, Plus } from "@/components/Icons";
import { ErrorNote, Loading, PageHead } from "@/components/PageHead";
import { cleanName, each, itemShort } from "@/lib/format";

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
  // One key per submit attempt on this screen: a retry after a network error reuses it.
  const idemKey = useRef<string | null>(null);

  useEffect(() => {
    api.shoppingDraft().then(setReq).catch((e) => setError(errorText(e)));
  }, []);

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

  return (
    <>
      <PageHead title="New order" crumb={{ href: "/orders", label: "Orders", current: "New order" }} />

      <div className="tbl-wrap">
        <table className="tbl wide">
          <thead>
            <tr>
              <th>Item</th>
              <th className="l">Last paid</th>
              <th className="l">Quantity</th>
              <th><span className="sr-only">Remove</span></th>
            </tr>
          </thead>
          <tbody>
            {req.items.map((it, i) => (
              <tr key={it.id}>
                <td>
                  <span className="strong">{itemShort(it.name)}</span>
                  <span className="sub">{it.name.split(",").slice(1).join(",").trim()}</span>
                </td>
                <td className="l muted">{it.pastUnitPrice != null ? `${each(it.pastUnitPrice)} each` : ""}</td>
                <td className="l">
                  <Stepper value={it.quantity} step={step(it.unit, it.quantity)} label={itemShort(it.name).toLowerCase()} onChange={(v) => setQty(i, v)} />
                </td>
                <td>
                  <button className="btn btn-ghost" onClick={() => remove(i)} aria-label={`Remove ${itemShort(it.name)}`}>Remove</button>
                </td>
              </tr>
            ))}
            {req.items.length === 0 && (
              <tr><td colSpan={4} className="empty">No items</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="cols" style={{ "--cols": "minmax(0, 200px) minmax(0, 1fr)" } as React.CSSProperties}>
        <label className="field">
          <span>Deliver to</span>
          <input className="input" inputMode="numeric" value={req.zipCode} onChange={(e) => update({ ...req, zipCode: e.target.value })} />
        </label>
        <div className="field">
          <span>Who we ask</span>
          <div className="tbl-wrap">
            <table className="tbl">
              <tbody>
                {req.suppliers.map((s) => (
                  <tr key={s.id}>
                    <td>{cleanName(s.name)}</td>
                    <td className="small muted">{s.adapter ? "Website" : "Website · new"}</td>
                  </tr>
                ))}
                {req.distributor && (
                  <tr>
                    <td>{cleanName(req.distributor.name)}</td>
                    <td className="small muted">Phone call</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <ErrorNote error={error} />
      <div className="actions">
        <button className="btn btn-primary has-icon" onClick={submit} disabled={busy || req.items.every((it) => it.quantity <= 0)}>
          {busy ? "Starting" : "Find best price"} <ArrowRight />
        </button>
      </div>
    </>
  );
}
