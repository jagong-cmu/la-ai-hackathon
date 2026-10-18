# Supplier lanes (agents team)

A lane is one supplier and one browser session for the whole job. The orchestrator calls your lane in this order, and it enforces the time budgets:

```
open → search → set_cart(full) → set_cart(subset)* → [owner approves] → set_cart(plan) → checkout → close
```

The interface is `SupplierLane` in `base.py`. `fixture.py` is a complete working example. Copy its structure.

## What to build

1. **Adapters for the 3 demo sites:** Playwright steps that call the shared Jev judgments. They must be fast (search ≤ 45 s, cart ≤ 45 s).
2. **One LLM-agent lane for unknown sites:**
   - Use the open-source `browser_use.Agent` with `Browser(use_cloud=True)` in the demo and a local browser in development.
   - Model: `ChatOpenAI(base_url=OpenRouter, model="stepfun/step-5-preview")`. This is the H3 gate.
   - Register custom tools (`@tools.action`) that call `app.judgments` and `ctx.guard`.

Register the lanes in `registry.py` (`register_adapter`, `register_llm_agent`), and import your module from `live_setup.py`.

CREAO results have no adapter field, so discovery maps each supplier URL to an adapter by domain (`ADAPTER_DOMAINS` in `app/discovery.py`: `webstaurant`, `katom`, `uline`, `restaurantware`, `wasserstrom`). Register your adapter under exactly that name and the site moves from the LLM agent lane to your adapter with no other change. For a new site, add its domain to the table.

Discovery gives a lane only to a supplier that a lane can run now (`can_run` in `registry.py`): cached browsers need `fixtures/suppliers/<id>.json`, and live browsers need a registered adapter or the LLM agent. When CREAO does not return an approved site, discovery adds it to a free adapter slot from `fixtures/approved_sites.json`, but only after its adapter is registered. For a new approved site, add it to that file and to `ADAPTER_DOMAINS`.

## Real-site adapters

| Adapter | Site | File | Notes |
|---|---|---|---|
| `webstaurant` | webstaurantstore.com | `live/webstaurant.py` | Needs a visible browser (`BROWSER_HEADLESS=false`) or the cloud browser: add to cart fails headless. Cart shows the subtotal and the ZIP freight, so the cart is `estimated`. The checkout contact step refuses to run until `CHECKOUT_CONTACT__*` is set. |
| `wees_express` | wees-express.com | `live/shopify.py` | Generic Shopify adapter: reads `/products/<handle>.js` and `/cart.js`, so it works with any theme. One-page checkout: the guard stops on the card fields, which is the handoff. |

To add a site:

1. Run the site preflight: `PYTHONPATH=. uv run python scripts/site_preflight.py <domain>`. It passes when a guest search shows prices with no bot or login wall.
2. A standard Shopify store needs only a `ShopifyStore(...)` entry in `live/shopify.py`. Check that `/products/<handle>.js` and `/cart.js` answer (EcoQuality blocks them, so it needs its own adapter).
3. Register the adapter in `live_setup.py`, add the domain to `ADAPTER_DOMAINS` in `app/discovery.py`, and add the site to `fixtures/approved_sites.json`.
4. Run it on the live site: `uv run python -m app.lanes.live.adapter_gate <name> [--headed] [--checkout]`.
5. Save trimmed pages under `tests/sites/<site>/` and add offline tests (see `tests/test_site_adapters.py`).

Many real carts show no total before checkout. Such a cart is `estimated`, never `verified` (see `stages/totals.py`).

## Rules

- **Call `await ctx.guard(snapshot)` after every navigation, and before every click or type.** If `verdict.stop` is true, stop at once. If `verdict.blocked` is set, raise `LaneBlocked(verdict.blocked)`.
- **Send page text, not screenshots, to Jev.** Fill `PageSnapshot(url, visible_text, inputs)` from the DOM, including each input's `name`, `id`, `autocomplete` and label.
- **Use the shared judgments:**
  - `j4_rerank`: choose which results to open.
  - `j5_listing`: match, must-haves and pack count. It finds the regex candidates for you.
  - `j7_totals` + `stages.totals.build_verified_cart`: read the cart.
- **Do not compute prices or units in a prompt.** Code computes them.
- **Never type card data.** The lane has none, and the guard stops before the payment step.
- **Set `ctx.session_id` and `ctx.live_url` in `open()`.** The UI shows the live view in the lane tile and on screen 4.
- **Write evidence with `ctx.evidence(name)`** (screenshots, DOM extracts), and emit `ctx.emit("screenshot", {"url": rel})`.
- **Keep the session alive after the cart step.** The same session does the checkout after approval.

## Test a lane without the rest of the team

Run the backend with `RUN_MODE=live`, your lane registered and the other dependencies in `cached` mode (`POST /admin/mode`). Fixture lanes, the stub Jev and the fixture distributor cover everything else.
