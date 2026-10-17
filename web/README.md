# Web dashboard (frontend): functional skeleton

Next.js 16 (App Router, TypeScript, pnpm). This is plumbing only: the pages fetch real data and wire every action, but the styling is plain and neutral. The real screens come from the Paper designs (spec §3). Visual identity: bright utility.

## Run against the backend in fixture mode

1. Start the backend. Fixture mode needs no keys, and every screen has data.
   ```bash
   cd backend && FIXTURE_TIME_SCALE=0.2 uv run uvicorn app.main:app --port 8000
   ```
2. Install the web packages: `cd web && pnpm install`.
3. When the backend API changes, generate the API types again:
   ```bash
   cd backend && uv run python scripts/export_openapi.py   # writes web/openapi.json
   cd ../web && pnpm gen:api                               # writes src/api/types.ts
   ```
4. Start the web app: `cd web && pnpm dev` (http://localhost:3000). The backend allows CORS from `http://localhost:3000` only. For another port, start the backend with `CORS_ORIGINS='["http://localhost:<port>"]'`.
5. Check the code: `pnpm typecheck` and `pnpm build`.

Environment (all optional, see `web/.env.example`; put local values in `web/.env.local`):

| Variable | Use |
|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Backend URL. Default `http://localhost:8000`. |
| `NEXT_PUBLIC_SENTRY_DSN` | Turns on Sentry (errors, traces, Session Replay). Without it, Sentry does nothing. |
| `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT` | Optional source map upload at build time. |

## Routes

| Route | Screen |
|---|---|
| `/planner` | Restock planner: supply plans (run-out, order-by, status), misses that wait for the owner (answer buttons), owner message box, adjustments, product charts (SVG, forecast range, misses in red) |
| `/` | 0 Spend insights (with the disclosure) |
| `/list` | 1 Shopping list: edit quantities, then **Compare suppliers** (`POST /jobs`) |
| `/jobs/[id]` | 2 Working: lane tiles, distributor SMS and call transcript, best plan so far, event log |
| `/jobs/[id]/results` | 3 Results: plans with status chips, rules badge, warnings, accept-quoted toggle, Approve / Reject |
| `/jobs/[id]/checkout` | 4 Checkout handoff: handoffs, simulated cards, placeholder **Place order** (does nothing) |
| `/admin` | Hidden admin drawer (not in the nav). Type the admin token in the page. The page keeps it in `sessionStorage` only. |

## Code map

- `src/api/types.ts`: generated from `openapi.json` by `openapi-typescript`. Do not edit it.
- `src/api/client.ts`: typed fetch client (`api.*`), `ApiError` (keeps the FastAPI `detail`), `detailText()`, `apiUrl()` for `/evidence/...` links. `createJob` sends `Idempotency-Key: crypto.randomUUID()`.
- `src/hooks/useJobEvents.ts`: `GET /jobs/{id}`, then `EventSource` on `/jobs/{id}/events`. A reducer keeps lanes, best plan, transcript and events. The browser resends `Last-Event-ID` on reconnect, and the hook drops any event with an old `seq`. It fetches the job again on `results_ready`, `results_updated`, `handoff_ready`, `job_state`, `approved` and `rejected`, and it closes the stream on a final job state. Note: the backend sends named SSE events (`event: <type>`), so the hook adds one listener for each type in `EVENT_TYPES`. Add new event types there.
- `src/components/StatusChip.tsx`: the one place that maps a status to a color.
- `src/components/ForecastChart.tsx`: plain SVG chart, no chart library.
- `src/instrumentation-client.ts`, `src/instrumentation.ts`, `next.config.ts`, `src/app/global-error.tsx`: Sentry wiring.

## For the designer

- Replace `src/app/globals.css` and the markup in each `page.tsx`. Keep the data calls and the `useJobEvents` hook.
- The status colors are CSS variables in `globals.css` (`--status-verified`, `--status-quoted`, `--status-estimated`, `--status-blocked`). Other statuses (lane states, supply status) use a neutral grey, so the four colors keep one meaning.

## Screen → endpoint

| Screen | Data |
|---|---|
| Planner (new, before or beside screen 0) | `GET /planner`: product charts with forecast ranges and marked misses, supply run-out dates, the misses that wait for the owner (`status: needs_owner` → `POST /planner/misses/{id}/answer`), and owner messages (`POST /planner/owner-messages`). Show `disclosure` and label the usage ratios as assumed. |
| 0 Spend insights | `GET /insights`. Start `GET /suppliers` at the same time, because discovery can take minutes when it runs live. |
| 1 Shopping list | `GET /shopping-list/draft` → the user edits it → `POST /jobs` with the header `Idempotency-Key: <uuid>` |
| 2 Working | `GET /jobs/{id}` for the first state, then `EventSource('/jobs/{id}/events')`. The browser's `EventSource` sends `Last-Event-ID` itself when it reconnects. |
| 3 Results | `results` in `GET /jobs/{id}` (on the `results_ready` and `results_updated` events). `POST /jobs/{id}/approve`. |
| 4 Checkout | `handoffs` and `cards` in `GET /jobs/{id}` (on the `handoff_ready` events) |

## Event types

See `docs/DATA_FLOW.md` §8 for the full list. These are the main ones:
- **Lane tiles:** `lane_state`, `screenshot`, `page_state`, `guard_stop`, `supplier_cart_ready`, `subset_total`.
- **Plan updates:** `plans_updated` (best plan so far), `results_ready`, `results_updated`.
- **Distributor panel:** `message_sent`, `reply_received`, `call_started`, `transcript_line`, `call_offer`, `call_ended`, `quote_received`.
- **Approval:** `rules_checked`, `card_minted`, `approved`, `handoff_ready`, `job_state`.

The stream closes itself after a final job state (`at_payment`, `partial_handoff`, `rejected`, `failed`).

## Rules for the UI

- **One status color for each state, on every screen:** verified (green), quoted (amber), estimated (blue-grey), blocked or failed (red).
- **Show a failed lane in red.** Do not hide it. The other lanes continue, and that is the designed behavior.
- **"Approve" sends an error (409) for an estimated plan, and for a quoted plan without `acceptQuoted: true`.** Show `detail.text`.
- **Label the synthetic data** (`insights.disclosure`), the simulated cards (`simulated: true`) and replays (`replayOf`).
- **Admin drawer** (hidden):
  - Preflight results: `GET /health/preflight`.
  - Mode switches: `POST /admin/mode`.
  - Replay: `POST /admin/replay/{jobId}`.
  - These need the header `X-Admin-Token`.
