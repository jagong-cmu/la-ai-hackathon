import Link from "next/link";

export function PageHead({
  title,
  crumb,
  right,
}: {
  title: React.ReactNode;
  crumb?: { href: string; label: string; current?: string };
  right?: React.ReactNode;
}) {
  return (
    <header className="head">
      <div>
        {crumb && (
          <p className="crumb">
            <Link href={crumb.href}>{crumb.label}</Link>
            {crumb.current && <> / <span className="ink-2">{crumb.current}</span></>}
          </p>
        )}
        <h1>{title}</h1>
      </div>
      {right}
    </header>
  );
}

export function Loading({ label }: { label: string }) {
  return (
    <div className="stack" aria-busy="true">
      <p className="sr-only" role="status">{label}</p>
      <div className="skeleton" style={{ height: 38, width: 220, minHeight: 0 }} />
      <div className="skeleton" style={{ minHeight: 240 }} />
    </div>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return <p className="alert" role="alert">{error}</p>;
}
