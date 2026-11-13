// "Take a break": self-exclusion is held by the Edge API, which only ever
// extends a pause and never shortens it.
import { NextResponse } from 'next/server';
import { edgePost, sessionToken } from '../../../lib/edge';
import { sameOrigin } from '../../../lib/guard';

export const dynamic = 'force-dynamic';

export async function POST(req: Request) {
  if (!sameOrigin(req)) {
    return NextResponse.json({ error: 'Refused.' }, { status: 403 });
  }
  const token = await sessionToken();
  if (!token) return NextResponse.json({ error: 'Sign in first.' }, { status: 401 });
  const body = await req.json().catch(() => null);
  const days = Number(body?.days);
  if (![1, 7, 30, 90, 180, 365].includes(days)) {
    return NextResponse.json({ error: 'Choose a length.' }, { status: 422 });
  }
  const r = await edgePost<{ paused_until: string }>('/auth/pause', token, { days });
  if (!r.ok) return NextResponse.json({ error: r.detail }, { status: r.status });
  return NextResponse.json(r.data);
}
