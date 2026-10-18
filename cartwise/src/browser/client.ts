import { BrowserUse, BrowserUseError } from "browser-use-sdk/v4";
import { z } from "zod";
import { config } from "../config.js";

type RunTaskOptions = {
  maxCostUsd?: number;
  retryTransient?: boolean;
  timeoutMs?: number;
  observer?: BrowserTaskObserver;
};

export type BrowserTaskObserver = {
  label: string;
  isCancelled?: () => boolean;
  onStarted?: (runId: string) => void;
  onBrowserReady?: (view: { runId: string; label: string; liveUrl: string }) => void;
  onFinished?: (runId: string, status: "completed" | "failed" | "cancelled") => void;
};

export type BrowserTaskResult<T> = {
  data: T;
  runId: string;
  workspaceId: string | null;
};

export class BrowserRunError extends Error {
  constructor(
    message: string,
    readonly transient: boolean,
    readonly runId: string | null = null,
  ) {
    super(message);
    this.name = "BrowserRunError";
  }
}

function createClient(): BrowserUse {
  if (!config.browserUseApiKey) {
    throw new Error("BROWSER_USE_API_KEY is required for Browser Use tasks");
  }

  return new BrowserUse({
    apiKey: config.browserUseApiKey,
    maxRetries: 1,
    timeout: 60_000,
  });
}

function isTransient(error: unknown): boolean {
  if (error instanceof BrowserRunError) {
    return error.transient;
  }

  if (error instanceof BrowserUseError) {
    return [408, 429, 500, 502, 503, 504].includes(error.statusCode);
  }

  return error instanceof TypeError && /fetch|network|timeout/i.test(error.message);
}

function looksTransient(message: string): boolean {
  return /timeout|timed out|temporar|unavailable|overloaded|provision|connection reset/i.test(message);
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitForCompletionResilient(
  client: BrowserUse,
  runId: string,
  timeoutMs: number,
) {
  const startedAt = Date.now();
  const deadline = Date.now() + timeoutMs;
  let transientFailures = 0;

  while (Date.now() < deadline) {
    try {
      const current = await client.runs.status(runId);
      transientFailures = 0;
      if (["completed", "failed", "cancelled"].includes(current.status)) {
        return await client.runs.get(runId);
      }
      await wait(5_000);
    } catch (error) {
      const initialNotFound =
        error instanceof BrowserUseError &&
        error.statusCode === 404 &&
        Date.now() - startedAt < 5_000;
      if (!isTransient(error) && !initialNotFound) throw error;
      transientFailures += 1;
      const message = error instanceof Error ? error.message : String(error);
      const delayMs = Math.min(15_000, 2_500 * 2 ** (transientFailures - 1));
      console.warn(
        `[browser-use] transient status error for run ${runId}; reconnecting in ${delayMs}ms: ${message}`,
      );
      await wait(delayMs);
    }
  }

  await client.runs.cancel(runId).catch(() => undefined);
  throw new Error(`timed out after ${Math.round(timeoutMs / 1000)} seconds`);
}

async function runOnce<T>(
  task: string,
  schema: z.ZodType<T>,
  options: RunTaskOptions,
): Promise<BrowserTaskResult<T>> {
  const client = createClient();
  const run = await client.runs.create({
    task,
    outputSchema: z.toJSONSchema(schema),
    maxCostUsd: options.maxCostUsd ?? config.maxBrowserRunCostUsd,
    agentmail: false,
    keepAlive: 120,
    browserSettings: {
      proxyCountryCode: "us",
      record: false,
    },
  });

  options.observer?.onStarted?.(run.id);

  console.log(`[browser-use] run ${run.id} started with status ${run.status}`);

  if (options.observer?.onBrowserReady) {
    void client.runs
      .waitForEvent(run.id, "browser.ready", { timeout: 120_000, interval: 5_000 })
      .then((event) => {
        const data = event.data as Record<string, unknown>;
        if (typeof data.live_view_url !== "string") return;
        const liveUrl = new URL(data.live_view_url);
        if (
          liveUrl.protocol !== "https:" ||
          (liveUrl.hostname !== "live.browser-use.com" &&
            !liveUrl.hostname.endsWith(".browser-use.com"))
        ) {
          console.warn(`[browser-use] ignored unexpected live-view URL for run ${run.id}`);
          return;
        }
        liveUrl.searchParams.set("readOnly", "true");
        liveUrl.searchParams.set("ui", "false");
        options.observer?.onBrowserReady?.({
          runId: run.id,
          label: options.observer.label,
          liveUrl: liveUrl.toString(),
        });
      })
      .catch((error: unknown) => {
        const message = error instanceof Error ? error.message : String(error);
        console.warn(`[browser-use] live preview unavailable for run ${run.id}: ${message}`);
      });
  }

  let result;
  try {
    result = await waitForCompletionResilient(
      client,
      run.id,
      options.timeoutMs ?? 10 * 60_000,
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    options.observer?.onFinished?.(run.id, /cancel/i.test(message) ? "cancelled" : "failed");
    throw new BrowserRunError(
      `Browser Use run ${run.id} could not be completed: ${message}`,
      isTransient(error) || looksTransient(message),
      run.id,
    );
  }

  console.log(`[browser-use] run ${run.id} finished with status ${result.status}`);

  if (result.status !== "completed") {
    const detail = result.error ?? "unknown error";
    options.observer?.onFinished?.(
      run.id,
      result.status === "cancelled" ? "cancelled" : "failed",
    );
    throw new BrowserRunError(
      `Browser Use run ${run.id} ${result.status}: ${detail}`,
      result.status === "failed" && looksTransient(detail),
      run.id,
    );
  }

  options.observer?.onFinished?.(run.id, "completed");

  return {
    data: schema.parse(result.output),
    runId: run.id,
    workspaceId: result.workspaceId ?? run.workspaceId ?? null,
  };
}

export async function downloadWorkspaceFilesByBasename(
  workspaceId: string,
  basenames: ReadonlySet<string>,
): Promise<Map<string, Uint8Array>> {
  const client = createClient();
  const downloaded = new Map<string, Uint8Array>();
  let cursor: string | null | undefined;

  do {
    const page = await client.workspaces.files(workspaceId, {
      cursor,
      includeUrls: true,
      limit: 100,
    });

    for (const file of page.files) {
      const basename = file.path.split("/").at(-1);
      if (!basename || !basenames.has(basename) || !file.url) continue;

      const response = await fetch(file.url);
      if (!response.ok) {
        console.warn(
          `[browser-use] could not download workspace evidence ${file.path}: ${response.status}`,
        );
        continue;
      }
      downloaded.set(basename, new Uint8Array(await response.arrayBuffer()));
    }

    cursor = page.nextCursor;
    if (!page.hasMore) break;
  } while (cursor);

  return downloaded;
}

export async function cancelBrowserRun(runId: string): Promise<void> {
  const client = createClient();
  const deadline = Date.now() + 30_000;
  let cancellationSent = false;
  let lastTransientError: unknown = null;

  while (Date.now() < deadline) {
    try {
      const status = await client.runs.status(runId);
      if (["completed", "failed", "cancelled"].includes(status.status)) return;

      if (!cancellationSent) {
        const cancelled = await client.runs.cancel(runId);
        cancellationSent = true;
        if (["completed", "failed", "cancelled"].includes(cancelled.status)) return;
      }
    } catch (error) {
      if (!isTransient(error)) throw error;
      lastTransientError = error;
    }
    await wait(1_000);
  }

  const detail =
    lastTransientError instanceof Error ? `: ${lastTransientError.message}` : "";
  throw new Error(`Browser Use run ${runId} did not stop within 30 seconds${detail}`);
}

export async function runBrowserTask<T>(
  task: string,
  schema: z.ZodType<T>,
  options: RunTaskOptions = {},
): Promise<BrowserTaskResult<T>> {
  if (options.observer?.isCancelled?.()) {
    throw new BrowserRunError("Comparison cancelled before browser run started", false);
  }
  try {
    return await runOnce(task, schema, options);
  } catch (error) {
    const runWasCreated = error instanceof BrowserRunError && error.runId !== null;
    if (options.retryTransient !== false && isTransient(error) && !runWasCreated) {
      console.warn("[browser-use] transient run-creation failure; retrying once");
      return runOnce(task, schema, { ...options, retryTransient: false });
    }

    throw error;
  }
}
