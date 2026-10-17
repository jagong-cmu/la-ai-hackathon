"use client";
// Hidden admin drawer. Not linked from the nav. The admin token is typed in here and kept
// in sessionStorage only (never hardcoded, never in the bundle).
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, errorText, type PreflightReport } from "@/api/client";

const DEPENDENCIES = ["jev", "llm", "creao", "browser", "phone"];
const MODES = ["live", "cached"] as const;
const TOKEN_KEY = "adminToken";

export default function AdminPage() {
  const [token, setToken] = useState("");
  const [preflight, setPreflight] = useState<PreflightReport | null>(null);
  const [modes, setModes] = useState<Record<string, string> | null>(null);
  const [dep, setDep] = useState(DEPENDENCIES[0]);
  const [mode, setMode] = useState<(typeof MODES)[number]>("cached");
  const [replayJob, setReplayJob] = useState("");
  const [speed, setSpeed] = useState(1);
  const [replayed, setReplayed] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    try {
      setToken(sessionStorage.getItem(TOKEN_KEY) ?? "");
    } catch {
      /* storage blocked */
    }
  }, []);

  function saveToken(v: string) {
    setToken(v);
    try {
      sessionStorage.setItem(TOKEN_KEY, v);
    } catch {
      /* storage blocked */
    }
  }

  async function run<T>(fn: () => Promise<T>, set: (v: T) => void) {
    setBusy(true);
    setError(null);
    try {
      set(await fn());
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Admin</h1>
      <p>
        <label>
          Admin token{" "}
          <input type="password" autoComplete="off" value={token} onChange={(e) => saveToken(e.target.value)} />
        </label>
      </p>
      {error && <p className="error" role="alert">{error}</p>}

      <h2>Preflight</h2>
      <div className="row">
        <button disabled={busy} onClick={() => run(() => api.preflight(false), setPreflight)}>Run preflight (check only)</button>
        <button disabled={busy} onClick={() => run(() => api.preflight(true), setPreflight)}>Run preflight and switch failed deps to cached</button>
      </div>
      {preflight && (
        <>
          <p>Overall: <strong>{preflight.overall}</strong></p>
          <table>
            <thead><tr><th>Check</th><th>Status</th><th>Detail</th></tr></thead>
            <tbody>
              {preflight.checks.map((c) => (
                <tr key={c.name}>
                  <td>{c.name}</td>
                  <td className={c.status === "red" ? "error" : undefined}>{c.status}</td>
                  <td>{c.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted">Modes: {JSON.stringify(preflight.modes)}</p>
          {(preflight.switchedToCached?.length ?? 0) > 0 && <p>Switched to cached: {preflight.switchedToCached!.join(", ")}</p>}
        </>
      )}

      <h2>Modes</h2>
      <div className="row">
        <button disabled={busy || !token} onClick={() => run(() => api.getModes(token), setModes)}>Load modes</button>
        <label>
          Dependency{" "}
          <select value={dep} onChange={(e) => setDep(e.target.value)}>
            {DEPENDENCIES.map((d) => <option key={d}>{d}</option>)}
          </select>
        </label>
        <label>
          Mode{" "}
          <select value={mode} onChange={(e) => setMode(e.target.value as (typeof MODES)[number])}>
            {MODES.map((m) => <option key={m}>{m}</option>)}
          </select>
        </label>
        <button disabled={busy || !token} onClick={() => run(() => api.setMode(token, { dependency: dep, mode }), setModes)}>
          Set mode
        </button>
      </div>
      {modes && <pre>{JSON.stringify(modes, null, 2)}</pre>}

      <h2>Replay</h2>
      <div className="row">
        <label>
          Source job id <input value={replayJob} onChange={(e) => setReplayJob(e.target.value)} />
        </label>
        <label>
          Speed <input type="number" min={0.1} step={0.1} value={speed} onChange={(e) => setSpeed(Number(e.target.value) || 1)} />
        </label>
        <button
          disabled={busy || !token || !replayJob}
          onClick={() => run(() => api.replay(token, replayJob.trim(), speed), (r) => setReplayed(r.jobId))}
        >
          Replay
        </button>
      </div>
      {replayed && <p>Replay job: <Link href={`/jobs/${replayed}`}>{replayed}</Link></p>}
    </>
  );
}
