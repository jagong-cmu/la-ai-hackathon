import "dotenv/config";
import { z } from "zod";

const EnvironmentSchema = z.object({
  PORT: z.coerce.number().int().positive().default(3001),
  BROWSER_USE_API_KEY: z.string().optional(),
  CARTWISE_MODE: z.enum(["fixture", "live"]).default("fixture"),
  MAX_BROWSER_RUN_COST_USD: z.coerce.number().positive().default(2),
  WEB_ORIGIN: z.url().default("http://localhost:3000"),
  APPROVED_SUPPLIER_DOMAINS: z
    .string()
    .default(
      "staples.com,officedepot.com,walmart.com,target.com,quill.com,zoro.com,homedepot.com,grainger.com,supplier-a.example,supplier-b.example",
    ),
});

const environment = EnvironmentSchema.parse(process.env);

if (environment.CARTWISE_MODE === "live" && !environment.BROWSER_USE_API_KEY) {
  throw new Error("BROWSER_USE_API_KEY is required when CARTWISE_MODE=live");
}

export const config = {
  port: environment.PORT,
  browserUseApiKey: environment.BROWSER_USE_API_KEY ?? null,
  mode: environment.CARTWISE_MODE,
  maxBrowserRunCostUsd: environment.MAX_BROWSER_RUN_COST_USD,
  webOrigin: environment.WEB_ORIGIN,
  approvedSupplierDomains: environment.APPROVED_SUPPLIER_DOMAINS.split(",")
    .map((domain) => domain.trim().toLowerCase())
    .filter(Boolean),
} as const;
