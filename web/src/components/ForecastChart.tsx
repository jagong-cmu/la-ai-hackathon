import type { ForecastPoint, HistoryPoint } from "@/api/client";
import { day, int } from "@/lib/format";

// Weekly amounts (ink line), expected range (mint band, history and future), the forecast mean
// (dashed) and misses (red dots). Product sales and supply use share this chart.
export function ForecastChart({
  name,
  history: hist,
  forecast: fut,
  unit = "sold",
  width = 880,
  height = 200,
}: {
  name: string;
  history: HistoryPoint[];
  forecast: ForecastPoint[];
  /** The verb for a week's amount in tooltips: "sold", "used". */
  unit?: string;
  width?: number;
  height?: number;
}) {
  const n = hist.length + fut.length;
  if (n === 0) return <p className="muted">No sales yet.</p>;

  const pad = { l: 44, r: 0, t: 8, b: 28 };
  const values: number[] = [];
  for (const h of hist) values.push(h.actual, h.high ?? 0);
  for (const f of fut) values.push(f.high, f.mean);
  const maxY = Math.max(1, ...values) * 1.05;
  const x = (i: number) => pad.l + (n === 1 ? 0 : (i / (n - 1)) * (width - pad.l - pad.r));
  const y = (v: number) => pad.t + (1 - v / maxY) * (height - pad.t - pad.b);

  const band: { i: number; low: number; high: number }[] = [];
  hist.forEach((h, i) => {
    if (h.low != null && h.high != null) band.push({ i, low: h.low, high: h.high });
  });
  fut.forEach((f, j) => band.push({ i: hist.length + j, low: f.low, high: f.high }));
  const bandPath =
    band.length > 1
      ? "M" + band.map((b) => `${x(b.i)},${y(b.high)}`).join(" L") +
        " L" + [...band].reverse().map((b) => `${x(b.i)},${y(b.low)}`).join(" L") + " Z"
      : "";
  const line = (pts: [number, number][]) => (pts.length ? "M" + pts.map(([i, v]) => `${x(i)},${y(v)}`).join(" L") : "");
  const actual = line(hist.map((h, i) => [i, h.actual]));
  const future = line([
    ...(hist.length ? [[hist.length - 1, hist[hist.length - 1].actual] as [number, number]] : []),
    ...fut.map((f, j) => [hist.length + j, f.mean] as [number, number]),
  ]);
  const weeks = [...hist.map((h) => h.weekStart), ...fut.map((f) => f.weekStart)];
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => Math.round(t * (n - 1)));
  const misses = hist.filter((h) => h.miss).length;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width="100%"
      role="img"
      aria-label={`${name}: ${hist.length} weeks of history, a ${fut.length}-week forecast and ${misses} misses.`}
      style={{ display: "block", overflow: "visible" }}
    >
      {[0.5, 1].map((t) => (
        <g key={t}>
          <line x1={pad.l} x2={width} y1={y(maxY * t / 1.05)} y2={y(maxY * t / 1.05)} stroke="#ECECEC" />
          <text x={pad.l - 8} y={y(maxY * t / 1.05) + 4} fontSize="12" fill="#6B6B6B" textAnchor="end">{int(maxY * t / 1.05)}</text>
        </g>
      ))}
      <line x1={pad.l} x2={width} y1={y(0)} y2={y(0)} stroke="#D4D4D4" />
      <text x={pad.l - 8} y={y(0) + 4} fontSize="12" fill="#6B6B6B" textAnchor="end">0</text>
      {bandPath && <path d={bandPath} fill="#E8F7EF" />}
      {fut.length > 0 && hist.length > 0 && (
        <line x1={x(hist.length - 1)} x2={x(hist.length - 1)} y1={pad.t} y2={y(0)} stroke="#D4D4D4" strokeDasharray="2 3" />
      )}
      <path d={future} fill="none" stroke="#6B6B6B" strokeWidth={1.5} strokeDasharray="4 3" />
      <path d={actual} fill="none" stroke="#0A0A0A" strokeWidth={1.5} strokeLinejoin="round" />
      {hist.map((h, i) => (
        <circle key={h.weekStart} cx={x(i)} cy={y(h.actual)} r={h.miss ? 3.5 : 6} fill={h.miss ? "#C8281E" : "transparent"}>
          <title>{`Week of ${day(h.weekStart)}: ${int(h.actual)} ${unit}${h.low != null ? `, expected ${int(h.low)}–${int(h.high)}` : ""}`}</title>
        </circle>
      ))}
      {fut.map((f, j) => (
        <circle key={f.weekStart} cx={x(hist.length + j)} cy={y(f.mean)} r={6} fill="transparent">
          <title>{`Week of ${day(f.weekStart)}: forecast ~${int(f.mean)} (${int(f.low)}–${int(f.high)})`}</title>
        </circle>
      ))}
      {ticks.map((i, k) => (
        <text
          key={k}
          x={x(i)}
          y={height - 6}
          fontSize="12"
          fill="#6B6B6B"
          textAnchor={k === 0 ? "start" : k === ticks.length - 1 ? "end" : "middle"}
        >
          {day(weeks[i])}
        </text>
      ))}
    </svg>
  );
}
