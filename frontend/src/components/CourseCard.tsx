import { useState } from "react";
import { useApp } from "../App";

const ELIG: Record<string, [string, string]> = {
  eligible: ["Eligible", "ok"], needs_verification: ["Needs verification", "warn"], ineligible: ["Ineligible", "bad"],
};
const SCHED: Record<string, string> = { checked: "Clash-free with your plan", unchecked: "Schedule not checked",
  unresolved: "Schedule partly unresolved", clash: "Clashes with your plan" };

export default function CourseCard({ card }: { card: any }) {
  const { showEvidence, plan, setPlan } = useApp();
  const [open, setOpen] = useState(false);
  const inPlan = plan.includes(card.offering_id);
  const [el, cls] = ELIG[card.eligibility.status];
  const r = card.requirement;
  return (
    <article className={`course ${card.kind}`}>
      <header>
        <div>
          <div className="code">{card.course_code} <span className="muted small">· {card.credit.value} {card.credit.system === "units" ? "units" : "credit hours"} · {card.term}</span></div>
          <h3>{card.title}</h3>
        </div>
        <span className={`status ${cls}`}>{el}</span>
      </header>
      {card.reason && <p className="reason small">{card.reason}</p>}
      <div className="req-line">
        {r ? <span className={`pill ${r.remaining_need ? "req" : ""}`}>Counts as {r.label}{r.remaining_need ? "" : " (requirement already met)"}</span>
          : <span className="pill">No requirement contribution resolved</span>}
        {r?.note && <span className="small muted">{r.note}</span>}
      </div>
      {card.why.length > 0 && <ul className="why">{card.why.map((w: string) => <li key={w}>{w}</li>)}</ul>}
      {card.unknowns.length > 0 && <div className="unknowns"><strong>Could not verify:</strong><ul>{card.unknowns.map((u: string) => <li key={u}>{u}</li>)}</ul></div>}
      {card.caveats.length > 0 && <p className="caveat small">⚠ {card.caveats[0]}</p>}
      <div className="facts small">
        {card.instructor_in_charge && <span>IC: {card.instructor_in_charge}</span>}
        {card.lecture_slot && <span>Lecture: {card.lecture_slot}</span>}
        {Object.entries(card.exams).map(([k, e]: any) => <span key={k}>{k === "midsem" ? "Mid-sem" : "Compre"}: {e.date ? `${e.date} ${e.session}` : e.status.replace("_", " ")}</span>)}
        <span className={`sched ${card.schedule.status}`}>{SCHED[card.schedule.status] ?? card.schedule.status}
          {card.schedule.sections && ` (sections ${card.schedule.sections.sections.join("+")})`}</span>
      </div>
      <div className="card-actions">
        <button className="secondary" onClick={() => showEvidence(card.evidence, `${card.course_code} evidence`)}>Evidence ({card.evidence.length})</button>
        <button className="link" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Hide" : "Show"} checks</button>
        <button className={inPlan ? "secondary" : ""} onClick={() => setPlan(inPlan ? plan.filter((x) => x !== card.offering_id) : [...plan, card.offering_id])}>
          {inPlan ? "✓ In plan (remove)" : "Add to plan"}</button>
      </div>
      {open && (
        <div className="checks">
          {card.eligibility.decisive.map((c: any) => (
            <div key={c.id} className={`check-row ${c.status}`}><span className="dot" />{c.id.replace(/_/g, " ")}: {c.message}
              {c.evidence?.length > 0 && <button className="link" onClick={() => showEvidence(c.evidence, c.id)}>source</button>}</div>
          ))}
          {card.eligibility.approvals_needed.length > 0 && <p className="small">Approvals needed: {card.eligibility.approvals_needed.join(", ")}</p>}
          {card.facts?.evaluation?.length > 0 && <table className="table mini"><tbody>
            {card.facts.evaluation.map((c: any, i: number) => <tr key={i}><td>{c.name}</td><td>{c.weight ?? "?"}%</td><td className="muted">{c.nature?.replace(/_/g, " ") ?? ""}</td></tr>)}
          </tbody></table>}
          {card.facts?.makeup && <p className="small"><strong>Make-up:</strong> {card.facts.makeup}</p>}
          {card.all_contributions?.length > 1 && <p className="small muted">Could also count as: {card.all_contributions.slice(1).map((c: any) => c.label).join(", ")}</p>}
        </div>
      )}
    </article>
  );
}
