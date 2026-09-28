import { useApp } from "../App";

const HARD_PROPS: Record<string, { label: string; kind: "bool" | "comp" | "text" | "num" }> = {
  midsem_present: { label: "Has mid-sem exam", kind: "bool" },
  compre_present: { label: "Has comprehensive exam", kind: "bool" },
  attendance_none: { label: "No attendance requirement", kind: "bool" },
  has_component: { label: "Has component", kind: "comp" },
  no_component: { label: "No component", kind: "comp" },
  department: { label: "Department prefix", kind: "text" },
  level: { label: "Level (F/G)", kind: "text" },
  units: { label: "Units", kind: "num" },
  instructor: { label: "Instructor", kind: "text" },
  course_code: { label: "Course code", kind: "text" },
};
const COMPS = ["quiz", "project", "lab", "assignment", "presentation", "viva", "case_study"];
const SOFT = { project_based: "Project-based evaluation", lenient_makeup: "Fewer make-up restrictions", open_book: "Open-book share",
  low_exam_weight: "Lower exam weight" } as Record<string, string>;
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export default function IntentEditor({ intent, onChange, onRun }: { intent: any; onChange: (i: any) => void; onRun: () => void }) {
  const { meta } = useApp();
  const set = (k: string, v: any) => onChange({ ...intent, [k]: v });
  const topicLabel = (id: string) => meta.topics.find((t: any) => t.id === id)?.label ?? id;
  return (
    <div className="intent-editor">
      <div className="form-grid">
        <label>Category
          <select value={intent.category} onChange={(e) => set("category", e.target.value)}>
            {["ANY", "CDC", "DEL", "HUEL", "OPEL", "MINOR"].map((c) => <option key={c}>{c}</option>)}
          </select>
        </label>
        <label>How many<input type="number" min={1} max={20} value={intent.count} onChange={(e) => set("count", Number(e.target.value) || 5)} /></label>
        <label>Add topic
          <select value="" onChange={(e) => e.target.value && set("topics", [...intent.topics, e.target.value])}>
            <option value="">Choose…</option>
            {meta.topics.filter((t: any) => !intent.topics.includes(t.id)).map((t: any) => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>
        </label>
      </div>
      <div className="chips">
        {intent.topics.map((t: string) => <span key={t} className="chip topic">{topicLabel(t)}<button aria-label="remove topic" onClick={() => set("topics", intent.topics.filter((x: string) => x !== t))}>×</button></span>)}
        {intent.search_terms.map((t: string) => <span key={t} className="chip">“{t}”<button aria-label="remove term" onClick={() => set("search_terms", intent.search_terms.filter((x: string) => x !== t))}>×</button></span>)}
      </div>
      {intent.topics.length > 0 && <label className="check small">
        <input type="checkbox" checked={intent.topic_mode === "filter"} onChange={(e) => set("topic_mode", e.target.checked ? "filter" : "rank")} />
        Only show courses matching these topics {intent.topic_mode === "rank" && <span className="muted">(topics come from your profile interests and only affect ordering)</span>}</label>}
      <h4>Hard constraints <span className="muted small">(must be verified from evidence)</span></h4>
      {intent.hard_filters.length === 0 && <p className="muted small">None.</p>}
      {intent.hard_filters.map((f: any, i: number) => {
        const meta_ = HARD_PROPS[f.property];
        const upd = (v: any) => set("hard_filters", intent.hard_filters.map((x: any, j: number) => j === i ? { ...x, value: v } : x));
        return (
          <div key={i} className="row filter">
            <select value={f.property} onChange={(e) => {
              const property = e.target.value;
              const kind = HARD_PROPS[property].kind;
              const value = kind === "bool" ? true : kind === "comp" ? COMPS[0] : kind === "num" ? 3 : "";
              set("hard_filters", intent.hard_filters.map((x: any, j: number) => j === i ? { property, value } : x));
            }}>
              {Object.entries(HARD_PROPS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
            {meta_?.kind === "bool" && <select value={String(f.value)} onChange={(e) => upd(e.target.value === "true")}><option value="true">yes</option>{f.property !== "attendance_none" && <option value="false">no</option>}</select>}
            {meta_?.kind === "comp" && <select value={f.value} onChange={(e) => upd(e.target.value)}>{COMPS.map((c) => <option key={c}>{c}</option>)}</select>}
            {meta_?.kind === "text" && <input value={f.value} onChange={(e) => upd(e.target.value)} />}
            {meta_?.kind === "num" && <input type="number" min={0} step={1} value={f.value} onChange={(e) => upd(Number(e.target.value))} />}
            <button className="link" onClick={() => set("hard_filters", intent.hard_filters.filter((_: any, j: number) => j !== i))}>remove</button>
          </div>
        );
      })}
      <button className="secondary small-btn" onClick={() => set("hard_filters", [...intent.hard_filters, { property: "midsem_present", value: false }])}>+ constraint</button>
      <h4>Preferences <span className="muted small">(rank only)</span></h4>
      <div className="chips">
        {Object.entries(SOFT).map(([k, label]) => {
          const on = intent.soft_preferences.some((p: any) => p.property === k);
          return <button key={k} className={`chip-btn ${on ? "on" : ""}`} aria-pressed={on}
            onClick={() => set("soft_preferences", on ? intent.soft_preferences.filter((p: any) => p.property !== k)
              : [...intent.soft_preferences, { property: k, weight: 1 }])}>{label}</button>;
        })}
      </div>
      <h4>Timetable</h4>
      <div className="row small">
        <label className="check"><input type="checkbox" checked={intent.schedule.no_early_classes}
          onChange={(e) => set("schedule", { ...intent.schedule, no_early_classes: e.target.checked })} /> No 8 AM classes</label>
        <label className="check"><input type="checkbox" checked={intent.schedule.compact}
          onChange={(e) => set("schedule", { ...intent.schedule, compact: e.target.checked })} /> Compact days</label>
        <span>Free day:</span>
        {DAYS.map((d) => <label key={d} className="check"><input type="checkbox" checked={intent.schedule.free_days.includes(d)}
          onChange={(e) => set("schedule", { ...intent.schedule, free_days: e.target.checked ? [...intent.schedule.free_days, d] : intent.schedule.free_days.filter((x: string) => x !== d) })} />{d}</label>)}
      </div>
      <button onClick={onRun}>Run with these constraints</button>
    </div>
  );
}
