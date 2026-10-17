"""Write the OpenAPI schema for the frontend's generated TypeScript types.

Run from backend/:  uv run python scripts/export_openapi.py
Then in web/:       npx openapi-typescript ../web/openapi.json -o src/api/types.ts
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import REPO_DIR  # noqa: E402
from app.main import app  # noqa: E402

out = REPO_DIR / "web" / "openapi.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(app.openapi(), indent=2))
print(f"wrote {out}")
