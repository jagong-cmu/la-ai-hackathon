"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { BUSINESS } from "@/lib/format";

const ITEMS = [
  { href: "/planner", label: "Planner", match: ["/planner"] },
  { href: "/orders", label: "Orders", match: ["/orders", "/jobs"] },
  { href: "/calls", label: "Calls", match: ["/calls"] },
  { href: "/spend", label: "Spend", match: ["/spend"] },
  { href: "/suppliers", label: "Suppliers", match: ["/suppliers"] },
  { href: "/rules", label: "Rules", match: ["/rules"] },
];

export function Sidebar() {
  const path = usePathname();
  return (
    <aside className="side">
      <Link href="/" className="mark">SOURN</Link>
      <nav aria-label="Main">
        {ITEMS.map((it) => {
          const on = it.match.some((m) => path === m || path.startsWith(m + "/"));
          return (
            <Link key={it.href} href={it.href} aria-current={on ? "page" : undefined}>
              {it.label}
            </Link>
          );
        })}
      </nav>
      <div className="acct">
        <span className="av" aria-hidden="true">{BUSINESS.name[0]}</span>
        <div>
          <b>{BUSINESS.name}</b>
          <span>{BUSINESS.place}</span>
        </div>
      </div>
    </aside>
  );
}
