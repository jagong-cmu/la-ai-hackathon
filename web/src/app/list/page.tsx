"use client";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api, errorText, type ShoppingRequest } from "@/api/client";

export default function ListPage() {
  const router = useRouter();
  const [req, setReq] = useState<ShoppingRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // One key per submit attempt on this screen: a retry after a network error reuses it.
  const idemKey = useRef<string | null>(null);

  useEffect(() => {
    api.shoppingDraft().then(setReq).catch((e) => setError(errorText(e)));
  }, []);

  function setQty(i: number, value: string) {
    if (!req) return;
    const q = Math.max(0, Math.floor(Number(value) || 0));
    const items = req.items.map((it, j) => (j === i ? { ...it, quantity: q } : it));
    setReq({ ...req, items });
    idemKey.current = null; // the request changed, so it is a new job
  }

  function removeItem(i: number) {
    if (!req) return;
    setReq({ ...req, items: req.items.filter((_, j) => j !== i) });
    idemKey.current = null;
  }

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

  if (!req) return <>{error ? <p className="error" role="alert">{error}</p> : <p>Loading shopping list...</p>}</>;

  return (
    <>
      <h1>Shopping list</h1>
      <table>
        <thead>
          <tr><th>Item</th><th>Quantity</th><th>Unit</th><th>Past unit price</th><th>Spec</th><th /></tr>
        </thead>
        <tbody>
          {req.items.map((it, i) => (
            <tr key={it.id}>
              <td>{it.name}</td>
              <td>
                <input id={`qty-${it.id}`} aria-label={`Quantity for ${it.name}`} type="number" min={0} step={1} value={it.quantity} onChange={(e) => setQty(i, e.target.value)} />
              </td>
              <td>{it.unit}</td>
              <td>{it.pastUnitPrice != null ? `$${it.pastUnitPrice.toFixed(4)}` : "-"}</td>
              <td>
                {it.spec ? (
                  <span className="muted">{[it.spec.query, ...(it.spec.mustHave ?? [])].join(" · ")}</span>
                ) : (
                  <span className="muted">spec at run time</span>
                )}
              </td>
              <td><button onClick={() => removeItem(i)} aria-label={`Remove ${it.name}`}>Remove</button></td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Suppliers ({req.suppliers.length})</h2>
      <ul>
        {req.suppliers.map((s) => (
          <li key={s.id}>
            {s.name} <span className="muted">({s.url}; {s.adapter ? `adapter ${s.adapter}` : "LLM agent"}{s.origin ? `; ${s.origin}` : ""})</span>
          </li>
        ))}
      </ul>
      {req.distributor && <p>Distributor: {req.distributor.name}</p>}

      <p>
        <label>
          Delivery ZIP code{" "}
          <input value={req.zipCode} onChange={(e) => setReq({ ...req, zipCode: e.target.value })} style={{ width: "6em" }} />
        </label>
      </p>
      <p>
        <label>
          <input
            type="checkbox"
            checked={!!req.acceptQuotedDefault}
            onChange={(e) => setReq({ ...req, acceptQuotedDefault: e.target.checked })}
          />{" "}
          Accept quoted offers by default
        </label>
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      <button onClick={submit} disabled={busy || req.items.every((it) => it.quantity <= 0)}>
        {busy ? "Starting..." : "Compare suppliers"}
      </button>
    </>
  );
}
