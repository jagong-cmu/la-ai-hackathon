"use client";
// Hidden admin page. Not in the sidebar. The admin token is typed in here and kept in
// sessionStorage only (never hardcoded, never in the bundle). Ids are allowed on this page.
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, errorText, type JobView, type PreflightReport } from "@/api/client";
import { ErrorNote, PageHead } from "@/components/PageHead";
import { Status, type Tone } from "@/components/Status";

const DEPENDENCIES: [string, string][] = [
  ["jev", "Jev"],
  ["llm", "Step 5 (LLM)"],
  ["creao", "CREAO"],
  ["browser", "Browser lanes"],
  ["phone", "Phone"],
];
const TOKEN_KEY = "adminToken";
const LIGHT: Record<string, Tone> = { green: "verified", amber: "quoted", red: "failed" };
const OVERALL: Record<string, string> = { green: "Ready", amber: "Needs attention", red: "Not ready" };

export default function AdminPage() {
  const [token, setToken] = useState("");
  const [runMode, setRunMode] = useState<string | null>(null);
  const [modes, setModes] = useState<Record<string, string> | null>(null);
  const [preflight, setPreflight] = useState<PreflightReport | null>(null);
  const [jobs, setJobs] = useState<JobView[]>([]);
  const [replayJob, setReplayJob] = useState("");
  const [speed, setSpeed] = useState(1);
  const [replayed, setReplayed] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<Record<string, unknown>[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadModes = useCallback(async (t: string) => {
    if (!t) return;
    try {
      setModes(await api.getModes(t));
      setDecisions(await api.decisions(t));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    api.health().then((h) => {
      setRunMode(h.runMode);
      setModes((m) => m ?? h.modes);
    }).catch((e) => setError(errorText(e)));
    api.jobs(20).then((j) => {
      setJobs(j);
      setReplayJob((r) => r || j[0]?.jobId || "");
    }).catch(() => undefined);
    try {
      const t = sessionStorage.getItem(TOKEN_KEY) ?? "";
      setToken(t);
      void loadModes(t);
    } catch {
      /* storage blocked */
    }
  }, [loadModes]);

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

  function saveToken(e: React.FormEvent) {
    e.preventDefault();
    try {
      sessionStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* storage blocked */
    }
    void loadModes(token);
  }

  return (
    <>
      <PageHead title="Admin" meta={runMode ? `Run mode: ${runMode}` : undefined} />
      <ErrorNote error={error} />

      <div className="cols">
        <form className="card pad" onSubmit={saveToken}>
          <h2>Admin token</h2>
          <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
            <label className="sr-only" htmlFor="tok">Admin token</label>
            <input id="tok" className="input" style={{ flex: 1 }} type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
            <button className="btn btn-secondary" type="submit" disabled={!token}>Use token</button>
          </div>
        </form>
        <div className="card pad">
          <h2>Replay</h2>
          <div className="row" style={{ gap: 8 }}>
            <label className="sr-only" htmlFor="rj">Job</label>
            <select id="rj" className="input mono" style={{ flex: "1 1 200px", fontSize: 13 }} value={replayJob} onChange={(e) => setReplayJob(e.target.value)}>
              {jobs.map((j) => <option key={j.jobId} value={j.jobId}>{j.jobId}</option>)}
            </select>
            <div className="seg" role="group" aria-label="Speed">
              {[1, 4].map((x) => (
                <button key={x} type="button" aria-pressed={speed === x} onClick={() => setSpeed(x)}>{x}×</button>
              ))}
            </div>
            <button
              className="btn btn-secondary"
              disabled={busy || !token || !replayJob}
              onClick={() => run(() => api.replay(token, replayJob, speed), (r) => setReplayed(r.jobId))}
            >
              Replay
            </button>
          </div>
          {replayed && <Link className="link small mono" href={`/jobs/${replayed}`}>{replayed}</Link>}
        </div>
      </div>

      <div className="cols" style={{ "--cols": "minmax(0, 1fr) minmax(0, 1.4fr)" } as React.CSSProperties}>
        <div className="tbl-wrap">
          <div className="tbl-caption"><b>Dependencies</b></div>
          <table className="tbl">
            <tbody>
              {DEPENDENCIES.map(([dep, label]) => (
                <tr key={dep}>
                  <td>{label}</td>
                  <td>
                    <div className="seg" role="group" aria-label={`${label} mode`}>
                      {(["live", "cached"] as const).map((m) => (
                        <button
                          key={m}
                          type="button"
                          disabled={busy || !token}
                          aria-pressed={modes?.[dep] === m}
                          onClick={() => run(() => api.setMode(token, { dependency: dep, mode: m }), setModes)}
                        >
                          {m === "live" ? "Live" : "Cached"}
                        </button>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="tbl-wrap">
          <div className="tbl-caption">
            <b>Preflight</b>
            {preflight && <Status tone={LIGHT[preflight.overall]}>Overall: {OVERALL[preflight.overall]?.toLowerCase()}</Status>}
          </div>
          <table className="tbl">
            <tbody>
              {(preflight?.checks ?? []).map((c) => (
                <tr key={c.name}>
                  <td style={{ width: 180 }}>{c.name}</td>
                  <td className="l"><Status tone={LIGHT[c.status] ?? null}>{c.detail}</Status></td>
                </tr>
              ))}
              <tr>
                <td colSpan={2} className="l">
                  <div className="row" style={{ gap: 8 }}>
                    <button className="btn btn-secondary" disabled={busy} onClick={() => run(() => api.preflight(false), setPreflight)}>Run check</button>
                    <button className="btn btn-secondary" disabled={busy} onClick={() => run(() => api.preflight(true), (p) => { setPreflight(p); setModes(p.modes); })}>
                      Run and switch failures to cached
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {decisions && decisions.length > 0 && (
        <div className="tbl-wrap">
          <div className="tbl-caption"><b>Decisions</b></div>
          <table className="tbl wide">
            <thead>
              <tr><th>Time</th><th className="l">Judgment</th><th className="l">Job</th><th className="l">Answer</th><th>Band</th></tr>
            </thead>
            <tbody>
              {decisions.slice(-50).reverse().map((d) => (
                <tr key={String(d.decision_id)}>
                  <td className="mono muted">{new Date(Number(d.created_at) * 1000).toLocaleTimeString()}</td>
                  <td className="l">{String(d.j_id)}</td>
                  <td className="l mono muted">{String(d.job_id ?? "")}</td>
                  <td className="l" style={{ maxWidth: 420, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{String(d.final ?? d.jev_answer ?? "")}</td>
                  <td>{String(d.band)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
