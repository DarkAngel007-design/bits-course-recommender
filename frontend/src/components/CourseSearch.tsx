import { useEffect, useRef, useState } from "react";
import { api } from "../api";

export default function CourseSearch({ onPick, placeholder, offeredOnly }: {
  onPick: (c: any) => void; placeholder?: string; offeredOnly?: boolean;
}) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const t = useRef<number | undefined>(undefined);

  useEffect(() => {
    window.clearTimeout(t.current);
    if (q.trim().length < 2) { setRes([]); return; }
    t.current = window.setTimeout(() => {
      api(`/courses?q=${encodeURIComponent(q)}&limit=12${offeredOnly ? "&offered_only=true" : ""}`)
        .then((r) => { setRes(r.results); setActive(0); setOpen(true); }).catch(() => setRes([]));
    }, 180);
  }, [q, offeredOnly]);

  const pick = (c: any) => { onPick(c); setQ(""); setRes([]); setOpen(false); };
  return (
    <div className="combo">
      <input value={q} placeholder={placeholder} aria-label={placeholder} role="combobox" aria-expanded={open}
        onChange={(e) => setQ(e.target.value)} onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setActive((a) => Math.min(a + 1, res.length - 1));
          if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0));
          if (e.key === "Enter" && res[active]) { e.preventDefault(); pick(res[active]); }
        }} />
      {open && res.length > 0 && (
        <ul className="combo-list" role="listbox">
          {res.map((c, i) => (
            <li key={c.code} role="option" aria-selected={i === active} className={i === active ? "active" : ""}
              onMouseDown={() => pick(c)}>
              <code>{c.code}</code> {c.title} {c.units != null && <span className="muted">· {c.units}U</span>}
              {c.offered && <span className="pill tiny">offered</span>}
            </li>
          ))}
        </ul>
      )}
      {open && q.length >= 2 && res.length === 0 && <div className="combo-list empty">No course with that code/title in the supplied documents.</div>}
    </div>
  );
}
