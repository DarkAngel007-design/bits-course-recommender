import { useState } from "react";
import { api } from "../api";
import { useApp } from "../App";

export default function DataPage() {
  const { meta, showEvidence } = useApp();
  const c = meta.coverage;
  const s = c.supported_scope;
  const [token, setToken] = useState("");
  const [queue, setQueue] = useState<any>(null);
  const [runs, setRuns] = useState<any>(null);
  const [filter, setFilter] = useState({ kind: "", severity: "", q: "" });
  const [err, setErr] = useState<string | null>(null);

  async function loadAdmin() {
    setErr(null);
    try {
      const qs = new URLSearchParams(Object.entries(filter).filter(([, v]) => v) as [string, string][]).toString();
      setQueue(await api(`/admin/review-queue?limit=100&${qs}`, { admin: token }));
      setRuns(await api("/admin/ingestion-runs", { admin: token }));
    } catch (e: any) { setErr(e.status === 403 ? "Maintainer token required." : e.message); }
  }
  async function publish(id: string) {
    try { await api(`/admin/datasets/${id}/publish`, { method: "POST", admin: token }); window.location.reload(); }
    catch (e: any) { setErr(e.message); }
  }

  const stat = (v: any, l: string) => <div className="kpi"><div className="kpi-v">{v}</div><div className="kpi-l">{l}</div></div>;
  return (
    <div className="page">
      <h1>Data coverage and limitations</h1>
      <div className="kpis">
        {stat(c.offerings_offered, `offerings offered (${c.offerings_total} incl. cancelled)`)}
        {stat(c.offered_courses, "distinct offered courses")}
        {stat(`${c.offered_with_handout}`, "offered courses with a handout")}
        {stat(c.handout_extraction.ok, `handouts with complete evaluation tables (of ${c.handouts_distinct})`)}
        {stat(c.curricula, `curricula (${c.curricula_by_type.single} single, ${c.curricula_by_type.dual} dual)`)}
        {stat(c.rules_reviewed, "rules validated against PDF text")}
      </div>
      <div className="grid2">
        <section className="card">
          <h3>Supported scope</h3>
          <ul className="list">
            <li>Campus: <strong>{s.campus}</strong>; term: <strong>{s.term}</strong></li>
            <li>Curriculum: <strong>{s.curriculum_edition}</strong> (unit framework)</li>
            <li>Resolved admission cohorts: <strong>{s.resolved_admission_years.join(", ")}</strong>; others can attest applicability (provisional)</li>
          </ul>
          <h4>Not supported</h4>
          <ul className="list small">{s.unsupported.map((u: string) => <li key={u}>{u}</li>)}</ul>
          <h4>Known limitations</h4>
          <ul className="list small">{s.limitations.map((u: string) => <li key={u}>{u}</li>)}</ul>
        </section>
        <section className="card">
          <h3>Extraction quality</h3>
          <table className="table mini"><tbody>
            {Object.entries(c.handout_midsem_status).map(([k, v]: any) => <tr key={k}><td>Handout mid-sem: {k.replace(/_/g, " ")}</td><td>{v}</td></tr>)}
            {Object.entries(c.handout_attendance_status).map(([k, v]: any) => <tr key={k}><td>Attendance: {k.replace(/_/g, " ")}</td><td>{v}</td></tr>)}
            {Object.entries(c.prerequisite_status).map(([k, v]: any) => <tr key={k}><td>Prerequisite: {k.replace(/_/g, " ")}</td><td>{v}</td></tr>)}
            {Object.entries(c.review_queue.by_kind).map(([k, v]: any) => <tr key={k}><td>Review queue: {k.replace(/_/g, " ")}</td><td>{v}</td></tr>)}
          </tbody></table>
        </section>
      </div>
      <section className="card">
        <h3>Maintainer: review queue and publication</h3>
        <div className="row">
          <input type="password" placeholder="Maintainer token" value={token} onChange={(e) => setToken(e.target.value)} aria-label="maintainer token" />
          <select value={filter.kind} onChange={(e) => setFilter({ ...filter, kind: e.target.value })} aria-label="kind">
            <option value="">all kinds</option>{Object.keys(c.review_queue.by_kind).map((k) => <option key={k}>{k}</option>)}</select>
          <select value={filter.severity} onChange={(e) => setFilter({ ...filter, severity: e.target.value })} aria-label="severity">
            <option value="">all severities</option><option>error</option><option>warning</option><option>info</option></select>
          <input placeholder="search subject/message" value={filter.q} onChange={(e) => setFilter({ ...filter, q: e.target.value })} />
          <button onClick={loadAdmin} disabled={!token}>Load</button>
        </div>
        {err && <p className="error">{err}</p>}
        {runs && <table className="table"><thead><tr><th>Snapshot</th><th>Parser</th><th>Rules</th><th>Review items</th><th /></tr></thead>
          <tbody>{runs.snapshots.map((r: any) => <tr key={r.snapshot_id}><td><code>{r.snapshot_id}</code>{r.current && <span className="pill tiny">current</span>}</td>
            <td>{r.parser_version}</td><td>{r.rules_version}</td><td>{r.coverage.review_queue.total}</td>
            <td>{!r.current && <button className="secondary" onClick={() => publish(r.snapshot_id)}>Publish</button>}</td></tr>)}</tbody></table>}
        {queue && <>
          <p className="small muted">{queue.total} items</p>
          <table className="table"><thead><tr><th>Severity</th><th>Kind</th><th>Subject</th><th>Message</th><th /></tr></thead>
            <tbody>{queue.items.map((r: any) => <tr key={r.id}><td><span className={`status tiny ${r.severity === "error" ? "bad" : r.severity === "warning" ? "warn" : "neutral"}`}>{r.severity}</span></td>
              <td className="small">{r.kind}</td><td className="small"><code>{r.subject}</code></td><td className="small">{r.message}</td>
              <td>{r.evidence.length > 0 && <button className="link" onClick={() => showEvidence(r.evidence, r.subject)}>source</button>}</td></tr>)}</tbody></table>
        </>}
      </section>
    </div>
  );
}
