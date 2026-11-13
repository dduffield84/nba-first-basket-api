import Link from 'next/link';
import { edgeGet } from '../../lib/edge';

export const dynamic = 'force-dynamic';

type TierInfo = { name: string; price: string; features: string[] };
type Tiers = { tiers: Record<string, TierInfo>; free_games_per_day: number; payments: string };

export default async function Pricing() {
  // Names, prices and features come from the Edge API, so the site and the
  // server can never disagree about what a tier includes.
  const r = await edgeGet<Tiers>('/auth/tiers');
  return (
    <main className="pricing">
      <div className="eyebrow" style={{ textAlign: 'center' }}>MEMBERSHIP</div>
      <h1>Choose your <span className="green">edge.</span></h1>
      <p className="muted" style={{ textAlign: 'center', marginBottom: 30 }}>
        Payments are not open yet. During the private beta, Pro and Elite are assigned by the Edge team.
      </p>
      {!r.ok ? <div className="warn">Plans could not be loaded: {r.detail}</div> : (
        <div className="pricegrid">
          {(['free', 'pro', 'elite'] as const).map(t => {
            const p = r.data.tiers[t];
            if (!p) return null;
            const [amount, per] = p.price.split('/');
            return (
              <article key={t} className={'card pricecard ' + (t === 'pro' ? 'featured' : '')}>
                <div className="eyebrow">{p.name}</div>
                <div className="price">{amount}{per && <small>/{per}</small>}</div>
                <ul>{p.features.map(f => <li key={f}>{f}</li>)}</ul>
                {t === 'free'
                  ? <Link className="btn" href="/login">Create free account</Link>
                  : <button className="btn secondary" disabled aria-disabled="true">Opens after the beta</button>}
              </article>
            );
          })}
        </div>
      )}
      <p className="muted small" style={{ textAlign: 'center', marginTop: 30 }}>
        A membership buys analytics, not picks that win. Past results do not predict future results. 21+ only.
      </p>
    </main>
  );
}
