import Link from 'next/link';

export default function Responsible() {
  return (
    <main className="prose">
      <div className="eyebrow">RESPONSIBLE USE</div>
      <h1>Analytics, not a sportsbook</h1>
      <p>Edge Intelligence publishes model probabilities and keeps a paper record of how they did. It does not take bets, hold money or place wagers for anyone, and no result is guaranteed.</p>
      <p>Edge is for people 21 and older. Sports betting is not legal everywhere; check the law where you live.</p>
      <h2>Take a break</h2>
      <p>Signed-in members can pause their own access for a day up to a year from the <Link className="green" href="/dashboard#account">dashboard</Link>. A pause can be extended but not shortened.</p>
      <h2>Get help</h2>
      <p>If gambling is causing problems for you or someone you know, call <a className="green" href="tel:18004264237">1-800-GAMBLER</a> (1-800-426-4237), any time, free and confidential.</p>
    </main>
  );
}
