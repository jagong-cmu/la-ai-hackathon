import type { Metadata } from "next";
import { Archivo, Geist_Mono } from "next/font/google";
import { Agentation } from "agentation";
import "./globals.css";

const archivo = Archivo({ subsets: ["latin"], variable: "--font-archivo" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" });

export const metadata: Metadata = {
  title: { default: "sourn", template: "%s · sourn" },
  description: "Restock planner and supplier price comparison",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${archivo.variable} ${geistMono.variable}`}>
      <body>
        {children}
        {/* Dev-only feedback toolbar; notes sync to the local agentation-mcp server. */}
        {process.env.NODE_ENV === "development" && <Agentation endpoint="http://localhost:4747" />}
      </body>
    </html>
  );
}
