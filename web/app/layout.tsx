import './style.css';
import Link from 'next/link';
import { ShieldCheck } from 'lucide-react';

export const metadata = {
  title: 'Edge Intelligence AI | Sports Analytics',
  description: 'Independent NBA, NFL and college football analytics with a public, paper-only model record.',
};

export const viewport = { width: 'device-width', initialScale: 1, themeColor: '#070b10' };

export default function Root({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="nav">
          <Link className="brand" href="/"><span className="brandmark">E</span>
            <span>EDGE <b>INTELLIGENCE</b><small>AI SPORTS ANALYTICS</small></span></Link>
          <nav>
            <Link href="/dashboard">Edge Board</Link>
            <Link href="/dashboard#record">Record</Link>
            <Link href="/pricing">Membership</Link>
          </nav>
          <div className="navright">
            <Link className="quiet" href="/login?mode=login">Log in</Link>
            <Link className="btn small" href="/login">Start free</Link>
          </div>
        </header>
        <div className="mobilenav">
          <Link href="/dashboard">Board</Link>
          <Link href="/dashboard#record">Record</Link>
          <Link href="/pricing">Membership</Link>
          <Link href="/responsible">Responsible use</Link>
        </div>
        {children}
        <footer>
          <span>© {new Date().getFullYear()} EDGE INTELLIGENCE AI</span>
          <span>Analytics only. Paper record, no real wagers. No guaranteed outcomes. 21+.</span>
          <span><ShieldCheck size={15} /> Gambling problem? Call 1-800-GAMBLER · <Link href="/responsible">Responsible use</Link></span>
        </footer>
      </body>
    </html>
  );
}
