"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, errorText, type SpendInsights } from "@/api/client";
import { ArrowRight } from "@/components/Icons";
import { ErrorNote, Loading, PageHead } from "@/components/PageHead";
import { Roll } from "@/components/Roll";
import { each, int, itemShort } from "@/lib/format";

const whole = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

export default function SpendPage() {
  const [data, setData] = useState<SpendInsights | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.insights().then(setData).catch((e) => setError(errorText(e)));
  }, []);

  if (!data) return error ? <ErrorNote error={error} /> : <Loading label="Loading spend" />;

  const items = [...data.items].sort((a, b) => b.yearSpend - a.yearSpend);
  const top = Math.max(1, ...items.map((i) => i.yearSpend));

  return (
    <>
      <PageHead
        title="Spend"
        right={<p className="figure"><Roll text={whole(data.supplySpend)} fromZero /></p>}
      />
      <div className="tbl-wrap">
        <table className="tbl wide">
          <thead>
            <tr>
              <th>Item</th>
              <th>Paid each</th>
              <th>Usual order</th>
              <th>Every</th>
              <th style={{ width: 200 }}><span className="sr-only">Share</span></th>
              <th>This year</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it) => (
              <tr key={it.itemId}>
                <td>{itemShort(it.billItemName)}<span className="sub">{it.billItemName.split(",").slice(1).join(",").trim()}</span></td>
                <td>{each(it.pastUnitPrice)}</td>
                <td>{int(it.pastOrderQty)}</td>
                <td>{it.orderCadenceDays != null ? `${Math.round(it.orderCadenceDays / 7)} wk` : ""}</td>
                <td>
                  <span className="bar" style={{ display: "block" }}>
                    <i style={{ width: `${(it.yearSpend / top) * 100}%` }} />
                  </span>
                </td>
                <td className="best">{whole(it.yearSpend)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <Link href="/orders/new" className="btn btn-primary has-icon">Find cheaper <ArrowRight /></Link>
      </div>
    </>
  );
}
