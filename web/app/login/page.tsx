'use client';
import { Suspense, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';

const EDGE = (process.env.NEXT_PUBLIC_EDGE_API_URL ?? '').replace(/\/+$/, '');

function LoginForm() {
  const params = useSearchParams();
  const router = useRouter();
  const [mode, setMode] = useState<'signup' | 'login'>(
    params.get('mode') === 'login' ? 'login' : 'signup');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [adult, setAdult] = useState(false);
  const [terms, setTerms] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      if (!EDGE) throw Error('Sign-in is not set up on this site yet (NEXT_PUBLIC_EDGE_API_URL).');
      const path = mode === 'signup' ? '/auth/signup' : '/auth/login';
      const body = mode === 'signup'
        ? { email, password, confirm_21_or_older: adult, accept_terms: terms }
        : { email, password };
      let r: Response;
      try {
        r = await fetch(EDGE + path, { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body) });
      } catch {
        throw Error('Could not reach Edge. The server may be waking up; try again in a minute.');
      }
      const data = await r.json().catch(() => null);
      if (r.status === 429) {
        const wait = Number(r.headers.get('Retry-After')) || 900;
        throw Error(`Too many tries. Wait about ${Math.ceil(wait / 60)} minutes.`);
      }
      if (!r.ok) {
        const d = data?.detail;
        throw Error(typeof d === 'string' ? d : Array.isArray(d)
          ? 'Check the email address and use a password of 10 or more characters.'
          : 'Sign-in failed.');
      }
      const s = await fetch('/api/session', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: data.token,
          expires_in_days: data.expires_in_days }) });
      if (!s.ok) throw Error('Signed in, but this site could not save the session. Try again.');
      setPassword('');
      router.push('/dashboard');
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed.');
    } finally {
      setBusy(false);
    }
  }

  const signup = mode === 'signup';
  return (
    <main className="authbox card">
      <div className="eyebrow">EDGE MEMBER ACCESS</div>
      <h1>{signup ? 'Create your account' : 'Welcome back'}</h1>
      <p className="muted">
        {signup ? 'Free to start. 21+ only. Analytics and a paper record, never real wagers.'
          : 'Sign in to your intelligence terminal.'}
      </p>
      <form onSubmit={submit}>
        <label className="field"><span>Email address</span>
          <input type="email" autoComplete="email" required value={email}
            onChange={e => setEmail(e.target.value)} /></label>
        <label className="field"><span>Password{signup ? ' (10+ characters)' : ''}</span>
          <input type="password" required minLength={signup ? 10 : 1}
            autoComplete={signup ? 'new-password' : 'current-password'}
            value={password} onChange={e => setPassword(e.target.value)} /></label>
        {signup && <>
          <label className="check"><input type="checkbox" checked={adult}
            onChange={e => setAdult(e.target.checked)} required />
            <span>I am 21 or older.</span></label>
          <label className="check"><input type="checkbox" checked={terms}
            onChange={e => setTerms(e.target.checked)} required />
            <span>I accept the terms. Edge is sports analytics only: it takes no bets,
              holds no money and guarantees no results.</span></label>
        </>}
        <button className="btn" style={{ width: '100%' }} disabled={busy}>
          {busy ? 'Please wait…' : signup ? 'Create free account' : 'Sign in'}</button>
      </form>
      {error && <p className="error" role="alert">{error}</p>}
      <p className="muted">
        {signup ? 'Already a member?' : 'New here?'}{' '}
        <button type="button" className="linkbtn"
          onClick={() => { setMode(signup ? 'login' : 'signup'); setError(''); }}>
          {signup ? 'Sign in' : 'Create an account'}</button>
      </p>
      <p className="muted small">Gambling problem? Call <a className="green" href="tel:18004264237">1-800-GAMBLER</a>. <Link className="green" href="/responsible">Responsible use</Link></p>
    </main>
  );
}

export default function Login() {
  return <Suspense><LoginForm /></Suspense>;
}
