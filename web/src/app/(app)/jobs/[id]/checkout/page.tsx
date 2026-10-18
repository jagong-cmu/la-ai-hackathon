"use client";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, apiUrl, money } from "@/api/client";
import { External } from "@/components/Icons";
import { ErrorNote, Loading, PageHead } from "@/components/PageHead";
import { Status } from "@/components/Status";
import { skuNames, TextPage } from "@/components/job/BrowserPane";
import { useJobEvents } from "@/hooks/useJobEvents";
import { host, int, mmss } from "@/lib/format";
import { headlinePlan, itemNames, jobStateWord, orderTitle, reasonWord, supplierNames, supplierUrls } from "@/lib/job";

const isImage = (u: string) => /\.(png|jpe?g|webp|gif)(\?|$)/i.test(u);

function PaymentPage({ url, names, total }: { url: string | null | undefined; names: Record<string, string>; total?: number }) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    if (!url || isImage(url)) return;
    api.evidenceText(url).then(setText).catch(() => setText(null));
  }, [url]);
  // eslint-disable-next-line @next/next/no-img-element
  if (url && isImage(url)) return <img className="bp-shot" src={apiUrl(url)} alt="Payment step" />;
  // The fixture evidence is the payment form's field labels; draw the form.
  if (text && /card number/i.test(text)) {
    return (
      <div className="bp-page" style={{ gap: 12 }}>
        <p className="bp-title">Payment</p>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 88px", gap: 8 }}>
          <div className="bp-search" style={{ color: "var(--color-faint)" }}>Card number</div>
          <div className="bp-search" style={{ color: "var(--color-faint)" }}>MM / YY</div>
        </div>
        <div className="bp-skel" style={{ height: 8, width: "60%" }} />
        <div className="bp-skel" style={{ height: 8, width: "40%" }} />
        {total != null && <p className="small strong tnum" style={{ textAlign: "right", marginTop: "auto" }}>Order total {money(total)}</p>}
      </div>
    );
  }
  if (text) return <TextPage text={text} skuNames={names} />;
  return (
    <div className="bp-page">
      <div className="bp-skel" />
      <div className="bp-skel" />
    </div>
  );
}

function useNow(ms: number) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

export default function CheckoutPage() {
  const { id } = useParams<{ id: string }>();
  const s = useJobEvents(id);
  const now = useNow(1000);
  const job = s.job;
  if (!job) return s.error ? <ErrorNote error={s.error} /> : <Loading label="Loading checkout" />;

  const names = supplierNames(job);
  const urls = supplierUrls(job);
  const items = itemNames(job);
  const plan = headlinePlan(job);
  const handoffs = job.handoffs ?? [];
  const atPayment = handoffs.filter((h) => h.status === "at_payment").length;
  const title = job.state === "at_payment" ? "Ready to pay" : job.state === "partial_handoff" ? "Partly ready to pay" : jobStateWord(job.state);

  return (
    <>
      <PageHead
        title={title}
        crumb={{ href: "/orders", label: "Orders", current: orderTitle(job) }}
        right={plan && <p className="figure">{money(plan.total)}</p>}
      />
      <ErrorNote error={s.error} />
      {handoffs.length === 0 && (
        <div className="panes" aria-busy="true">
          {(plan?.carts ?? []).map((c) => (
            <div key={c.cartKey} className="bp">
              <div className="bp-window"><div className="bp-bar"><span className="mono bp-url">{host(urls[c.supplierId])}</span></div><div className="bp-body"><div className="bp-page"><div className="bp-skel" /><div className="bp-skel" /></div></div></div>
              <div className="bp-foot"><b>{names[c.supplierId]}</b><p className="small ink-2">Filling checkout</p></div>
            </div>
          ))}
        </div>
      )}
      <div className="panes" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 380px), 1fr))" }}>
        {handoffs.map((h) => {
          const card = (h.cardId ? job.cards.find((c) => c.cardId === h.cardId) : null) ?? job.cards.find((c) => c.supplierId === h.supplierId);
          const cart = plan?.carts.find((c) => c.supplierId === h.supplierId);
          const failed = h.status === "failed";
          const left = card ? Math.round((new Date(card.expiresAt).getTime() - now) / 1000) : 0;
          return (
            <section key={h.supplierId} className={`bp${failed ? " failed" : ""}`} aria-label={`${names[h.supplierId]} checkout`}>
              <div className="bp-window">
                <div className="bp-bar">
                  <span className="mono bp-url">{host(urls[h.supplierId])}/checkout</span>
                  {h.status === "at_payment" && <span className="small muted">Payment step</span>}
                </div>
                <div className="bp-body" style={{ height: 180 }}>
                  {h.liveUrl ? (
                    <iframe className="bp-live" src={h.liveUrl} title={`${names[h.supplierId]} checkout`} sandbox="allow-scripts allow-same-origin" />
                  ) : failed ? (
                    <div className="bp-wall"><Status tone="failed">{reasonWord(h.reason) || "Failed"}</Status></div>
                  ) : (
                    <PaymentPage url={h.screenshotUrl} names={skuNames(job)} total={cart?.total} />
                  )}
                </div>
              </div>
              <div className="bp-foot" style={{ gap: 14 }}>
                <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
                  <div>
                    <b>{names[h.supplierId] ?? h.supplierId}</b>
                    <p className="small muted" style={{ marginTop: 2 }}>
                      {cart?.lines.map((l) => `${int(l.packQuantity)} × ${items[l.itemId] ?? l.itemId}`).join(" · ")}
                      {h.status === "quoted_confirm_by_phone" ? " · confirm by phone" : ""}
                    </p>
                  </div>
                  {cart && <span className="strong tnum" style={{ fontSize: 20, lineHeight: "24px" }}>{money(cart.total)}</span>}
                </div>
                {card && (
                  <div className="panel small" style={{ display: "grid", gap: 4 }}>
                    <div className="row" style={{ justifyContent: "space-between" }}>
                      <span className="ink-2">Single-use card{card.simulated ? " · simulated" : ""}</span>
                      <span className="mono" style={{ fontSize: 13 }}>•••• {card.last4}</span>
                    </div>
                    <span className="muted tnum">
                      Max {money(card.limitUsd)} · {card.merchantLock} only · {left > 0 ? `expires in ${mmss(left)}` : card.status === "used" ? "used" : "expired"}
                    </span>
                  </div>
                )}
                {h.liveUrl && (
                  <a className="btn btn-secondary has-icon" style={{ alignSelf: "flex-start" }} href={h.liveUrl} target="_blank" rel="noreferrer">
                    Open checkout <External />
                  </a>
                )}
              </div>
            </section>
          );
        })}
      </div>
      {atPayment > 0 && <span className="sr-only" role="status">{atPayment} carts at the payment step</span>}
    </>
  );
}
