"""Give the ElevenLabs agent a webhook tool that logs each turn to the Photon service, live.

Why: ElevenLabs fills the conversation transcript only after the call, and the real-time
monitor WebSocket is Enterprise-only (403 `monitoring_enterprise_only`). So the agent calls
`log_live_turn` each time the person stops speaking, with its own last line and the person's
reply. The service forwards both lines to the backend as `transcript_line` with `live: true`.

What this script does (safe to run again, for example when the tunnel URL changes):
1. Makes ELEVENLABS_TOOL_SECRET in .env if it is missing, and saves PHOTON_PUBLIC_URL.
2. Creates the `log_live_turn` workspace tool, or updates its URL and secret.
3. Adds the tool to the agent, adds the "# Live log" rules to the end of its prompt once, and sets
   the turn-taking that stops repeated lines (TURN below).

Reads the repo-root .env and never prints secrets. Run from the repo root:
    python3 services/photon/scripts/setup_live_transcript.py https://<photon-tunnel-host>
Then restart the Photon service, so that it reads the new ELEVENLABS_TOOL_SECRET.
"""

from __future__ import annotations

import json
import re
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENV = Path(__file__).resolve().parents[3] / ".env"
DEFAULT_API = "https://api.us.elevenlabs.io"  # this workspace is on the US host
TOOL_NAME = "log_live_turn"
PROMPT_MARKER = "# Live log"
PROMPT_RULES = f"""

{PROMPT_MARKER}
- Each time the person finishes speaking, call the tool `{TOOL_NAME}` one time. When it returns, say exactly the reply that you logged, one time. Do not call the tool again until the person speaks again.
  - `supplier_said`: what the person just said, copied word for word. Do not shorten, correct or summarize it.
  - `agent_reply`: the exact words that you will say now.
- Never mention the tool or the log aloud."""
# Turn-taking that keeps the agent from saying a line twice (real call, Oct 17): a speculative turn
# can start a second reply (and a second tool call) for one supplier turn; a fragment such as "Uh-"
# ended the supplier's turn early; a backchannel such as "Yeah." cut the agent off mid-line.
TURN = {
    "speculative_turn": False,
    "turn_eagerness": "patient",
    "interruption_ignore_terms": ["yeah", "yes", "yep", "okay", "ok", "uh huh", "mhm", "mm-hmm",
                                  "right", "sure", "uh", "um", "got it"],
}


def read_env() -> dict[str, str]:
    values = {}
    for line in ENV.read_text().splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if m:
            values[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return values


def set_env(name: str, value: str) -> None:
    text = ENV.read_text()
    if re.search(rf"^{name}=", text, re.M):
        text = re.sub(rf"^{name}=.*$", f"{name}={value}", text, flags=re.M)
    else:
        text += f"\n{name}={value}\n"
    ENV.write_text(text)


def call(method: str, url: str, key: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:600]
        sys.exit(f"ElevenLabs {method} {url.split('/v1/')[-1]} returned HTTP {exc.code}: {detail}")


def tool_config(public_url: str, secret: str) -> dict:
    return {
        "type": "webhook",
        "name": TOOL_NAME,
        "description": ("Log the exchange for the live transcript screen: what the person just said and what you "
                        "will say now. Call it one time each time the person finishes speaking, then say that "
                        "reply. It returns nothing that you need."),
        # `immediate`: one tool call, one result, then the agent speaks once. In `async` mode the
        # "background task completed" result started a second LLM turn, and the agent said each
        # line twice (real call, Oct 17). The service answers at once, so the wait is short.
        "execution_mode": "immediate",
        "pre_tool_speech": "off",
        "response_timeout_secs": 5,
        "api_schema": {
            "url": f"{public_url}/elevenlabs/live-line",
            "method": "POST",
            "request_headers": {"X-Tool-Secret": secret},
            "request_body_schema": {
                "type": "object",
                "description": "The current exchange of the call.",
                "required": ["supplier_said", "agent_reply"],
                "properties": {
                    "conversation_id": {"type": "string", "dynamic_variable": "system__conversation_id"},
                    "supplier_said": {"type": "string",
                                      "description": "What the person just said, copied word for word."},
                    "agent_reply": {"type": "string",
                                    "description": "The exact words that you will say now."},
                },
            },
        },
    }


def main() -> None:
    env = read_env()
    missing = [n for n in ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID") if not env.get(n)]
    if missing:
        sys.exit("Fill these in .env first: " + ", ".join(missing))
    public_url = (sys.argv[1] if len(sys.argv) > 1 else env.get("PHOTON_PUBLIC_URL", "")).rstrip("/")
    if not public_url.startswith("https://"):
        sys.exit("Give the Photon service's public https URL, for example: "
                 "python3 services/photon/scripts/setup_live_transcript.py https://abc.trycloudflare.com")
    key, agent_id = env["ELEVENLABS_API_KEY"], env["ELEVENLABS_AGENT_ID"]
    api = env.get("ELEVENLABS_BASE_URL", DEFAULT_API).rstrip("/")

    secret = env.get("ELEVENLABS_TOOL_SECRET") or secrets.token_urlsafe(32)
    if not env.get("ELEVENLABS_TOOL_SECRET"):
        set_env("ELEVENLABS_TOOL_SECRET", secret)
        print("Made ELEVENLABS_TOOL_SECRET and saved it to .env.")
    set_env("PHOTON_PUBLIC_URL", public_url)

    config = tool_config(public_url, secret)
    tools = call("GET", f"{api}/v1/convai/tools", key).get("tools", [])
    tool_id = next((t["id"] for t in tools if t.get("tool_config", {}).get("name") == TOOL_NAME), None)
    if tool_id:
        call("PATCH", f"{api}/v1/convai/tools/{tool_id}", key, {"tool_config": config})
        print(f"Updated tool {TOOL_NAME} ({tool_id}): URL {public_url}/elevenlabs/live-line")
    else:
        tool_id = call("POST", f"{api}/v1/convai/tools", key, {"tool_config": config})["id"]
        print(f"Created tool {TOOL_NAME} ({tool_id}): URL {public_url}/elevenlabs/live-line")

    prompt = call("GET", f"{api}/v1/convai/agents/{agent_id}", key)["conversation_config"]["agent"]["prompt"]
    tool_ids = list(prompt.get("tool_ids") or [])
    if tool_id not in tool_ids:
        tool_ids.append(tool_id)
    # The live-log section is always the end of the prompt: replace it, so new rules take effect.
    text = prompt["prompt"]
    if PROMPT_MARKER in text:
        text = text[:text.index(PROMPT_MARKER)]
    text = text.rstrip() + PROMPT_RULES
    # Thinking makes each turn (and so each tool call) slower; this agent needs no reasoning.
    call("PATCH", f"{api}/v1/convai/agents/{agent_id}", key,
         {"conversation_config": {"agent": {"prompt": {"prompt": text, "tool_ids": tool_ids,
                                                       "thinking_budget": 0}},
                                  "turn": TURN}})

    config = call("GET", f"{api}/v1/convai/agents/{agent_id}", key)["conversation_config"]
    after, turn = config["agent"]["prompt"], config.get("turn", {})
    ok = tool_id in (after.get("tool_ids") or []) and after["prompt"].endswith(PROMPT_RULES.strip())
    turn_ok = all(turn.get(k) == v for k, v in TURN.items())
    print(f"Agent {agent_id}: tool attached and live-log rules current: {'yes' if ok else 'NO, check the dashboard'}. "
          f"Turn-taking current: {'yes' if turn_ok else 'NO, check the dashboard'}. "
          f"LLM {after.get('llm')}, thinking budget {after.get('thinking_budget')}.")


if __name__ == "__main__":
    main()
