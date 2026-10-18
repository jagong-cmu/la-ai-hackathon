# Cartwise

Cartwise compares complete purchasing plans across supplier websites. It discovers matching products, normalizes package quantities, enumerates every feasible allocation, checks the distinct supplier carts, and ranks only plans backed by validated checkout totals.

It never places an order or enters payment information.

## What is implemented

- Browser Use Cloud API V4 adapters for product discovery and supplier-batched checkout observation
- Deterministic normalization, allocation, cart deduplication, quote validation, and result ranking
- In-memory background jobs with progress polling
- Fixture and live modes through the same pipeline
- REST API on port 3001 with CORS for `http://localhost:3000`
- Minimal browser test page at `http://localhost:3001/`
- Read-only live previews for currently running Browser Use agents
- Sanitized completed results persisted to `runs/<jobId>.json`

Fixture mode is fully verified. Live supplier compatibility is intentionally not claimed: supplier access, CAPTCHA, login, and checkout behavior must be tested against the final supplier choices.

## Setup

Requirements: Node.js 20 or newer.

```bash
cd cartwise
npm install
cp .env.example .env
npm test
npm run typecheck
npm run fixture
```

`npm run fixture` is always offline, even if `.env` selects live mode.

For local fixture development, set `CARTWISE_MODE=fixture` in `.env`, then run `npm start`. Open `http://localhost:3001/` for the test page or call the API directly.

## Configuration

| Variable | Purpose |
|---|---|
| `PORT` | API port; defaults to `3001` |
| `CARTWISE_MODE` | `fixture` or `live` |
| `BROWSER_USE_API_KEY` | Required only in live mode; never sent to the frontend |
| `MAX_BROWSER_RUN_COST_USD` | Per-run Browser Use cost cap |
| `WEB_ORIGIN` | Allowed frontend CORS origin |
| `APPROVED_SUPPLIER_DOMAINS` | Comma-separated supplier host allowlist |

Keep `.env` private. In live mode, replace the example supplier domains with the selected real suppliers.

## REST API

All money values are integer USD cents. Unknown checkout costs are `null`, never assumed to be zero.

### Start a comparison

`POST /api/comparisons`

Each comparison accepts one or more items and one or more suppliers. Supplier URLs must match `APPROVED_SUPPLIER_DOMAINS`. There is no application-level count limit, but exact checkout verification grows quickly as more products and suppliers are added.

```json
{
  "items": [
    { "id": "markers", "description": "Sharpie black fine-point permanent markers", "quantity": 12, "unit": "marker" },
    { "id": "tape", "description": "Scotch clear shipping tape, 1.88 inches by 38.2 yards", "quantity": 2, "unit": "roll" }
  ],
  "suppliers": [
    { "id": "supplier-a", "name": "Supplier A", "url": "https://supplier-a.example" },
    { "id": "supplier-b", "name": "Supplier B", "url": "https://supplier-b.example" }
  ],
  "address": { "line1": "123 Test Street", "city": "Los Angeles", "state": "CA", "postalCode": "90001", "country": "US" },
  "contact": { "firstName": "Cart", "lastName": "Wise", "email": "cartwise.demo@example.com", "phone": "202-555-0147" }
}
```

Success returns HTTP 202 immediately:

```json
{ "jobId": "0b856e03-a8cf-4dc8-b71c-72ea1bfc3855", "status": "queued" }
```

Invalid input or an unapproved supplier URL returns HTTP 400.

### Read a comparison

`GET /api/comparisons/:id`

Poll about every two seconds until `status` is `complete` or `failed`.

```json
{
  "id": "0b856e03-a8cf-4dc8-b71c-72ea1bfc3855",
  "status": "verifying",
  "mode": "fixture",
  "message": "Checking supplier carts",
  "progress": { "completed": 2, "total": 6 },
  "result": null,
  "error": null
}
```

Unknown IDs return HTTP 404. A completed job's `result` follows the `ComparisonResult` contract in `src/types.ts`. See [`sample-output.json`](./sample-output.json) for a compact fixture example.

### Cancel a comparison

`POST /api/comparisons/:id/cancel`

Cancellation marks the job as `cancelled`, stops active Browser Use runs, and prevents later discovery, optimization, or checkout stages from starting. Browser panels already created for the job remain in its in-memory state with a cancelled status.

### Health

`GET /api/health`

```json
{ "ok": true, "mode": "fixture" }
```

## Result semantics

- `all_feasible_verified`: every feasible plan had validated cart totals.
- `partial_verified`: at least one plan was verified, but the result is not a proven global optimum.
- `none_verified`: no plan had all required verified carts.
- `verified` means a full checkout total was observed and passed backend validation. It does not mean an order was placed or that a price is guaranteed.
- Savings are calculated only against the cheapest verified single-supplier plan.

## Live-mode handoff

The code is ready for live runs, but final supplier qualification remains external work:

1. Set real supplier domains and supplier URLs.
2. Run one supplier end-to-end and inspect product identity, package count, costs, run ID, and checkout evidence.
3. Run both suppliers and confirm all available distinct carts.
4. Treat login, CAPTCHA, inaccessible totals, and payment requirements as incomplete/failed quotes; do not substitute fixture values.

Live mode uses one discovery run and one checkout run per supplier. Each checkout run evaluates that supplier's required cart combinations sequentially, emptying the cart between scenarios. Runs have a cost cap and are limited to two concurrent tasks. Checkout automation is explicitly instructed to stop before payment or final order submission.

Live preview URLs are obtained server-side from Browser Use `browser.ready` events, converted to read-only viewer URLs, and held only in in-memory job state. Completed panels remain visible until the page starts another comparison or the server restarts, although Browser Use may stop the underlying browser after its keep-alive expires. Treat these temporary URLs as credentials and do not log or persist them.

Guest contact details are sent only to checkout agents when a supplier requires them to calculate a destination-specific quote. They are not included in `ComparisonResult` or persisted under `runs/`. Checkout prompts forbid account creation, password entry, marketing enrollment, payment entry, and order submission.
