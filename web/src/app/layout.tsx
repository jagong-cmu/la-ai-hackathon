import type { Metadata } from "next";
import { Agentation } from "agentation";
import { Nav } from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "Supply agent",
  description: "Restock planner and supplier price comparison (skeleton UI)",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Nav />
        <main>{children}</main>
        {/* Dev-only feedback toolbar; notes sync to the local agentation-mcp server. */}
        {process.env.NODE_ENV === "development" && <Agentation endpoint="http://localhost:4747" />}
      </body>
    </html>
  );
}
