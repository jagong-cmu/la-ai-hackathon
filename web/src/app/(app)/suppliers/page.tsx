"use client";
import { useEffect, useState } from "react";
import { api, errorText, type DiscoveryResult } from "@/api/client";
import { Phone } from "@/components/Icons";
import { ErrorNote, Loading, PageHead } from "@/components/PageHead";
import { Status } from "@/components/Status";
import { cleanName, host } from "@/lib/format";
import { reasonWord } from "@/lib/job";

function Score({ p }: { p: number | undefined }) {
  if (p == null) return <span className="muted">—</span>;
  return (
    <span className="row" style={{ gap: 10, flexWrap: "nowrap", justifyContent: "flex-end" }}>
      <span className="bar" style={{ width: 56 }}><i style={{ width: `${p * 100}%` }} /></span>
      <span className="small ink-2 tnum" style={{ width: 28, textAlign: "right" }}>{p.toFixed(2)}</span>
    </span>
  );
}

const SKIP: Record<string, string> = { "known adapter": "", "llm agent": "" };

export default function SuppliersPage() {
  const [data, setData] = useState<DiscoveryResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.suppliers().then(setData).catch((e) => setError(errorText(e)));
  }, []);

  if (!data) return error ? <ErrorNote error={error} /> : <Loading label="Finding suppliers" />;
  const used = data.candidates.filter((c) => c.selected).length;
  const d = data.distributor;

  return (
    <>
      <PageHead title="Suppliers" meta={`${data.candidates.length} found near ${data.zipCode} · ${used} used`} />
      <div className="tbl-wrap">
        <table className="tbl wide">
          <thead>
            <tr>
              <th>Supplier</th>
              <th>Sells this</th>
              <th>Public prices</th>
              <th>Ships to you</th>
              <th className="l">Status</th>
            </tr>
          </thead>
          <tbody>
            {data.candidates.map((c) => {
              const j = (c.jev ?? {}) as Record<string, number>;
              const why = SKIP[c.reason ?? ""] ?? reasonWord(c.reason);
              return (
                <tr key={c.id} className={c.selected ? undefined : "dim"}>
                  <td>
                    <span className={c.selected ? "strong" : undefined}>{cleanName(c.name)}</span>
                    <span className="sub">{host(c.url)}</span>
                  </td>
                  <td><Score p={j.sells_category} /></td>
                  <td><Score p={j.prices_without_login} /></td>
                  <td><Score p={j.ships_to_business} /></td>
                  <td className="l">
                    {c.selected ? <Status tone="verified">{c.adapter ? "Used" : "Used · new site"}</Status> : <span className="small">Skipped{why ? ` · ${why}` : ""}</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {d && (
        <div className="tbl-wrap">
          <div className="tbl-caption">Distributor</div>
          <table className="tbl">
            <tbody>
              <tr>
                <td>
                  <span className="strong">{cleanName(d.name)}</span>
                  <span className="sub">{[d.phone, d.email].filter(Boolean).join(" · ")}</span>
                </td>
                <td><span className="status"><Phone /> We call</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
