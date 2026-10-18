import type { BillLine } from "@/api/client";
import { day, each, int, month } from "@/lib/format";

// The unit price on each QuickBooks bill (ink dots on a time axis), the average paid (dashed) and
// the best price found by the agents (mint). The gap between the two lines is the saving each.
export function PriceHistoryChart({
  lines,
  avg,
  best,
  width = 880,
  height = 200,
}: {
  lines: BillLine[];
  avg: number | null;
  best: number | null;
  width?: number;
  height?: number;
}) {
  if (lines.length === 0) return <p className="muted">No bills for this item.</p>;
  const pad = { l: 56, r: 96, t: 12, b: 28 };
  const t = (d: string) => new Date(d + "T00:00:00Z").getTime();
  const t0 = t(lines[0].txnDate);
  const t1 = Math.max(t0 + 86_400_000, t(lines[lines.length - 1].txnDate));
  const prices = [...lines.map((l) => l.unitPrice), ...(avg != null ? [avg] : []), ...(best != null ? [best] : [])];
  const lo = Math.min(...prices);
  const hi = Math.max(...prices);
  const span = Math.max(hi - lo, hi * 0.05);
  const minY = Math.max(0, lo - span * 0.25);
  const maxY = hi + span * 0.25;
  const x = (d: string) => pad.l + ((t(d) - t0) / (t1 - t0)) * (width - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - (v - minY) / (maxY - minY)) * (height - pad.t - pad.b);
  const path = "M" + lines.map((l) => `${x(l.txnDate)},${y(l.unitPrice)}`).join(" L");
  const ticks = [0, 0.5, 1].map((f) => new Date(t0 + f * (t1 - t0)).toISOString().slice(0, 10));
  const maxQty = Math.max(...lines.map((l) => l.quantity));

  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" style={{ display: "block", overflow: "visible" }}
      aria-label={`Unit price on ${lines.length} bills, from ${each(lo)} to ${each(hi)}${best != null ? `; best price found ${each(best)}` : ""}.`}>
      {[minY + (maxY - minY) * 0.2, minY + (maxY - minY) * 0.8].map((v) => (
        <g key={v}>
          <line x1={pad.l} x2={width - pad.r} y1={y(v)} y2={y(v)} stroke="#ECECEC" />
          <text x={pad.l - 8} y={y(v) + 4} fontSize="12" fill="#6B6B6B" textAnchor="end">{each(v)}</text>
        </g>
      ))}
      <path d={path} fill="none" stroke="#A3A3A3" strokeWidth={1} />
      {avg != null && (
        <g>
          <line x1={pad.l} x2={width - pad.r} y1={y(avg)} y2={y(avg)} stroke="#0A0A0A" strokeDasharray="4 3" />
          <text x={width - pad.r + 8} y={y(avg) + 4} fontSize="12" fill="#0A0A0A">Avg paid {each(avg)}</text>
        </g>
      )}
      {best != null && (
        <g>
          <line x1={pad.l} x2={width - pad.r} y1={y(best)} y2={y(best)} stroke="#1F7A55" strokeWidth={2} />
          <text x={width - pad.r + 8} y={y(best) + 4} fontSize="12" fontWeight={600} fill="#1F7A55">Found {each(best)}</text>
        </g>
      )}
      {lines.map((l) => (
        <circle key={l.billId + l.itemId} cx={x(l.txnDate)} cy={y(l.unitPrice)} r={2.5 + 2.5 * (l.quantity / maxQty)} fill="#0A0A0A">
          <title>{`${day(l.txnDate)} · ${l.vendorName}: ${int(l.quantity)} at ${each(l.unitPrice)} = $${l.amount.toFixed(2)}`}</title>
        </circle>
      ))}
      {ticks.map((d, k) => (
        <text key={k} x={pad.l + (k / 2) * (width - pad.l - pad.r)} y={height - 6} fontSize="12" fill="#6B6B6B"
          textAnchor={k === 0 ? "start" : k === 2 ? "end" : "middle"}>
          {month(d)}
        </text>
      ))}
    </svg>
  );
}
