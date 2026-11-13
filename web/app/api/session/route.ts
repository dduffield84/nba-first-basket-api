// The browser signs in directly with the Edge API (so Edge's per-address
// sign-in limits see the member's own address), then hands the session
// token here. This route checks it with Edge and keeps it in an httpOnly
// cookie, so page scripts never hold it after sign-in.
import { NextResponse } from 'next/server';
import { SESSION_COOKIE, edgeGet, edgePost, sessionToken } from '../../../lib/edge';
import { sameOrigin } from '../../../lib/guard';

export const dynamic = 'force-dynamic';

export async function POST(req: Request) {
  if (!sameOrigin(req)) {
    return NextResponse.json({ error: 'Refused.' }, { status: 403 });
  }
  const body = await req.json().catch(() => null);
  const token = typeof body?.token === 'string' ? body.token : '';
  if (!/^[A-Za-z0-9_-]{20,200}$/.test(token)) {
    return NextResponse.json({ error: 'Sign in again.' }, { status: 400 });
  }
  const me = await edgeGet<{ user: unknown }>('/auth/me', token);
  if (!me.ok) {
    return NextResponse.json({ error: me.detail }, { status: 401 });
  }
  const days = Math.min(Math.max(Number(body?.expires_in_days) || 30, 1), 30);
  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, token, {
    httpOnly: true, secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax', path: '/', maxAge: days * 86400 });
  return res;
}

export async function DELETE(req: Request) {
  if (!sameOrigin(req)) {
    return NextResponse.json({ error: 'Refused.' }, { status: 403 });
  }
  const token = await sessionToken();
  if (token) await edgePost('/auth/logout', token, {});
  const res = NextResponse.json({ signed_out: true });
  res.cookies.set(SESSION_COOKIE, '', { httpOnly: true, path: '/', maxAge: 0 });
  return res;
}
