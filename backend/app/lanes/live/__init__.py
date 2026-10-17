"""Live browser lanes (agents team). Needs the optional `browser` extra:

    uv sync --extra browser && uv run playwright install chromium

- session.py       one browser per lane (local Chromium or a Browser Use cloud browser)
- guards.py        guard verdict -> control flow
- tools.py         Browser Use custom tools: page_state (J6), rank_results (J4), judge_listing (J5),
                   read_cart_totals (J7)
- agent_lane.py    BrowserUseAgentLane for unknown sites (Step 5 Preview through OpenRouter)
- adapter_base.py  PlaywrightAdapterLane for known sites; example_site.py is the reference
- h3_gate.py       runs the agent lane on the local test site (the H3 gate check)

Registration happens in app/lanes/live_setup.py, guarded so that the server starts without it.
"""
