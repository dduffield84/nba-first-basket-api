import './style.css';
import Link from 'next/link';
import { ShieldCheck } from 'lucide-react';

export const metadata = {
  title: 'Sanchez Sports Lab | Where Data Meets the Game.',
  description: 'Sanchez Sports Lab: independent NBA, NHL, NFL, college football and college basketball analytics with a public, paper-only model record.',
};

export const viewport = { width: 'device-width', initialScale: 1, themeColor: '#0a0a0a' };

export default function Root({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="nav">
          <Link className="brand" href="/">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img className="brandlogo" src="/logo.png" alt="" width={52} height={52} />
            <span>SANCHEZ <b>SPORTS LAB</b><small>WHERE DATA MEETS THE GAME.</small></span></Link>
          <nav>
            <Link href="/dashboard">Lab Board</Link>
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
          <span>© {new Date().getFullYear()} SANCHEZ SPORTS LAB</span>
          <span>Analytics only. Paper record, no real wagers. No guaranteed outcomes. 21+.</span>
          <span><ShieldCheck size={15} /> Gambling problem? Call 1-800-GAMBLER · <Link href="/responsible">Responsible use</Link></span>
        </footer>
      </body>
    </html>
  );
}
