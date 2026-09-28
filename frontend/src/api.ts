// Thin typed client for the FastAPI backend. Credentials never live here: the only secret is
// the per-profile owner token returned when the profile is created (kept in localStorage).
const BASE: string = import.meta.env.DEV ? "/api" : (import.meta.env.VITE_API_BASE ?? "");

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : (detail as any)?.message ?? JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

export interface Session {
  profileId: string;
  token: string;
}

const SESSION_KEY = "bcr.session";

export function loadSession(): Session | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function saveSession(s: Session | null) {
  try {
    if (s) localStorage.setItem(SESSION_KEY, JSON.stringify(s));
    else localStorage.removeItem(SESSION_KEY);
  } catch {
    /* storage unavailable: session lasts for this tab only */
  }
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; session?: Session | null;
  admin?: string; signal?: AbortSignal; timeoutMs?: number } = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (opts.session) headers["X-Profile-Token"] = opts.session.token;
  if (opts.admin) headers["X-Admin-Token"] = opts.admin;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? 30000);
  opts.signal?.addEventListener("abort", () => ctrl.abort());
  try {
    const res = await fetch(BASE + path, {
      method: opts.method ?? (opts.body ? "POST" : "GET"),
      headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      signal: ctrl.signal,
    });
    const text = await res.text();
    const data = text ? JSON.parse(text) : null;
    if (!res.ok) throw new ApiError(res.status, data?.detail ?? data ?? res.statusText);
    return data as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if ((e as Error).name === "AbortError") throw new ApiError(0, "The request timed out. Try again.");
    throw new ApiError(0, "Cannot reach the recommender API. Is the backend running on port 8000?");
  } finally {
    clearTimeout(timer);
  }
}

export const documentUrl = (docId: string, page: number) => `${BASE}/documents/${docId}#page=${page}`;
