import Link from "next/link";

export function Nav() {
  return (
    <nav className="top" aria-label="Main">
      <Link href="/planner">Planner</Link>
      <Link href="/">Spend insights</Link>
      <Link href="/list">Shopping list</Link>
    </nav>
  );
}
