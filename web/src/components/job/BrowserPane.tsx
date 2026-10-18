"use client";
// One store's browser agent, drawn as a small browser window. A live view (Browser Use) is
// embedded when the lane has one; else the last screenshot; else the page the agent is on,
// drawn from the job data. The agent cursor sits on the element the agent works on.
import { useEffect, useState } from "react";
import { api, apiUrl, money, type JobView } from "@/api/client";
import { Pointer } from "@/components/Icons";
import { Status } from "@/components/Status";
import type { LaneTile } from "@/hooks/useJobEvents";
import { host, itemShort } from "@/lib/format";
import { reasonWord } from "@/lib/job";

/** sku -> "Nitrile gloves", from every store's offers. */
export function skuNames(job: JobView | null): Record<string, string> {
  const items = Object.fromEntries((job?.request.items ?? []).map((i) => [i.id, itemShort(i.name)]));
  const out: Record<string, string> = {};
  for (const o of job?.offers ?? []) for (const p of o.products) if (p.sku) out[p.sku] = items[p.itemId] ?? p.title;
  return out;
}

const STEPS = ["Search", "Match", "Cart", "Price"];
const ACTIVE = new Set(["queued", "searching", "carting", "subset_pricing", "checkout"]);
const isImage = (u: string) => /\.(png|jpe?g|webp|gif)(\?|$)/i.test(u);

/** How many of the four steps are done, and which one runs now. */
function progress(lane: LaneTile, hasOffers: boolean, priced: boolean): { done: number; current: number | null } {
  switch (lane.state) {
    case "queued":
      return { done: 0, current: null };
    case "searching":
      return hasOffers ? { done: 1, current: 1 } : { done: 0, current: 0 };
    case "carting":
      return { done: 2, current: 2 };
    case "subset_pricing":
      return { done: 3, current: 3 };
    case "cart_ready":
    case "checkout":
    case "at_payment":
      return priced ? { done: 4, current: null } : { done: 3, current: 3 };
    default: // blocked, failed, idle
      return { done: hasOffers ? 1 : 0, current: null };
  }
}

function useEvidenceText(url: string | null) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    if (!url || isImage(url)) return;
    let live = true;
    api.evidenceText(url).then((t) => live && setText(t)).catch(() => live && setText(null));
    return () => {
      live = false;
    };
  }, [url]);
  return url && !isImage(url) ? text : null;
}

/** A fixture "screenshot" is the cart page as text: one line per row, amount last. */
export function TextPage({ text, highlightLast, skuNames = {} }: { text: string; highlightLast?: boolean; skuNames?: Record<string, string> }) {
  // Owner words only: "Bread bags × 8" for "A-BRB-8418 x 8"; drop the store's promo lines.
  const lines = text
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !/^you saved/i.test(l))
    .map((l) => l.replace(/^(\S+) x (\d+)/, (m, sku: string, n: string) => (skuNames[sku] ? `${skuNames[sku]} × ${n}` : m)));
  const [title, ...rows] = lines;
  return (
    <div className="bp-page">
      <p className="bp-title">{title}</p>
      <div className="bp-rows">
        {rows.map((r, i) => {
          const m = r.match(/^(.*?)\s*(-?\$[\d,]+\.\d{2})$/);
          const last = i === rows.length - 1;
          return (
            <div key={i} className={`bp-row${last ? " total" : ""}${last && highlightLast ? " hl" : ""}`}>
              <span>{m ? m[1] : r}</span>
              {m && <span className="tnum">{m[2]}</span>}
              {last && highlightLast && <i className="bp-cursor"><Pointer /></i>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** The watched lane's live browser, large: the stage above the store tiles. */
export function LiveStage({ lane, name, siteUrl }: { lane: LaneTile; name: string; siteUrl: string | undefined }) {
  if (!lane.liveUrl) return null;
  return (
    <section className="stage" aria-label={`${name} live browser, large`}>
      <div className="stage-bar">
        <span className="bp-live-tag"><i />Live</span>
        <b>{name}</b>
        <span className="mono bp-url">{host(lane.pageUrl ?? siteUrl)}</span>
        <a className="link small stage-open" href={lane.liveUrl} target="_blank" rel="noreferrer">Open full screen</a>
      </div>
      <div className="stage-view">
        <iframe className="bp-live" src={lane.liveUrl} title={`${name} live browser`} sandbox="allow-scripts allow-same-origin" />
      </div>
    </section>
  );
}

export function BrowserPane({
  lane,
  name,
  siteUrl,
  job,
  watched = false,
  onWatch,
}: {
  lane: LaneTile;
  name: string;
  siteUrl: string | undefined;
  job: JobView | null;
  /** This lane's live view is on the stage: show a note here, not a second stream. */
  watched?: boolean;
  onWatch?: () => void;
}) {
  const offers = job?.offers.find((o) => o.supplierId === lane.supplierId && o.source === "web");
  const products = offers?.products ?? [];
  const query = job?.request.items[0] ? itemShort(job.request.items[0].spec?.query ?? job.request.items[0].name) : "";
  const shot = lane.lastScreenshotUrl;
  const text = useEvidenceText(shot);
  const failed = lane.state === "failed" || lane.state === "blocked";
  const idle = lane.state === "idle";
  const live = ACTIVE.has(lane.state);
  const total = lane.lastTotal;
  const { done, current } = progress(lane, products.length > 0, lane.subsetTotal != null || (job?.results != null));

  const status = (() => {
    if (failed) return `${reasonWord(lane.reason) || "Stopped"} · skipped`;
    if (idle) return reasonWord(lane.reason) || "Nothing to do here";
    switch (lane.state) {
      case "queued":
        return "Waiting";
      case "searching":
        return products.length ? `Matching ${products.length} products` : `Searching “${query.toLowerCase()}”`;
      case "carting":
        return "Adding items to cart";
      case "cart_ready":
        return "Cart read";
      case "subset_pricing":
        return "Pricing items alone";
      case "checkout":
        return "Filling checkout";
      case "at_payment":
        return "Stopped at payment";
      default:
        return lane.state;
    }
  })();

  let body: React.ReactNode;
  if (lane.liveUrl && watched) {
    body = (
      <div className="bp-wall">
        <span className="small muted">Shown above</span>
      </div>
    );
  } else if (lane.liveUrl) {
    body = <iframe className="bp-live" src={lane.liveUrl} title={`${name} live browser`} sandbox="allow-scripts allow-same-origin" />;
  } else if (failed) {
    body = (
      <div className="bp-wall">
        <Status tone="failed">{reasonWord(lane.reason) || "Blocked"}</Status>
      </div>
    );
  } else if (shot && isImage(shot)) {
    // eslint-disable-next-line @next/next/no-img-element
    body = <img className="bp-shot" src={apiUrl(shot)} alt={`${name}, last screenshot`} />;
  } else if (text) {
    body = <TextPage text={text} skuNames={skuNames(job)} highlightLast={lane.state === "cart_ready" || lane.state === "subset_pricing"} />;
  } else {
    // Search page: the query box, then the products the agent found (one is the match).
    body = (
      <div className="bp-page">
        <div className={`bp-search${products.length === 0 && live ? " hl" : ""}`}>
          {query.toLowerCase() || "search"}
          {products.length === 0 && live && <i className="bp-cursor"><Pointer /></i>}
        </div>
        <div className="bp-rows">
          {products.length > 0
            ? products.slice(0, 4).map((p, i) => (
                <div key={p.sku ?? i} className={`bp-row${i === 0 && live ? " hl" : ""}`}>
                  <span className="bp-ellipsis">{p.title}</span>
                  {p.packPrice != null && <span className="tnum">{money(p.packPrice)}</span>}
                  {i === 0 && live && <i className="bp-cursor"><Pointer /></i>}
                </div>
              ))
            : [0, 1, 2].map((i) => <div key={i} className="bp-skel" />)}
        </div>
      </div>
    );
  }

  return (
    <section className={`bp${failed ? " failed" : ""}${watched ? " watched" : ""}`} aria-label={`${name} browser`}>
      <div className="bp-window">
        <div className="bp-bar">
          <span className="mono bp-url">{host(lane.pageUrl ?? siteUrl)}</span>
          {lane.liveUrl && onWatch && !watched ? (
            <button type="button" className="bp-watch" onClick={onWatch}>Watch</button>
          ) : (
            live && <span className="bp-live-tag"><i />Live</span>
          )}
        </div>
        <div className="bp-body">{body}</div>
      </div>
      <div className="bp-foot">
        <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
          <b>{name}</b>
          {failed ? null : total != null ? (
            <Status status="verified">{money(total)}</Status>
          ) : !siteUrl || lane.mode === "llm_agent" ? (
            <span className="small muted">Unknown site</span>
          ) : null}
        </div>
        <ol className="bp-steps" aria-label="Progress">
          {STEPS.map((s, i) => (
            <li key={s} className={i < done ? "done" : i === current ? "now" : failed && i === done ? "stop" : undefined}>
              <i />
              <span>{s}</span>
            </li>
          ))}
        </ol>
        <p className="small ink-2" aria-live="polite">{status}</p>
      </div>
    </section>
  );
}
