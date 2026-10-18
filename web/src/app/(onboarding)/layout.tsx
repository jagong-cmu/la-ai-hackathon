import Link from "next/link";
import "./onboarding.css";

// First-run flow: no sidebar, one task per screen. The dashboard lives under (app).
export default function OnboardingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="ob">
      <header className="ob-top">
        <Link href="/" className="ob-mark">SOURN</Link>
        <Link href="/planner" className="ob-skip">Skip to planner</Link>
      </header>
      {children}
    </div>
  );
}
