import { useEffect, useState } from "react";
import { api } from "../api";
import { useApp } from "../App";

const STATUS: Record<string, [string, string]> = {
  met: ["Met", "ok"], projected_met: ["Met if in-progress courses clear", "info"], in_progress: ["In progress", "warn"],
  not_started: ["Not started", "neutral"], unresolved: ["Unresolved", "bad"], met_by_rule: ["Met by rule", "ok"],
};

export function ScopeBanner({ scope }: { scope: any }) {
  const { showEvidence } = useApp();
  const cls = { resolved: "ok", attested: "warn", unresolved: "warn", unsupported: "bad" }[scope.status as string] ?? "info";
  const label = { resolved: "Curriculum resolved", attested: "Provisional (attested)", unresolved: "Curriculum applicability unresolved",
    unsupported: "Outside supported scope" }[scope.status as string];
  return (
    <div className={`banner ${cls}`}>
      <strong>{label}.</strong> {scope.reasons.join(" ")}
      {scope.evidence?.length > 0 && <button className="link" onClick={() => showEvidence(scope.evidence, "Curriculum source")}>sources</button>}
    </div>
  );
}

export default function ProgressPage() {
  const { session, profile, showEvidence, go } = useApp();
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    api(`/profiles/${session!.profileId}/requirements`, { session }).then(setData).catch((e) => setErr(e.message));
  }, [session, profile?.version]);

  if (err) return <div className="page"><p className="error">{err}</p></div>;
  if (!data) return <div className="page"><div className="spinner" /> Calculating requirements…</div>;
  const req = data.requirements;
  return (
    <div className="page">
      <div className="page-head">
        <h1>Academic progress</h1>
        <span className="muted small">profile v{data.profile_version} · dataset {data.dataset_version} · rules {data.rules_version}</span>
      </div>
      <ScopeBanner scope={data.scope} />
      {!req.available ? <p>Requirements cannot be calculated for this profile.</p> : <>
        <div className="kpis">
          <div className="kpi"><div className="kpi-v">{req.totals.earned_units}</div><div className="kpi-l">units earned</div></div>
          <div className="kpi"><div className="kpi-v">{req.totals.earned_courses}</div><div className="kpi-l">courses cleared</div></div>
          <div className="kpi"><div className="kpi-v">{req.totals.in_progress_courses}</div><div className="kpi-l">in progress ({req.totals.in_progress_units} U projected)</div></div>
          <div className="kpi"><div className="kpi-v small-v">{req.curriculum_name}</div><div className="kpi-l">{req.curriculum_type === "dual" ? "dual degree" : "single degree"} · unit framework</div></div>
        </div>

        <section className="card">
          <h3>Graduation requirements by category</h3>
          <table className="table req">
            <thead><tr><th>Category</th><th>Required</th><th>Earned</th><th>Projected</th><th>Remaining</th><th>Status</th><th /></tr></thead>
            <tbody>
              {req.categories.map((c: any) => {
                const [label, cls] = STATUS[c.status] ?? [c.status, "neutral"];
                const pct = c.required.units ? Math.min(100, (100 * c.earned.units) / c.required.units) : c.status === "met_by_rule" ? 100 : 0;
                return (
                  <tr key={c.key}>
                    <td><strong>{c.label}</strong>
                      <div className="bar" aria-hidden><span style={{ width: `${pct}%` }} /></div>
                      {c.provenance === "derived" && <div className="small muted" title={JSON.stringify(c.derivation)}>derived: {c.derivation?.units ?? "from course list"}</div>}
                      {c.note && <div className="small muted">{c.note}</div>}
                    </td>
                    <td>{fmt(c.required)}</td>
                    <td>{c.earned.courses} · {c.earned.units} U</td>
                    <td>{c.projected.list.length ? `+${c.projected.courses} · ${c.projected.units} U` : "—"}</td>
                    <td>{fmt(c.remaining)}</td>
                    <td><span className={`status ${cls}`}>{label}</span></td>
                    <td><button className="link" onClick={() => showEvidence(c.evidence, c.label)}>source</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <details>
            <summary>How courses were allocated</summary>
            {req.categories.map((c: any) => c.earned.list.length > 0 && (
              <p key={c.key} className="small"><strong>{c.label}:</strong> {c.earned.list.map((x: any) =>
                `${x.code}${x.via_equivalence ? " (via listed equivalence)" : ""}`).join(", ")}</p>
            ))}
            {req.uncounted?.length > 0 && <p className="small">Not counted (project-course limits): {req.uncounted.map((x: any) => x.code).join(", ")}</p>}
            <p className="small muted">Rules applied: {req.rules_applied.join(", ")}. Named courses first; electives by exhaustive
              search that minimises the remaining DEL/HUEL deficit; overflow counts as open electives.</p>
          </details>
        </section>

        <div className="grid2">
          <section className="card">
            <h3>This semester's named courses ({req.semester.year}, sem {req.semester.sem})</h3>
            {req.semester.obligations.length === 0 ? <p className="muted">No uncleared named courses placed in this semester.</p> :
              <ul className="list">{req.semester.obligations.map((o: any) => <li key={o.codes.join()}><code>{o.codes.join(" / ")}</code> {o.title} <span className="pill tiny">{o.category}</span>{o.in_progress && <span className="pill tiny info">in progress</span>}</li>)}</ul>}
            {req.semester.backlog.length > 0 && <>
              <h4>Backlog from earlier semesters</h4>
              <ul className="list">{req.semester.backlog.map((o: any) => <li key={o.codes.join()}><code>{o.codes.join(" / ")}</code> {o.title} <span className="muted small">(year {o.year} sem {o.sem})</span></li>)}</ul>
            </>}
            <button className="secondary" onClick={() => go("recommend")}>Find courses →</button>
          </section>
          <section className="card">
            <h3>Remaining named courses</h3>
            {req.categories.filter((c: any) => c.named_remaining?.length).map((c: any) => (
              <div key={c.key}><h4>{c.label}</h4>
                <ul className="list compact">{c.named_remaining.map((n: any) => <li key={n.codes.join()}><code>{n.codes.join(" or ")}</code> {n.title}
                  {n.placement && <span className="muted small"> · Y{n.placement.year} S{n.placement.sem}</span>}
                  {n.in_progress && <span className="pill tiny info">in progress</span>}</li>)}</ul></div>
            ))}
          </section>
        </div>

        {req.minor && <section className="card">
          <h3>Minor in {req.minor.name}</h3>
          <p>{req.minor.earned.courses}/{req.minor.required.courses} courses · {req.minor.earned.units}/{req.minor.required.units} units ·
            core remaining: {req.minor.core?.remaining?.join(", ") || "none"}</p>
          {req.minor.notes?.map((n: string) => <p key={n} className="small warn-text">{n}</p>)}
          {req.minor.exclusions?.length > 0 && <p className="small warn-text">Exclusion clause: {req.minor.exclusions[0]}</p>}
          <p className="small muted">{req.minor.approval_needed}</p>
        </section>}

        {(req.history_issues.length > 0 || Object.values(data.attempts).some((a: any) => a.notes.length)) && (
          <section className="card">
            <h3>History that needs attention</h3>
            <ul className="list">
              {req.history_issues.map((i: any) => <li key={i.course + i.code} className="warn-text">{i.message}</li>)}
              {Object.entries(data.attempts).filter(([, a]: any) => a.notes.length).map(([c, a]: any) => <li key={c}><code>{c}</code>: {a.notes.join(" ")}</li>)}
            </ul>
          </section>
        )}
      </>}
    </div>
  );
}

function fmt(x: any) {
  if (x.courses == null && x.units == null) return <span className="muted">unresolved</span>;
  return `${x.courses ?? "?"} · ${x.units ?? "?"} U`;
}
