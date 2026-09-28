import { Fragment, useEffect, useState } from "react";
import { api } from "../api";
import { useApp } from "../App";
import CourseSearch from "./CourseSearch";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const HOURS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
const HOUR_LABEL = ["8", "9", "10", "11", "12", "1", "2", "3", "4", "5"];
const CHECK_CLS: Record<string, string> = { pass: "ok", fail: "bad", unknown: "warn" };

export default function PlanPage() {
  const { session, plan, setPlan, showEvidence, profile } = useApp();
  const [prefs, setPrefs] = useState({ no_early_classes: false, free_days: [] as string[], compact: true, hard: true });
  const [val, setVal] = useState<any>(null);
  const [saved, setSaved] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [name, setName] = useState("Semester plan");
  const [labels, setLabels] = useState<Record<string, any>>({});

  useEffect(() => {
    plan.filter((id) => !labels[id]).forEach((id) =>
      api(`/offerings/${encodeURIComponent(id)}`).then((o) => setLabels((l) => ({ ...l, [id]: o }))).catch(() => undefined));
  }, [plan]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadSaved = () => api(`/profiles/${session!.profileId}/plans`, { session }).then((r) => setSaved(r.plans)).catch(() => undefined);
  useEffect(() => { loadSaved(); }, [profile?.version]); // eslint-disable-line react-hooks/exhaustive-deps

  async function validate() {
    setBusy(true); setErr(null);
    try { setVal(await api("/plans/validate", { session, body: { profile_id: session!.profileId, offering_ids: plan, preferences: prefs } })); }
    catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }
  useEffect(() => { if (plan.length) validate(); else setVal(null); }, [plan, prefs, profile?.version]); // eslint-disable-line react-hooks/exhaustive-deps

  async function save() {
    try { await api("/plans", { session, body: { profile_id: session!.profileId, offering_ids: plan, preferences: prefs, name } }); loadSaved(); }
    catch (e: any) { setErr(e.message); }
  }

  const sched = val?.schedule;
  const grid: Record<string, string> = {};
  sched?.grid?.forEach((g: any) => { grid[`${g.day}-${g.hour}`] = g.course; });
  const colors: Record<string, number> = {};
  Object.keys(sched?.assignment ?? {}).forEach((c, i) => { colors[c] = i % 8; });

  return (
    <div className="page">
      <div className="page-head"><h1>Semester plan</h1>
        {val && <span className={`status ${val.status === "valid" ? "ok" : val.status === "invalid" ? "bad" : "warn"}`}>
          {val.status === "valid" ? "Valid plan" : val.status === "invalid" ? "Plan violates a rule" : "Needs verification"}</span>}
      </div>
      {err && <div className="banner bad">{err}</div>}
      <div className="grid2">
        <section className="card">
          <h3>Courses ({plan.length}) {val && <span className="muted small">· {val.total.value} {val.total.system}</span>}</h3>
          <CourseSearch offeredOnly placeholder="Add an offered course…" onPick={async (c) => {
            const course = await api(`/courses/${encodeURIComponent(c.code)}`);
            const off = course.offerings.find((o: any) => o.status === "offered" && o.credit.system === "units") ?? course.offerings[0];
            if (off && !plan.includes(off.id)) setPlan([...plan, off.id]);
          }} />
          {plan.length === 0 && <p className="muted">Add courses from Recommendations or search above.</p>}
          <ul className="list">
            {plan.map((id) => {
              const o = labels[id];
              const v = val?.courses?.find((c: any) => c.offering_id === id);
              const a = o && sched?.assignment?.[o.course_code];
              return <li key={id} className="plan-item">
                <span className={`swatch c${colors[o?.course_code] ?? 9}`} />
                <div><code>{o?.course_code ?? id}</code> {o?.course?.title}
                  <div className="small muted">{o ? `${o.credit.value} ${o.credit.system}` : ""}{a ? ` · sections ${a.sections.join(" + ")}${a.alternatives ? ` (${a.alternatives} alternative bundle${a.alternatives > 1 ? "s" : ""})` : ""}` : ""}
                    {v && <> · <span className={`status tiny ${v.status === "eligible" ? "ok" : v.status === "ineligible" ? "bad" : "warn"}`}>{v.status.replace("_", " ")}</span></>}</div>
                </div>
                <button className="link" onClick={() => setPlan(plan.filter((x) => x !== id))}>remove</button>
              </li>;
            })}
          </ul>
          <h4>Timetable preferences</h4>
          <div className="row small">
            <label className="check"><input type="checkbox" checked={prefs.no_early_classes} onChange={(e) => setPrefs({ ...prefs, no_early_classes: e.target.checked })} /> No 8 AM classes</label>
            <label className="check"><input type="checkbox" checked={prefs.compact} onChange={(e) => setPrefs({ ...prefs, compact: e.target.checked })} /> Compact days</label>
            <label className="check"><input type="checkbox" checked={prefs.hard} onChange={(e) => setPrefs({ ...prefs, hard: e.target.checked })} /> Treat as hard constraints</label>
          </div>
          <div className="row small">Keep free: {DAYS.map((d) => <label key={d} className="check"><input type="checkbox" checked={prefs.free_days.includes(d)}
            onChange={(e) => setPrefs({ ...prefs, free_days: e.target.checked ? [...prefs.free_days, d] : prefs.free_days.filter((x) => x !== d) })} />{d}</label>)}</div>
        </section>
        <section className="card">
          <h3>Checks {busy && <span className="spinner inline" />}</h3>
          {!val && <p className="muted">Add courses to validate the combined plan.</p>}
          {val?.checks.map((c: any) => (
            <div key={c.id} className={`check-row ${c.status}`}><span className="dot" />
              <span><strong>{c.id.replace(/_/g, " ")}</strong> — {c.message}</span>
              {c.evidence?.length > 0 && <button className="link" onClick={() => showEvidence(c.evidence, c.id)}>source</button>}
              <span className={`status tiny ${CHECK_CLS[c.status]}`}>{c.status}</span>
            </div>
          ))}
          {sched?.exam_clashes?.length > 0 && <div className="banner bad small">Exam clashes: {sched.exam_clashes.map((x: any) => `${x.courses.join(" & ")} on ${x.date}`).join("; ")}</div>}
          {sched?.binding_pairs?.length > 0 && <div className="banner bad small">Always clash: {sched.binding_pairs.map((p: string[]) => p.join(" × ")).join(", ")}</div>}
          {sched?.courses_without_sections?.length > 0 && <div className="banner bad small">No admissible section under your preferences: {sched.courses_without_sections.join(", ")}</div>}
          {sched?.unresolved?.length > 0 && <details><summary className="small">Unresolved schedule data ({sched.unresolved.length})</summary>
            <ul className="small">{sched.unresolved.map((u: string) => <li key={u}>{u}</li>)}</ul></details>}
          {sched?.metrics && <p className="small muted">{sched.metrics.early_classes} early classes · {sched.metrics.gap_hours} gap hours · days: {sched.metrics.days_used.join(", ")} · solved in {sched.elapsed_ms} ms{sched.optimal_within_budget ? " (optimal)" : " (budget reached)"}</p>}
          <div className="row">
            <input value={name} onChange={(e) => setName(e.target.value)} aria-label="plan name" />
            <button disabled={!plan.length} onClick={save}>Save plan</button>
          </div>
        </section>
      </div>
      {sched?.grid && (
        <section className="card">
          <h3>Weekly timetable</h3>
          <div className="tt" role="table" aria-label="weekly timetable">
            <div className="tt-h" role="columnheader" />
            {DAYS.map((d) => <div key={d} className="tt-h" role="columnheader">{d}</div>)}
            {HOURS.map((h, i) => <Fragment key={h}>
              <div className="tt-h" role="rowheader">{HOUR_LABEL[i]}:00</div>
              {DAYS.map((d) => {
                const c = grid[`${d}-${h}`];
                return <div key={`${d}-${h}`} className={`tt-c ${c ? `c${colors[c]}` : ""} ${prefs.free_days.includes(d) ? "free" : ""}`} role="cell">{c ?? ""}</div>;
              })}
            </Fragment>)}
          </div>
          <p className="small muted">Exams: {val.courses.map((c: any) => labels[c.offering_id]).filter(Boolean).map((o: any) =>
            `${o.course_code} ${o.exams.map((e: any) => `${e.type} ${e.date ?? e.status}`).join(", ")}`).join(" · ")}</p>
        </section>
      )}
      {saved.length > 0 && (
        <section className="card">
          <h3>Saved plans</h3>
          <table className="table">
            <thead><tr><th>Name</th><th>Courses</th><th>Saved against</th><th>Status</th><th /></tr></thead>
            <tbody>{saved.map((p) => <tr key={p.id}>
              <td>{p.name}</td><td>{p.offering_ids.length}</td>
              <td className="small">profile v{p.profile_version} · {p.dataset_version}</td>
              <td>{p.stale ? <span className="status warn" title={p.stale_reasons.join("; ")}>Stale — revalidate</span> : <span className="status ok">Current</span>}</td>
              <td><button className="link" onClick={() => setPlan(p.offering_ids)}>load</button></td>
            </tr>)}</tbody>
          </table>
        </section>
      )}
    </div>
  );
}
