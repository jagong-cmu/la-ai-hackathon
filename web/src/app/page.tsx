"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, errorText, money, type DiscoveryResult, type SpendInsights } from "@/api/client";

export default function InsightsPage() {
  const [data, setData] = useState<SpendInsights | null>(null);
  const [suppliers, setSuppliers] = useState<DiscoveryResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.insights().then(setData).catch((e) => setError(errorText(e)));
    // Start discovery at the same time: it can take minutes when it runs live.
    api.suppliers().then(setSuppliers).catch(() => setSuppliers(null));
  }, []);

  if (error) return <p className="error" role="alert">{error}</p>;
  if (!data) return <p>Loading spend insights...</p>;

  return (
    <>
      <h1>Spend insights</h1>
      {data.disclosure && <p className="disclosure">{data.disclosure}</p>}
      <p>
        Supply spend in the last {data.periodDays} days: <strong>{money(data.supplySpend)}</strong> in {data.supplyOrders} orders
        ({data.otherLines} other bill lines).
      </p>
      <table>
        <thead>
          <tr>
            <th>Item</th><th>Category</th><th>Past unit price</th><th>Usual order</th><th>Cadence</th><th>Year spend</th><th>Source</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((it) => (
            <tr key={it.itemId}>
              <td>{it.billItemName}</td>
              <td>{it.category}</td>
              <td>${it.pastUnitPrice.toFixed(4)} / {it.unit}</td>
              <td>{it.pastOrderQty.toLocaleString()}</td>
              <td>{it.orderCadenceDays != null ? `${it.orderCadenceDays} days` : "-"}</td>
              <td>{money(it.yearSpend)}</td>
              <td>{it.synthetic ? "synthetic" : "books"} ({it.sourceBillIds.length} bills)</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted">
        Suppliers: {suppliers
          ? `found ${suppliers.candidates.length}, using ${suppliers.candidates.filter((c) => c.selected).length} (${suppliers.source}${suppliers.ageS != null ? `, cache age ${Math.round(suppliers.ageS)} s` : ""})`
          : "discovery running..."}
      </p>
      <p>
        <Link href="/list">Find cheaper</Link>
      </p>
    </>
  );
}
