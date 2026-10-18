import Link from "next/link";
import "./onboarding.css";

// First-run flow: no sidebar, one task per screen. The dashboard lives under (app).
export default function OnboardingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="ob">
      <header className="ob-top">
        <span className="ob-mark">SOURN</span>
        <Link href="/planner" className="ob-skip">Skip to dashboard</Link>
      </header>
      {children}
    </div>
  );
}
