// Server-side calls to the Edge API. The Edge API is the single source of
// truth for accounts, tiers and the paper-only ledger; this site never keeps
// its own copy of either.
import 'server-only';
import { cookies } from 'next/headers';

export const SESSION_COOKIE = 'edge_session';

export function edgeUrl(): string | null {
  const u = process.env.EDGE_API_URL?.trim();
  return u ? u.replace(/\/+$/, '') : null;
}

export type Tier = 'free' | 'pro' | 'elite';

export type User = {
  email: string;
  tier: Tier;
  tier_name: string;
  features: string[];
  member_since: string;
  paused_until: string | null;
};

export type EdgeResult<T> =
  | { ok: true; data: T }
  | { ok: false; status: number; detail: string };

export async function sessionToken(): Promise<string | null> {
  const jar = await cookies();
  return jar.get(SESSION_COOKIE)?.value ?? null;
}

export async function edgeGet<T>(path: string, token?: string | null,
  params?: Record<string, string | undefined>): Promise<EdgeResult<T>> {
  const base = edgeUrl();
  if (!base) {
    return { ok: false, status: 503,
      detail: 'The Edge API address (EDGE_API_URL) is not set on this site.' };
  }
  const url = new URL(base + path);
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v) url.searchParams.set(k, v);
  }
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (token) headers.Authorization = 'Bearer ' + token;
  try {
    const r = await fetch(url, { headers, cache: 'no-store',
      signal: AbortSignal.timeout(20000) });
    const body = await r.json().catch(() => null);
    if (!r.ok) {
      const detail = typeof body?.detail === 'string' ? body.detail
        : 'The Edge API answered ' + r.status + '.';
      return { ok: false, status: r.status, detail };
    }
    return { ok: true, data: body as T };
  } catch {
    return { ok: false, status: 502,
      detail: 'The Edge API did not answer. It may be waking up; try again in a minute.' };
  }
}

export async function edgePost<T>(path: string, token: string | null,
  body: unknown): Promise<EdgeResult<T>> {
  const base = edgeUrl();
  if (!base) {
    return { ok: false, status: 503, detail: 'EDGE_API_URL is not set.' };
  }
  const headers: Record<string, string> = {
    Accept: 'application/json', 'Content-Type': 'application/json' };
  if (token) headers.Authorization = 'Bearer ' + token;
  try {
    const r = await fetch(base + path, { method: 'POST', headers,
      body: JSON.stringify(body ?? {}), cache: 'no-store',
      signal: AbortSignal.timeout(20000) });
    const data = await r.json().catch(() => null);
    if (!r.ok) {
      return { ok: false, status: r.status,
        detail: typeof data?.detail === 'string' ? data.detail
          : 'The Edge API answered ' + r.status + '.' };
    }
    return { ok: true, data: data as T };
  } catch {
    return { ok: false, status: 502, detail: 'The Edge API did not answer.' };
  }
}

export async function currentUser(): Promise<{ token: string | null;
  user: User | null }> {
  const token = await sessionToken();
  if (!token) return { token: null, user: null };
  const r = await edgeGet<{ user: User }>('/auth/me', token);
  return { token, user: r.ok ? r.data.user : null };
}
