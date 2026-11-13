'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';

export function SignOut() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <button className="btn secondary small" disabled={busy} onClick={async () => {
      setBusy(true);
      await fetch('/api/session', { method: 'DELETE' }).catch(() => null);
      router.push('/');
      router.refresh();
    }}>{busy ? 'Signing out…' : 'Sign out'}</button>
  );
}

export function TakeABreak() {
  const router = useRouter();
  const [days, setDays] = useState('7');
  const [sure, setSure] = useState(false);
  const [msg, setMsg] = useState('');
  async function go() {
    setMsg('');
    const r = await fetch('/api/pause', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ days: Number(days) }) }).catch(() => null);
    const data = await r?.json().catch(() => null);
    if (!r?.ok) { setMsg(data?.error ?? 'That did not work. Try again.'); return; }
    router.refresh();
  }
  return (
    <div>
      <p className="muted small">Pause your own access. A pause can be made longer, never shorter.</p>
      <div className="row">
        <select value={days} onChange={e => setDays(e.target.value)} aria-label="Length of the break"
          style={{ padding: 12, borderRadius: 8, background: '#0a1218', color: 'white', border: '1px solid #3c4c54' }}>
          {[['1', '1 day'], ['7', '1 week'], ['30', '30 days'], ['90', '90 days'], ['180', '6 months'], ['365', '1 year']]
            .map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        {!sure
          ? <button className="btn secondary small" onClick={() => setSure(true)}>Take a break</button>
          : <button className="btn small" onClick={go}>Yes, pause my access</button>}
      </div>
      {msg && <p className="error" role="alert">{msg}</p>}
    </div>
  );
}
