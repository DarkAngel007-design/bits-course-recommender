import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, loadSession, saveSession, type Session } from "./api";
import EvidenceDrawer from "./components/EvidenceDrawer";
import ProfilePage from "./components/ProfilePage";
import ProgressPage from "./components/ProgressPage";
import RecommendPage from "./components/RecommendPage";
import PlanPage from "./components/PlanPage";
import DataPage from "./components/DataPage";

export interface Ctx {
  meta: any;
  session: Session | null;
  setSession: (s: Session | null) => void;
  profile: any | null; // {id, version, profile, warnings}
  reloadProfile: () => Promise<void>;
  setProfile: (p: any) => void;
  plan: string[];
  setPlan: (ids: string[]) => void;
  showEvidence: (ids: string[], title?: string) => void;
  go: (tab: Tab) => void;
}

export const AppCtx = createContext<Ctx>(null as unknown as Ctx);
export const useApp = () => useContext(AppCtx);

type Tab = "profile" | "progress" | "recommend" | "plan" | "data";
const TABS: { id: Tab; label: string; needsProfile: boolean }[] = [
  { id: "profile", label: "Profile", needsProfile: false },
  { id: "progress", label: "Progress", needsProfile: true },
  { id: "recommend", label: "Recommendations", needsProfile: true },
  { id: "plan", label: "Semester plan", needsProfile: true },
  { id: "data", label: "Data & coverage", needsProfile: false },
];

function readTab(): Tab {
  const h = window.location.hash.replace("#/", "") as Tab;
  return TABS.some((t) => t.id === h) ? h : "profile";
}

function planKey(pid: string) {
  return `bcr.plan.${pid}`;
}

export default function App() {
  const [meta, setMeta] = useState<any>(null);
  const [metaErr, setMetaErr] = useState<string | null>(null);
  const [session, setSessionState] = useState<Session | null>(loadSession());
  const [profile, setProfile] = useState<any | null>(null);
  const [tab, setTab] = useState<Tab>(readTab());
  const [plan, setPlanState] = useState<string[]>([]);
  const [evidence, setEvidence] = useState<{ ids: string[]; title?: string } | null>(null);

  useEffect(() => {
    api("/meta").then(setMeta).catch((e) => setMetaErr(e.message));
    const onHash = () => setTab(readTab());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const setSession = useCallback((s: Session | null) => {
    saveSession(s);
    setSessionState(s);
    if (!s) setProfile(null);
  }, []);

  const reloadProfile = useCallback(async () => {
    if (!session) return;
    try {
      setProfile(await api(`/profiles/${session.profileId}`, { session }));
    } catch (e: any) {
      if (e.status === 403 || e.status === 404) setSession(null);
    }
  }, [session, setSession]);

  useEffect(() => {
    reloadProfile();
    if (session) {
      try {
        setPlanState(JSON.parse(localStorage.getItem(planKey(session.profileId)) ?? "[]"));
      } catch {
        setPlanState([]);
      }
    } else setPlanState([]);
  }, [session, reloadProfile]);

  const setPlan = useCallback((ids: string[]) => {
    setPlanState(ids);
    try {
      if (session) localStorage.setItem(planKey(session.profileId), JSON.stringify(ids));
    } catch {
      /* ignore */
    }
  }, [session]);

  const go = (t: Tab) => {
    window.location.hash = `#/${t}`;
    setTab(t);
  };

  const ctx: Ctx = useMemo(() => ({
    meta, session, setSession, profile, reloadProfile, setProfile, plan, setPlan,
    showEvidence: (ids, title) => setEvidence({ ids, title }), go,
  }), [meta, session, setSession, profile, reloadProfile, plan, setPlan]);

  if (metaErr) {
    return <div className="fatal"><h1>BITS Course Recommender</h1><p className="error">{metaErr}</p>
      <p>Start the API with <code>make api</code> (or <code>uvicorn backend.app.api.main:app --port 8000</code>).</p></div>;
  }
  if (!meta) return <div className="fatal"><div className="spinner" aria-label="Loading" /> Loading dataset…</div>;

  const p = profile?.profile;
  return (
    <AppCtx.Provider value={ctx}>
      <header className="topbar">
        <div className="brand">
          <span className="logo">B</span>
          <div>
            <strong>BITS Course Recommender</strong>
            <div className="muted small">{meta.term.label} · {meta.term.campus} · dataset <code>{meta.dataset_version}</code></div>
          </div>
        </div>
        <nav className="tabs" role="tablist">
          {TABS.map((t) => (
            <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? "active" : ""}
              disabled={t.needsProfile && !profile} onClick={() => go(t.id)}>
              {t.label}{t.id === "plan" && plan.length ? <span className="badge">{plan.length}</span> : null}
            </button>
          ))}
        </nav>
        <div className="who small">
          {p ? <><span className="who-name" title={p.display_name}>{p.display_name || "Profile"}</span> · {p.admission_year} · sem {p.current_semester}
            <button className="link" onClick={() => setSession(null)}>switch</button></> : <span className="muted">No profile</span>}
        </div>
      </header>
      {!meta.llm_enabled && (
        <div className="banner info">LLM not configured: natural-language queries use the deterministic parser.
          Profile, progress and structured filters work fully.</div>
      )}
      <main>
        {tab === "profile" && <ProfilePage />}
        {tab === "progress" && profile && <ProgressPage />}
        {tab === "recommend" && profile && <RecommendPage />}
        {tab === "plan" && profile && <PlanPage />}
        {tab === "data" && <DataPage />}
      </main>
      {evidence && <EvidenceDrawer ids={evidence.ids} title={evidence.title} onClose={() => setEvidence(null)} />}
    </AppCtx.Provider>
  );
}
