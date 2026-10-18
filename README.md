# Supplier Agent (LA Hacks 2026)

The product finds the cheapest way for a small business to restock its supplies. It reads the business's past bills, searches real supplier sites in parallel, texts and calls the current distributor, and brings the owner to a ready checkout. Code makes sure that no agent can submit a payment.

The product name is not final. The team picks it at H0.

## When the work was done

The planning documents listed below, together with the CREAO integration notes in `agents/creao/README.md`, were drafted on October 16, 2026, the day before LA Hacks began. All of the code in this repository was written during the event.

## Documents

| File | What it holds |
|---|---|
| `.plans/supplier-agent-alignment-spec.md` | The decisions, the pipeline and the reliability rules (v2). Read this first. |
| `docs/DATA_FLOW.md` | The data contracts, the API and the events. |
| `.plans/architecture-review.md` | Why the design changed from v1. |
| `docs/system-map.html` | Diagrams. Published at https://app.superset.sh/page/supplier-agent-system-map-i726ti |
| `docs/PROJECT_BRIEF.md` | Background: sponsor tracks, dataset, team. |

## Repository layout

| Path | Owner | State |
|---|---|---|
| `backend/` | Backend | Built. Runs end to end in fixture mode with no keys. 30 tests. |
| `backend/app/lanes/` | Agents | The interface and a fixture lane exist. Add the live lanes here. See `backend/app/lanes/README.md`. |
| `backend/app/distributor/` | Agents | The interface and a fixture channel exist. Add the live Photon channel here. |
| `services/photon/` | Agents | Not started. See its README. |
| `web/` | Frontend | Not started. See its README. |

## Quick start (backend, fixture mode)

1. Install `uv` (`brew install uv`).
2. Run the tests:
   ```bash
   cd backend && uv sync && uv run pytest -q
   ```
3. Start the server:
   ```bash
   uv run uvicorn app.main:app --reload --port 8000
   ```
4. Open http://localhost:8000/docs to see and try every endpoint.

To try a full run from the terminal:
```bash
D=$(curl -s localhost:8000/shopping-list/draft)
curl -s -X POST localhost:8000/jobs -H 'Content-Type: application/json' -H 'Idempotency-Key: try-0001' -d "$D"
curl -N localhost:8000/jobs/<jobId>/events
```

Copy `.env.example` to `.env` to add keys. Never commit `.env`.
