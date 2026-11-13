import Link from 'next/link';
import { ArrowUpRight, BrainCircuit, ChartColumnIncreasing, Clock, LockKeyhole, ShieldCheck, Target } from 'lucide-react';

const features = [
  { icon: BrainCircuit, title: 'Sport-specific models', desc: 'NBA first basket, NHL, NFL, college football and college basketball engines, each recorded under its own model version.' },
  { icon: Target, title: 'Probability first', desc: 'Every pick shows the model probability; members also see fair odds, the market price and expected value.' },
  { icon: Clock, title: 'Timestamped before the game', desc: 'A pick only counts as verified when our server received it before the start, on its own schedule.' },
  { icon: ChartColumnIncreasing, title: 'Losses stay on the record', desc: 'Results are append-only. Hit rate, Brier score and calibration are worked out from every settled pick.' },
  { icon: ShieldCheck, title: 'Paper only', desc: 'The record is model predictions and paper bets. Edge takes no wagers and holds no money.' },
  { icon: LockKeyhole, title: 'Free, Pro and Elite', desc: 'Free shows three games a day. Pro and Elite add value, every game and deeper breakdowns.' },
];

const steps = [
  ['1', 'Model runs before the game', 'Each engine publishes its probability and decision (BET or PASS).'],
  ['2', 'Server stamps it', 'Edge records when it arrived and checks it against the official start time.'],
  ['3', 'Result is graded', 'Wins, losses and closing prices are added; nothing is edited or deleted.'],
];

export default function Home() {
  return <>
    <main className="hero">
      <div>
        <div className="eyebrow">INDEPENDENT SPORTS ANALYTICS</div>
        <h1>See beyond<br />the <em>odds.</em></h1>
        <p className="lead">Model probabilities for the NBA, NHL, NFL, college football and college basketball, set against the market, with a public paper record that keeps every loss.</p>
        <div className="actions">
          <Link href="/login" className="btn">Start for free ↗</Link>
          <Link href="/pricing" className="btn secondary">Explore membership</Link>
        </div>
        <div className="trust">
          <span><ShieldCheck size={16} /> 21+ only</span>
          <span><Clock size={16} /> Server-timestamped picks</span>
          <span><Target size={16} /> Paper record, not wagers</span>
        </div>
      </div>
      <div className="terminal">
        <div className="terminalhead"><span>◈ HOW A PICK COUNTS</span><span className="live">● VERIFIED RECORD</span></div>
        <div className="board">
          {steps.map(s => (
            <div key={s[0]} className="stat" style={{ margin: '12px 0' }}>
              <strong className="green">{s[0]} · {s[1]}</strong>
              <small style={{ fontSize: 13 }}>{s[2]}</small>
            </div>
          ))}
          <p className="muted small">Sign in to see today&apos;s board and the live record. No sample numbers are shown here.</p>
        </div>
      </div>
    </main>
    <section className="section">
      <div className="eyebrow">MORE THAN A PICKS PAGE</div>
      <h2>Research-grade tools.<br /><span className="green">Built for your phone.</span></h2>
      <div className="featuregrid">
        {features.map(f => <article className="card" key={f.title}><f.icon size={26} /><h3>{f.title}</h3><p>{f.desc}</p></article>)}
      </div>
    </section>
    <section className="section">
      <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 25, flexWrap: 'wrap' }}>
        <div>
          <div className="eyebrow">YOUR EDGE STARTS HERE</div>
          <h2>One platform. Every insight.</h2>
          <p className="muted">Create a free account and see three games a day.</p>
        </div>
        <Link className="btn" href="/pricing">Compare plans <ArrowUpRight size={16} style={{ verticalAlign: 'middle' }} /></Link>
      </div>
    </section>
  </>;
}
