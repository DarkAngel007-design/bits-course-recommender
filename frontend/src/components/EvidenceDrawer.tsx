import { useEffect, useRef, useState } from "react";
import { api, documentUrl } from "../api";

const FAMILY: Record<string, string> = {
  timetable: "Timetable 2026-27 Sem I", bulletin: "Bulletin 2025-26", regulations: "Academic Regulations",
  handout: "Course handout",
};

export default function EvidenceDrawer({ ids, title, onClose }: { ids: string[]; title?: string; onClose: () => void }) {
  const [items, setItems] = useState<any[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const uniq = Array.from(new Set(ids)).slice(0, 12);
    Promise.all(uniq.map((id) => api(`/sources/${id}`).catch(() => ({ id, missing: true }))))
      .then(setItems).catch((e) => setErr(e.message));
  }, [ids]);

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Source evidence" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <h2>{title ?? "Source evidence"}</h2>
          <button ref={closeRef} className="icon" onClick={onClose} aria-label="Close">×</button>
        </div>
        {err && <p className="error">{err}</p>}
        {!items && <div className="spinner" />}
        {items?.length === 0 && <p className="muted">No evidence attached (value was computed from the cited records above).</p>}
        {items?.map((ev) => ev.missing ? (
          <div key={ev.id} className="evidence missing">Evidence {ev.id} not found in this snapshot.</div>
        ) : (
          <div key={ev.id} className="evidence">
            <div className="ev-head">
              <span className="pill">{FAMILY[ev.family] ?? ev.family}</span>
              <span className="small muted">{ev.file} · PDF page {ev.pdf_page}{ev.printed_page ? ` (printed ${ev.printed_page})` : ""}</span>
            </div>
            <blockquote>{ev.excerpt}</blockquote>
            <div className="small muted">
              parser {ev.parser} · confidence {ev.confidence}
              {ev.document_available ? <> · <a href={documentUrl(ev.doc_id, ev.pdf_page)} target="_blank" rel="noreferrer">open PDF page ↗</a></>
                : " · raw PDF not on this server"}
            </div>
          </div>
        ))}
      </aside>
    </div>
  );
}
