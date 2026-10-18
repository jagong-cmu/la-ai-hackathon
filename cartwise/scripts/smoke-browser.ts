import { z } from "zod";
import { runBrowserTask } from "../src/browser/client.js";

const SmokeResultSchema = z.object({
  reachedPage: z.boolean(),
  pageTitle: z.string(),
  finalUrl: z.string(),
  error: z.string().nullable(),
});

const result = await runBrowserTask(
  [
    "Visit https://example.com.",
    "Read the page title and final URL.",
    "Do not navigate anywhere else and do not interact with forms.",
    "Return reachedPage=true only if the page visibly loads.",
  ].join("\n"),
  SmokeResultSchema,
  { maxCostUsd: 0.5, retryTransient: true },
);

console.log(JSON.stringify(result, null, 2));
