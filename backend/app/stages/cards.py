"""Simulated agent-scoped single-use cards (Visa track). Never real card numbers.

The card stays in the backend. No card data goes to a browser session or an agent prompt.
"""

from __future__ import annotations

import math
from datetime import timedelta
from urllib.parse import urlparse

from app.contracts import Plan, VirtualCard, utcnow
from app.db import stable_hash

LIMIT_MARGIN = 1.02
LIFETIME = timedelta(minutes=60)


def mint_cards(job_id: str, plan: Plan, supplier_urls: dict[str, str]) -> list[VirtualCard]:
    cards = []
    for cart in plan.carts:
        digest = stable_hash(job_id, plan.plan_id, cart.supplier_id)
        domain = urlparse(supplier_urls.get(cart.supplier_id, "")).hostname or cart.supplier_id
        cards.append(
            VirtualCard(
                card_id="vc_" + digest[:10],
                supplier_id=cart.supplier_id,
                last4=str(int(digest[:8], 16) % 10_000).zfill(4),
                limit_usd=math.ceil(cart.total * LIMIT_MARGIN * 100) / 100,
                merchant_lock=domain,
                expires_at=utcnow() + LIFETIME,
            )
        )
    return cards
