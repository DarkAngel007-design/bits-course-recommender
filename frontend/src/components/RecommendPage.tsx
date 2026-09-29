import { useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../App";
import CourseCard from "./CourseCard";
import IntentEditor from "./IntentEditor";
import { ScopeBanner } from "./ProgressPage";

const EXAMPLES = [
  "Suggest DELs related to AI.",
  "I want an OPEL with no attendance requirement.",
  "Suggest courses with no midsem and a lenient makeup policy.",
  "I need a HUEL and prefer project-based evaluation.",
  "Suggest an AI-related DEL with no midsem",
  "Two finance OPELs with no 8 AM classes, keep Saturday free",
];

export default function RecommendPage() {
  const { meta, session, plan } = useApp();
  const [query, setQuery] = useState("");
  const [res, setRes] = useState<any>(null);
  const [intent, setIntent] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [opts, setOpts] = useState({ use_llm: true, strict_prerequisites: false, with_plan: true });
  const abort = useRef<AbortController | null>(null);

  async function run(body: any) {
    abort.current?.abort();
    abort.current = new AbortController();
    setLoading(true); setErr(null);
    try {
      const r = await api("/recommendations", {
        session, signal: abort.current.signal, timeoutMs: 45000,
        body: { profile_id: session!.profileId, use_llm: opts.use_llm, strict_prerequisites: opts.strict_prerequisites,
          plan_offering_ids: opts.with_plan ? plan : [], ...body },
      });
      setRes(r);
      setIntent(r.parsed_intent);
    } catch (e: any) {
      if (e.status !== 0 || !abort.current?.signal.aborted) setErr(e.message);
    } finally { setLoading(false); }
  }

  const submit = (q: string) => { setQuery(q); if (q.trim()) run({ query: q }); };

  return (
    <div className="page">
      <h1>Ask for courses</h1>
      <form className="ask" onSubmit={(e) => { e.preventDefault(); submit(query); }}>
        <label htmlFor="q" className="sr-only">Your request</label>
        <input id="q" value={query} onChange={(e) => setQuery(e.target.value)} maxLength={500}
          placeholder="e.g. Suggest an AI-related DEL with no midsem" autoFocus />
        <button type="submit" disabled={loading || !query.trim()}>{loading ? "Working…" : "Recommend"}</button>
      </form>
      <div className="chips">
        {EXAMPLES.map((q) => <button key={q} className="chip-btn" onClick={() => submit(q)}>{q}</button>)}
      </div>
      <div className="row small options">
        <label className="check"><input type="checkbox" checked={opts.use_llm && meta.llm_enabled} disabled={!meta.llm_enabled}
          onChange={(e) => setOpts({ ...opts, use_llm: e.target.checked })} /> Use LLM to interpret {meta.llm_enabled ? `(${meta.llm_provider}: ${meta.llm_model})` : "(not configured)"}</label>
        <label className="check"><input type="checkbox" checked={opts.strict_prerequisites}
          onChange={(e) => setOpts({ ...opts, strict_prerequisites: e.target.checked })} /> Strict prerequisites
          <span className="muted" title="The authoritative prerequisite list is on an external portal that was not supplied. Strict mode treats courses without a printed prerequisite as needing verification."> (?)</span></label>
        <label className="check"><input type="checkbox" checked={opts.with_plan}
          onChange={(e) => setOpts({ ...opts, with_plan: e.target.checked })} /> Check clashes against my plan ({plan.length})</label>
      </div>

      {err && <div className="banner bad" role="alert">{err} <button className="link" onClick={() => submit(query)}>retry</button></div>}
      {loading && <div className="loading"><div className="spinner" /> Checking requirements, eligibility and evidence…</div>}

      {res && !loading && <>
        <ScopeBanner scope={res.scope} />
        <section className="card intent">
          <div className="page-head">
            <h3>How I read your request <span className="pill tiny">{res.parser?.startsWith("llm") ? `LLM · ${res.parser.split(":")[1]}` : res.parser === "edited" ? "edited" : "rule-based"}</span></h3>
            {res.degraded && <span className="small muted">{res.degraded}: deterministic parser used</span>}
          </div>
          {intent && <IntentEditor intent={intent} onChange={setIntent} onRun={() => run({ intent, query })} />}
          {res.parsed_intent.clarifications?.map((c: string) => <div key={c} className="banner info small">{c}</div>)}
          {res.parsed_intent.unsupported?.map((c: string) => <div key={c} className="banner warn small">Not supported by the documents — {c}</div>)}
        </section>

        <div className="result-head">
          <h2>{res.message}</h2>
          <span className="small muted">{res.stats.offerings_considered} offerings checked · {res.stats.ineligible} ineligible ·
            schedule {res.schedule_check} · {res.stats.elapsed_ms} ms · ranking {res.versions.ranking}</span>
        </div>

        {res.matches.length > 0 && <div className="cards">{res.matches.map((m: any) => <CourseCard key={m.offering_id} card={m} />)}</div>}

        {res.status === "no_verified_match" && res.binding_filters && (
          <section className="card">
            <h3>Why nothing matched</h3>
            <ul className="list">
              {res.binding_filters.filters.map((f: any) => <li key={f.filter + f.kind}>
                <code>{f.filter}</code>: {f.kind === "unknown" ? `${f.unverifiable} candidate(s) could not be verified`
                  : f.kind === "no_topic_match" ? `${f.excluded} candidate(s) had no match to your topics`
                  : `excluded ${f.excluded} candidate(s)`}</li>)}
              <li className="muted">{res.binding_filters.ineligible_offerings} offerings were academically ineligible for you.</li>
            </ul>
            <p className="small muted">Hard filters are never relaxed silently. Remove or edit a constraint above and run again.</p>
          </section>
        )}

        {res.unverified.length > 0 && (
          <section className="unverified">
            <h2>Needs verification <span className="muted small">— not recommendations; a fact or approval is missing</span></h2>
            <div className="cards">{res.unverified.map((m: any) => <CourseCard key={m.offering_id} card={m} />)}</div>
          </section>
        )}
        {res.alternatives.length > 0 && (
          <section className="alternatives">
            <h2>Alternatives <span className="muted small">— clearly outside at least one part of your request</span></h2>
            <div className="cards">{res.alternatives.map((m: any) => <CourseCard key={m.offering_id + m.kind} card={m} />)}</div>
          </section>
        )}
      </>}
    </div>
  );
}
