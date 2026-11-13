import Link from 'next/link';
import { currentUser, edgeGet } from '../../lib/edge';
import { american, ev, pct, when } from '../../lib/format';
import { SignOut, TakeABreak } from '../../components/AccountActions';

export const dynamic = 'force-dynamic';

type Item = {
  entry_key: string; sport: string; market: string; event_label: string;
  event_start_utc: string | null; selection: string; line: number | null;
  model_prob: number | null; market_prob: number | null;
  odds_american: number | null; fair_odds: number | null;
  expected_value: number | null; decision: string; status: string;
  outcome: string | null; model_version: string | null;
};
type Board = {
  date: string; items: Item[];
  access: { tier: string; hidden_games: number; locked: string[] };
};
type Summary = {
  entries: number; settled: number; awaiting_result: number;
  probability?: { n: number; hit_rate: number; mean_model_prob: number; brier: number };
  betting?: { wins: number; losses: number; units: number; roi: number | null };
  clv?: { mean_price_clv: number | null; beat_close_rate: number | null } | null;
};
type Perf = {
  evidence: string; evidence_note: string;
  performance: Record<string, { all: Summary }>;
  access: { tier: string; locked: string[] };
};

// sport keys as the Edge ledger records them; cbb = college basketball (men's and women's D1)
const SPORTS = ['', 'nba', 'nhl', 'nfl', 'cfb', 'cbb'];

// Some importers (college basketball) already put the line in the selection ("Akron -12"); don't repeat it.
function pick(selection: string, line: number | null): string {
  if (line === null) return selection;
  const tail = selection.trim().split(' ').pop()?.replace(/^\+/, '');
  return tail !== undefined && Number(tail) === line ? selection : selection + ' ' + line;
}

function todayET(): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' });
}

export default async function Dashboard({ searchParams }: {
  searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? '';
  const date = /^\d{4}-\d{2}-\d{2}$/.test(one(sp.date)) ? one(sp.date) : todayET();
  const sport = SPORTS.includes(one(sp.sport)) ? one(sp.sport) : '';
  const evidence = one(sp.evidence) === 'unverified' ? 'unverified' : 'verified';

  const { token, user } = await currentUser();
  if (!user) {
    return (
      <main className="dashboard">
        <div className="eyebrow">MEMBER TERMINAL</div>
        <h1>Sign in to see the board</h1>
        <p className="muted">Today&apos;s model picks and the paper record are for members. A free account sees three games a day.</p>
        <div className="row"><Link className="btn" href="/login">Create free account</Link>
          <Link className="btn secondary" href="/login?mode=login">Sign in</Link></div>
      </main>
    );
  }

  const tierName = user.tier_name.toUpperCase();
  const account = (
    <section id="account" className="card" style={{ marginTop: 28 }}>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <div><div className="eyebrow">ACCOUNT</div>
          <p className="muted" style={{ margin: '8px 0' }}>{user.email} · {tierName}</p></div>
        <SignOut />
      </div>
      {!user.paused_until && <TakeABreak />}
    </section>
  );

  if (user.paused_until) {
    return (
      <main className="dashboard">
        <div className="eyebrow">MEMBER TERMINAL</div>
        <h1>Access paused</h1>
        <div className="warn">You paused your access until {user.paused_until.slice(0, 10)}. It can&apos;t be shortened.
          If you need help, call <a href="tel:18004264237">1-800-GAMBLER</a>.</div>
        {account}
      </main>
    );
  }

  const [board, perf] = await Promise.all([
    edgeGet<Board>('/ledger/board', token, { date, sport: sport || undefined }),
    edgeGet<Perf>('/ledger/performance', token, { sport: sport || undefined, evidence }),
  ]);
  const pro = user.tier !== 'free';
  const q = (o: Record<string, string>) => '?' + new URLSearchParams(
    Object.entries({ date, sport, evidence, ...o }).filter(([, v]) => v)).toString();
  const shift = (d: number) => {
    const t = new Date(date + 'T12:00:00Z');
    t.setUTCDate(t.getUTCDate() + d);
    return t.toISOString().slice(0, 10);
  };

  return (
    <main className="dashboard">
      <div className="eyebrow">MEMBER TERMINAL · {tierName}</div>
      <h1>Edge <span className="green">Board</span></h1>
      <div className="notice">Paper record only. These are model predictions and paper bets, never real wagers. Nothing here is a guarantee.</div>

      <div className="row" style={{ margin: '18px 0' }}>
        {SPORTS.map(s => <Link key={s || 'all'} className={'pill' + (s === sport ? ' on' : '')}
          href={q({ sport: s })}>{s ? s.toUpperCase() : 'All'}</Link>)}
        <span style={{ flex: 1 }} />
        <Link className="pill" href={q({ date: shift(-1) })}>◀</Link>
        <span className="pill on">{date}</span>
        <Link className="pill" href={q({ date: shift(1) })}>▶</Link>
      </div>

      {!board.ok ? <div className="warn">The board could not be loaded: {board.detail}</div> : <>
        {board.data.items.length === 0
          ? <p className="muted">No model picks recorded for {date}{sport ? ' in ' + sport.toUpperCase() : ''}.</p>
          : <div className="tablewrap"><table className="table">
            <thead><tr>
              <th>GAME</th><th>PICK</th><th className="num">MODEL</th>
              <th className="num">FAIR</th><th className="num">PRICE</th><th className="num">EV</th>
              <th>CALL</th><th>STATUS</th>
            </tr></thead>
            <tbody>{board.data.items.map(i => (
              <tr key={i.entry_key}>
                <td>{i.event_label}<div className="locked">{i.sport.toUpperCase()} · {i.market} · {when(i.event_start_utc)}</div></td>
                <td>{pick(i.selection, i.line)}</td>
                <td className="num">{pct(i.model_prob)}</td>
                <td className="num">{pro ? american(i.fair_odds) : <span className="locked">PRO</span>}</td>
                <td className="num">{american(i.odds_american)}</td>
                <td className="num">{pro ? ev(i.expected_value) : <span className="locked">PRO</span>}</td>
                <td className={i.decision === 'BET' ? 'bet' : ''}>{i.decision}</td>
                <td>{i.outcome ? i.outcome.toUpperCase() : i.status}</td>
              </tr>))}
            </tbody></table></div>}
        {board.data.access.hidden_games > 0 &&
          <div className="notice">{board.data.access.hidden_games} more game{board.data.access.hidden_games === 1 ? '' : 's'} on this day {board.data.access.hidden_games === 1 ? 'is' : 'are'} in Pro, with fair odds and value for every pick. <Link className="green" href="/pricing">See plans</Link></div>}
      </>}

      <section id="record" style={{ marginTop: 40 }}>
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h2 style={{ margin: 0 }}>Record</h2>
          <div className="row">
            <Link className={'pill' + (evidence === 'verified' ? ' on' : '')} href={q({ evidence: 'verified' })}>Verified</Link>
            <Link className={'pill' + (evidence === 'unverified' ? ' on' : '')} href={q({ evidence: 'unverified' })}>Not verified</Link>
          </div>
        </div>
        {!perf.ok ? <div className="warn">The record could not be loaded: {perf.detail}</div> : <>
          <p className="muted small">{perf.data.evidence_note}</p>
          <Record perf={perf.data} />
        </>}
      </section>
      {account}
    </main>
  );
}

function Record({ perf }: { perf: Perf }) {
  const p = perf.performance.prediction?.all;
  const paper = perf.performance.paper_bet?.all;
  const clvLocked = perf.access.locked.includes('clv');
  if (!p) return <p className="muted">Nothing counted yet in this view.</p>;
  return <>
    <div className="statgrid">
      <div className="stat"><strong>{p.settled}</strong><small>SETTLED PICKS</small></div>
      <div className="stat"><strong className="green">{pct(p.probability?.hit_rate)}</strong><small>HIT RATE</small></div>
      <div className="stat"><strong>{pct(p.probability?.mean_model_prob)}</strong><small>AVG MODEL PROB.</small></div>
      <div className="stat"><strong>{p.probability ? p.probability.brier.toFixed(3) : '–'}</strong><small>BRIER (LOWER IS BETTER)</small></div>
    </div>
    <div className="statgrid">
      <div className="stat"><strong>{p.awaiting_result}</strong><small>AWAITING RESULT</small></div>
      <div className="stat"><strong>{paper?.betting ? `${paper.betting.wins}-${paper.betting.losses}` : '–'}</strong><small>PAPER BETS W-L</small></div>
      <div className="stat"><strong>{paper?.betting ? (paper.betting.units >= 0 ? '+' : '') + paper.betting.units.toFixed(2) + 'u' : '–'}</strong><small>PAPER UNITS</small></div>
      <div className="stat">{clvLocked
        ? <><strong className="locked">ELITE</strong><small>CLOSING LINE VALUE</small></>
        : <><strong>{pct(p.clv?.beat_close_rate)}</strong><small>BEAT THE CLOSE</small></>}</div>
    </div>
  </>;
}
