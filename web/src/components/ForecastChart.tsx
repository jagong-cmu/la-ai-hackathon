import type { ProductForecast } from "@/api/client";

// Plain SVG line chart: actual sales (solid), past forecast (dashed), forecast range
// (shaded band, history + 6-week future), and misses (red dots). No chart library.
export function ForecastChart({ product, width = 640, height = 200 }: { product: ProductForecast; width?: number; height?: number }) {
  const hist = product.history ?? [];
  const fut = product.forecast ?? [];
  const n = hist.length + fut.length;
  if (n === 0) return <p className="muted">No history.</p>;

  const pad = { l: 44, r: 8, t: 8, b: 20 };
  const values: number[] = [];
  for (const h of hist) values.push(h.actual, h.forecast ?? 0, h.high ?? 0);
  for (const f of fut) values.push(f.high, f.mean);
  const maxY = Math.max(1, ...values);
  const x = (i: number) => pad.l + (n === 1 ? 0 : (i / (n - 1)) * (width - pad.l - pad.r));
  const y = (v: number) => pad.t + (1 - v / maxY) * (height - pad.t - pad.b);

  // Range band across history (where known) and future.
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
  const actualPath = line(hist.map((h, i) => [i, h.actual]));
  const pastForecast = line(hist.flatMap((h, i) => (h.forecast != null ? [[i, h.forecast] as [number, number]] : [])));
  const futureMean = line([
    ...(hist.length ? [[hist.length - 1, hist[hist.length - 1].actual] as [number, number]] : []),
    ...fut.map((f, j) => [hist.length + j, f.mean] as [number, number]),
  ]);
  const firstLabel = hist[0]?.weekStart ?? fut[0]?.weekStart;
  const lastLabel = fut[fut.length - 1]?.weekStart ?? hist[hist.length - 1]?.weekStart;
  const misses = hist.filter((h) => h.miss).length;

  return (
    <figure style={{ margin: 0 }}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        role="img"
        aria-label={`${product.name}: ${hist.length} weeks of sales and a ${fut.length}-week forecast range. ${misses} misses marked.`}
      >
        <line x1={pad.l} x2={width - pad.r} y1={y(0)} y2={y(0)} stroke="#ccc" />
        <text x={pad.l - 4} y={y(maxY) + 10} fontSize="10" textAnchor="end">{Math.round(maxY)}</text>
        <text x={pad.l - 4} y={y(0)} fontSize="10" textAnchor="end">0</text>
        {hist.length > 0 && fut.length > 0 && (
          <line x1={x(hist.length - 0.5)} x2={x(hist.length - 0.5)} y1={pad.t} y2={y(0)} stroke="#bbb" strokeDasharray="2 3" />
        )}
        {bandPath && <path d={bandPath} fill="#5f7380" fillOpacity={0.15} stroke="none" />}
        <path d={pastForecast} fill="none" stroke="#5f7380" strokeDasharray="4 3" />
        <path d={futureMean} fill="none" stroke="#5f7380" strokeWidth={2} strokeDasharray="6 3" />
        <path d={actualPath} fill="none" stroke="#1a1a1a" strokeWidth={1.5} />
        {hist.map((h, i) =>
          h.miss ? (
            <circle key={h.weekStart} cx={x(i)} cy={y(h.actual)} r={3.5} fill="#c62828">
              <title>{`Miss, week of ${h.weekStart}: actual ${h.actual}, range ${h.low ?? "?"}-${h.high ?? "?"}`}</title>
            </circle>
          ) : null,
        )}
        <text x={pad.l} y={height - 4} fontSize="10">{firstLabel}</text>
        <text x={width - pad.r} y={height - 4} fontSize="10" textAnchor="end">{lastLabel}</text>
      </svg>
      <figcaption className="muted" style={{ fontSize: "0.8em" }}>
        Solid: actual. Dashed: forecast. Band: forecast range. Red dot: miss.
      </figcaption>
    </figure>
  );
}
