import type { ProductForecast } from "@/api/client";
import { day } from "@/lib/format";

// Weekly sales (ink line), expected range (mint band, history and future) and misses (red dots).
export function ForecastChart({ product, width = 880, height = 180 }: { product: ProductForecast; width?: number; height?: number }) {
  const hist = product.history ?? [];
  const fut = product.forecast ?? [];
  const n = hist.length + fut.length;
  if (n === 0) return <p className="muted">No sales yet.</p>;

  const pad = { l: 0, r: 0, t: 8, b: 28 };
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
      aria-label={`${product.name}: ${hist.length} weeks of sales, a ${fut.length}-week forecast and ${misses} misses.`}
      style={{ display: "block", overflow: "visible" }}
    >
      {[0.25, 0.5, 0.75, 1].map((t) => (
        <line key={t} x1={0} x2={width} y1={y(maxY * t)} y2={y(maxY * t)} stroke="#ECECEC" />
      ))}
      <line x1={0} x2={width} y1={y(0)} y2={y(0)} stroke="#D4D4D4" />
      {bandPath && <path d={bandPath} fill="#E8F7EF" />}
      {fut.length > 0 && hist.length > 0 && (
        <line x1={x(hist.length - 1)} x2={x(hist.length - 1)} y1={pad.t} y2={y(0)} stroke="#D4D4D4" strokeDasharray="2 3" />
      )}
      <path d={future} fill="none" stroke="#6B6B6B" strokeWidth={1.5} strokeDasharray="4 3" />
      <path d={actual} fill="none" stroke="#0A0A0A" strokeWidth={1.5} strokeLinejoin="round" />
      {hist.map((h, i) =>
        h.miss ? (
          <circle key={h.weekStart} cx={x(i)} cy={y(h.actual)} r={3.5} fill="#C8281E">
            <title>{`Week of ${day(h.weekStart)}: ${Math.round(h.actual)} sold, expected ${Math.round(h.low ?? 0)}–${Math.round(h.high ?? 0)}`}</title>
          </circle>
        ) : null,
      )}
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
