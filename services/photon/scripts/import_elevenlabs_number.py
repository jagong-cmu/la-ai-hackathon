"""Import the Photon voice number into ElevenLabs as a SIP trunk and assign our agent.

Settings follow Photon's ElevenLabs cookbook (photon.codes/docs/beta/cookbooks/voice/elevenlabs):
outbound sip.photon.codes:5061 over TLS, media encryption "allowed", codecs G.722/PCMU/PCMA,
inbound from any address. API: POST /v1/convai/phone-numbers (provider "sip_trunk").

Reads the repo-root .env, never prints secrets, and writes ELEVENLABS_AGENT_PHONE_NUMBER_ID back.
Run from the repo root:  python3 services/photon/scripts/import_elevenlabs_number.py
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENV = Path(__file__).resolve().parents[3] / ".env"
API = "https://api.us.elevenlabs.io/v1/convai/phone-numbers"  # this workspace is on the US host
REQUIRED = ["ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID", "PHOTON_PHONE_NUMBER",
            "PHOTON_SIP_USERNAME", "PHOTON_SIP_PASSWORD"]


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


def call(method: str, url: str, key: str, body: dict | None = None) -> dict | list:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:600]
        sys.exit(f"ElevenLabs returned HTTP {exc.code}: {detail}")


def main() -> None:
    env = read_env()
    missing = [n for n in REQUIRED if not env.get(n)]
    if missing:
        sys.exit("Fill these in .env first: " + ", ".join(missing))
    number = env["PHOTON_PHONE_NUMBER"].replace(" ", "")
    if not re.fullmatch(r"\+\d{8,15}", number):
        sys.exit("PHOTON_PHONE_NUMBER must be in +E.164 format, for example +15551234567")
    masked = number[:-4].replace(number[2:-4], "*" * len(number[2:-4])) + number[-4:]
    key = env["ELEVENLABS_API_KEY"]

    existing = call("GET", API, key)
    for row in existing if isinstance(existing, list) else existing.get("phone_numbers", []):
        if row.get("phone_number", "").replace(" ", "") == number:
            pid = row.get("phone_number_id")
            set_env("ELEVENLABS_AGENT_PHONE_NUMBER_ID", pid)
            print(f"{masked} is already imported (id {pid}). Saved the id to .env. Nothing else changed.")
            return

    body = {
        "provider": "sip_trunk",
        "phone_number": number,
        "label": "Photon voice line",
        "agent_id": env["ELEVENLABS_AGENT_ID"],
        "inbound_trunk_config": {"allowed_addresses": ["0.0.0.0/0"], "media_encryption": "allowed"},
        "outbound_trunk_config": {
            "address": "sip.photon.codes:5061",
            "transport": "tls",
            "media_encryption": "allowed",
            "enabled_codecs": ["G722/8000", "PCMU/8000", "PCMA/8000"],
            "credentials": {"username": env["PHOTON_SIP_USERNAME"], "password": env["PHOTON_SIP_PASSWORD"]},
        },
    }
    result = call("POST", API, key, body)
    pid = result.get("phone_number_id") if isinstance(result, dict) else None
    if not pid:
        sys.exit(f"Unexpected response (no phone_number_id): {str(result)[:300]}")
    set_env("ELEVENLABS_AGENT_PHONE_NUMBER_ID", pid)
    print(f"Imported {masked} as a SIP trunk, assigned agent {env['ELEVENLABS_AGENT_ID']}. "
          f"Saved ELEVENLABS_AGENT_PHONE_NUMBER_ID={pid} to .env.")


if __name__ == "__main__":
    main()
