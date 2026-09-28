import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { useApp } from "../App";
import CourseSearch from "./CourseSearch";

const YEARS = ["I", "II", "III", "IV", "V"];
export const semLabel = (n: number) => `Year ${YEARS[Math.floor((n - 1) / 2)]}, Sem ${n % 2 === 1 ? 1 : 2}`;
const GRADES = ["A", "A-", "B", "B-", "C", "C-", "D", "E", "GOOD", "CLR", "NC", "W", "RC", "I", "GA"];
const APPROVALS = ["prerequisite_waiver", "dca_prior_preparation", "hd_course_permission", "minor_admission",
  "extra_elective_permission", "other"];

const EMPTY = {
  display_name: "", campus: "Pilani", admission_year: 2025, programmes: [] as string[], current_semester: 3,
  current_term: "2026-27-S1", minor: null as string | null, interests: [] as string[], attempts: [] as any[],
  approvals: [] as any[], cgpa: null as number | null, curriculum_attested: false,
};

export default function ProfilePage() {
  const { meta, session, setSession, profile, setProfile, go } = useApp();
  const [form, setForm] = useState<any>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [demos, setDemos] = useState<any>(null);
  const [fillGrade, setFillGrade] = useState("B");
  const [interest, setInterest] = useState("");
  const [openId, setOpenId] = useState({ id: "", token: "" });

  useEffect(() => { setForm(profile ? { ...EMPTY, ...profile.profile } : EMPTY); }, [profile]);
  useEffect(() => { api("/demo-profiles").then(setDemos).catch(() => setDemos({})); }, []);

  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));
  const dualPartners = useMemo(() => {
    const first = form.programmes[0];
    if (!first) return [];
    return meta.dual_degrees.filter((d: any) => d.programmes.includes(first))
      .map((d: any) => d.programmes.find((x: string) => x !== first));
  }, [form.programmes, meta]);
  const progName = (id: string) => meta.programmes.find((p: any) => p.id === id)?.name ?? id;

  async function save() {
    setBusy(true); setMsg(null);
    const body = { ...form, admission_year: Number(form.admission_year), current_semester: Number(form.current_semester),
      cgpa: form.cgpa === "" || form.cgpa === null ? null : Number(form.cgpa), minor: form.minor || null,
      attempts: form.attempts.map((a: any) => ({ ...a, grade: a.status === "in_progress" ? null : (a.grade || null),
        attempt_order: a.attempt_order ? Number(a.attempt_order) : null })) };
    try {
      if (!session) {
        const r = await api("/profiles", { body });
        setSession({ profileId: r.id, token: r.owner_token });
        setProfile(r);
        setMsg({ kind: "ok", text: `Profile created. Keep this owner token to reopen it elsewhere: ${r.owner_token}` });
      } else {
        const r = await api(`/profiles/${session.profileId}`, { method: "PATCH", session,
          body: { expected_version: profile.version, profile: body } });
        setProfile(r);
        setMsg({ kind: "ok", text: `Saved (version ${r.version}). Dependent results were recomputed.` });
      }
    } catch (e: any) {
      if (e.status === 409) setMsg({ kind: "error", text: "This profile was changed in another tab. Reload to continue." });
      else if (e.status === 422) setMsg({ kind: "error", text: `Please fix the form: ${formatErr(e.detail)}` });
      else setMsg({ kind: "error", text: e.message });
    } finally { setBusy(false); }
  }

  async function quickFill() {
    if (!form.programmes.length) return setMsg({ kind: "error", text: "Choose programme(s) first." });
    try {
      const cur = await api(`/curricula/resolve?programmes=${encodeURIComponent(form.programmes.join(","))}`);
      const upto = Number(form.current_semester);
      const have = new Set(form.attempts.map((a: any) => a.course_code));
      const add = cur.named_placement.filter((n: any) => (YEARS.indexOf(n.year) * 2 + n.sem) < upto && !have.has(n.codes[0]))
        .map((n: any) => ({ course_code: n.codes[0], status: "completed", grade: n.codes[0] === "BITS K101" ? "GOOD" : fillGrade }));
      set("attempts", [...form.attempts, ...add]);
      setMsg({ kind: "info", text: `Added ${add.length} named courses from ${cur.name} before ${semLabel(upto)} with grade ${fillGrade}. Edit any that differ, then save.` });
    } catch (e: any) { setMsg({ kind: "error", text: e.message }); }
  }

  async function loadDemo(key: string) {
    setBusy(true);
    try {
      const r = await api("/profiles", { body: { ...demos[key].profile, display_name: demos[key].label } });
      setSession({ profileId: r.id, token: r.owner_token });
      setProfile(r);
      setMsg({ kind: "ok", text: `Loaded synthetic demo profile "${demos[key].label}".` });
    } catch (e: any) { setMsg({ kind: "error", text: e.message }); } finally { setBusy(false); }
  }

  const upd = (i: number, k: string, v: any) => set("attempts", form.attempts.map((a: any, j: number) => j === i ? { ...a, [k]: v } : a));
  const warnings: any[] = profile?.warnings ?? [];

  return (
    <div className="page">
      <div className="page-head">
        <h1>{session ? "Your academic profile" : "Create a profile"}</h1>
        {session && <button onClick={() => go("progress")}>View progress →</button>}
      </div>
      {msg && <div className={`banner ${msg.kind}`} role="status">{msg.text}</div>}

      {!session && (
        <div className="grid2">
          <section className="card">
            <h3>Try a synthetic demo profile</h3>
            <p className="muted small">Clearly synthetic students used by the test suite. Useful for a quick tour.</p>
            <div className="stack">
              {demos && Object.entries(demos).map(([k, d]: any) => (
                <button key={k} className="secondary left" disabled={busy} onClick={() => loadDemo(k)}>{d.label}</button>
              ))}
            </div>
          </section>
          <section className="card">
            <h3>Reopen an existing profile</h3>
            <label>Profile id<input value={openId.id} onChange={(e) => setOpenId({ ...openId, id: e.target.value.trim() })} placeholder="prof_…" /></label>
            <label>Owner token<input value={openId.token} onChange={(e) => setOpenId({ ...openId, token: e.target.value.trim() })} /></label>
            <button disabled={!openId.id || !openId.token} onClick={() => setSession({ profileId: openId.id, token: openId.token })}>Open</button>
          </section>
        </div>
      )}

      <section className="card">
        <h3>Programme and cohort</h3>
        <div className="form-grid">
          <label>Name (optional)<input value={form.display_name ?? ""} onChange={(e) => set("display_name", e.target.value)} /></label>
          <label>Campus
            <select value={form.campus} onChange={(e) => set("campus", e.target.value)}>
              {["Pilani", "Goa", "Hyderabad", "Dubai", "Mumbai"].map((c) => <option key={c}>{c}</option>)}
            </select>
          </label>
          <label>Admission year<input type="number" min={2015} max={2030} value={form.admission_year}
            onChange={(e) => set("admission_year", e.target.value)} /></label>
          <label>Registering for
            <select value={form.current_semester} onChange={(e) => set("current_semester", Number(e.target.value))}>
              {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => <option key={n} value={n}>{semLabel(n)}</option>)}
            </select>
          </label>
          <label>Degree
            <select value={form.programmes[0] ?? ""} onChange={(e) => set("programmes", e.target.value ? [e.target.value] : [])}>
              <option value="">Choose…</option>
              {meta.programmes.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <label>Dual degree (second programme)
            <select value={form.programmes[1] ?? ""} disabled={!dualPartners.length}
              onChange={(e) => set("programmes", e.target.value ? [form.programmes[0], e.target.value] : [form.programmes[0]])}>
              <option value="">None (single degree)</option>
              {dualPartners.map((id: string) => <option key={id} value={id}>{progName(id)}</option>)}
            </select>
          </label>
          <label>Minor
            <select value={form.minor ?? ""} onChange={(e) => set("minor", e.target.value || null)}>
              <option value="">None</option>
              {meta.minors.map((m: string) => <option key={m}>{m}</option>)}
            </select>
          </label>
          <label>CGPA (optional)<input type="number" step="0.01" min={0} max={10} value={form.cgpa ?? ""}
            onChange={(e) => set("cgpa", e.target.value)} /></label>
        </div>
        {Number(form.admission_year) !== 2025 && Number(form.admission_year) < 2026 && (
          <label className="check">
            <input type="checkbox" checked={form.curriculum_attested} onChange={(e) => set("curriculum_attested", e.target.checked)} />
            I confirm the Bulletin 2025-26 semester chart applies to my programme. (The supplied bulletin only establishes it
            for 2025 admissions; results will be labelled provisional.)
          </label>
        )}
        {Number(form.admission_year) >= 2026 && <div className="banner warn">2026 admissions follow the credit-hour framework;
          no curriculum for it was supplied, so requirements and recommendations are unavailable.</div>}
        {form.campus !== "Pilani" && <div className="banner warn">Only the Pilani timetable was supplied; offerings for {form.campus} cannot be checked.</div>}
      </section>

      <section className="card">
        <h3>Academic interests</h3>
        <div className="chips">
          {form.interests.map((t: string) => (
            <span key={t} className="chip">{t}<button aria-label={`remove ${t}`} onClick={() => set("interests", form.interests.filter((x: string) => x !== t))}>×</button></span>
          ))}
        </div>
        <div className="row">
          <input list="topic-list" placeholder="e.g. machine learning, finance" value={interest}
            onChange={(e) => setInterest(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && interest.trim()) { set("interests", [...form.interests, interest.trim()]); setInterest(""); } }} />
          <datalist id="topic-list">{meta.topics.map((t: any) => <option key={t.id} value={t.label} />)}</datalist>
          <button className="secondary" disabled={!interest.trim()} onClick={() => { set("interests", [...form.interests, interest.trim()]); setInterest(""); }}>Add</button>
        </div>
      </section>

      <section className="card">
        <div className="page-head">
          <h3>Course history ({form.attempts.length})</h3>
          <div className="row small">
            <span className="muted">Quick add named courses before this semester with grade</span>
            <select value={fillGrade} onChange={(e) => setFillGrade(e.target.value)} aria-label="grade for quick add">
              {GRADES.slice(0, 8).map((g) => <option key={g}>{g}</option>)}
            </select>
            <button className="secondary" onClick={quickFill}>Add</button>
          </div>
        </div>
        <p className="muted small">Completed attempts need a grade or report. Mark courses you are taking now as in progress:
          they count as projected progress, never as earned. For a repeat, add another row with a higher attempt number.</p>
        <CourseSearch placeholder="Add a course (code or title)…" onPick={(c) => set("attempts", [...form.attempts, { course_code: c.code, status: "completed", grade: "" }])} />
        <table className="table">
          <thead><tr><th>Course</th><th>Status</th><th>Grade / report</th><th>Attempt #</th><th /></tr></thead>
          <tbody>
            {form.attempts.map((a: any, i: number) => {
              const warn = warnings.find((w) => w.course === a.course_code);
              return (
                <tr key={i} className={warn ? "row-warn" : ""}>
                  <td><code>{a.course_code}</code>{warn && <div className="small warn-text">{warn.message}</div>}</td>
                  <td><select value={a.status} onChange={(e) => upd(i, "status", e.target.value)} aria-label="status">
                    <option value="completed">completed</option><option value="in_progress">in progress</option></select></td>
                  <td><select value={a.grade ?? ""} disabled={a.status === "in_progress"} onChange={(e) => upd(i, "grade", e.target.value)} aria-label="grade">
                    <option value="">—</option>{GRADES.map((g) => <option key={g}>{g}</option>)}</select></td>
                  <td><input className="narrow" type="number" min={1} max={5} value={a.attempt_order ?? ""} onChange={(e) => upd(i, "attempt_order", e.target.value)} aria-label="attempt number" /></td>
                  <td><button className="link" onClick={() => set("attempts", form.attempts.filter((_: any, j: number) => j !== i))}>remove</button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h3>Recorded approvals</h3>
        <p className="muted small">Only record approvals you actually hold (e.g. a Dean's prerequisite waiver). They change eligibility checks.</p>
        {form.approvals.map((a: any, i: number) => (
          <div className="row" key={i}>
            <select value={a.type} onChange={(e) => set("approvals", form.approvals.map((x: any, j: number) => j === i ? { ...x, type: e.target.value } : x))}>
              {APPROVALS.map((t) => <option key={t}>{t}</option>)}</select>
            <input placeholder="course (optional)" value={a.course_code ?? ""} onChange={(e) => set("approvals", form.approvals.map((x: any, j: number) => j === i ? { ...x, course_code: e.target.value || null } : x))} />
            <button className="link" onClick={() => set("approvals", form.approvals.filter((_: any, j: number) => j !== i))}>remove</button>
          </div>
        ))}
        <button className="secondary" onClick={() => set("approvals", [...form.approvals, { type: "prerequisite_waiver", course_code: null }])}>Add approval</button>
      </section>

      <div className="sticky-actions">
        <button disabled={busy || !form.programmes.length} onClick={save}>{busy ? "Saving…" : session ? "Save changes" : "Create profile"}</button>
        {session && <span className="muted small">Profile {session.profileId} · version {profile?.version}</span>}
      </div>
    </div>
  );
}

function formatErr(d: any): string {
  if (Array.isArray(d)) return d.map((x) => `${(x.loc ?? []).slice(1).join(".")}: ${x.msg}`).join("; ");
  return typeof d === "string" ? d : JSON.stringify(d);
}
