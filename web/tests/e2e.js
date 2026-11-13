const { chromium, devices } = require('playwright');
const OUT = process.env.SHOTS || '/tmp/';
const EDGE = process.env.EDGE || 'http://127.0.0.1:8765';
const KEY = process.env.EDGE_WRITE_KEY;
if (!KEY) { console.error('Set EDGE_WRITE_KEY to the local test server key.'); process.exit(2); }
const EMAIL = 'webtest-' + Date.now() + '@example.com';
const SITE = process.env.SITE || 'http://localhost:3000';
const results = [];
function check(name, ok, info = '') { results.push([ok ? 'PASS' : 'FAIL', name, info]); }
async function setTier(email, tier) {
  const r = await fetch(EDGE + '/admin/users/tier', { method: 'POST',
    headers: { 'X-API-Key': KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, tier }) });
  return r.status;
}
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ ...devices['iPhone 13'] });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push(String(e)));

  await page.goto(SITE + '/');
  await page.screenshot({ path: OUT + '01_home.png', fullPage: true });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  check('home has no sideways scroll at iPhone width', !overflow);
  check('home shows no made-up numbers', !(await page.content()).includes('61.2%'));

  await page.goto(SITE + '/dashboard');
  check('signed-out dashboard asks to sign in', await page.getByText('Sign in to see the board').isVisible());

  await page.goto(SITE + '/pricing');
  await page.screenshot({ path: OUT + '02_pricing.png', fullPage: true });
  check('pricing reads prices from Edge', (await page.content()).includes('$14.99'));
  check('paid plans cannot be bought', await page.getByRole('button', { name: 'Opens after the beta' }).first().isDisabled());

  // sign up
  await page.goto(SITE + '/login');
  await page.fill('input[type=email]', EMAIL);
  await page.fill('input[type=password]', 'correct horse battery');
  await page.click('button.btn');
  check('sign-up blocked without the 21+ box', page.url().endsWith('/login'));
  await page.check('text=I am 21 or older.');
  await page.check('text=I accept the terms');
  await page.screenshot({ path: OUT + '03_signup.png', fullPage: true });
  await page.click('button.btn');
  await page.waitForURL('**/dashboard', { timeout: 15000 });
  await page.waitForSelector('table.table');
  const rows = await page.locator('table.table tbody tr').count();
  check('free member sees 3 games', rows === 3, rows + ' rows');
  check('free member sees PRO locks', await page.locator('td >> text=PRO').count() >= 3);
  check('free member sees hidden-games notice', await page.getByText('2 more games').isVisible());
  await page.screenshot({ path: OUT + '04_board_free.png', fullPage: true });

  const cookies = await ctx.cookies();
  const c = cookies.find(x => x.name === 'edge_session');
  check('session cookie is httpOnly', !!c && c.httpOnly);
  const ls = await page.evaluate(() => JSON.stringify(localStorage) + document.cookie);
  check('page scripts cannot read the token', !ls.includes(c?.value ?? 'x'));

  await page.goto(SITE + '/dashboard?evidence=unverified');
  const rec = await page.locator('#record').innerText();
  check('record shows 6 settled (unverified view)', rec.includes('6\nSETTLED') || /\b6\b/.test(rec), rec.split('\n').slice(0, 8).join(' | '));
  check('CLV locked for free', rec.includes('ELITE'));

  check('upgrade to pro via admin', (await setTier(EMAIL, 'pro')) === 200);
  await page.goto(SITE + '/dashboard');
  const prow = await page.locator('table.table tbody tr').count();
  check('pro sees all 5 games', prow === 5, prow + ' rows');
  check('pro sees fair odds', (await page.locator('td >> text=PRO').count()) === 0);
  await page.screenshot({ path: OUT + '05_board_pro.png', fullPage: true });

  await setTier(EMAIL, 'elite');
  await page.goto(SITE + '/dashboard?evidence=unverified');
  check('elite sees CLV tile', (await page.locator('#record').innerText()).includes('BEAT THE CLOSE'));

  // cross-site post refused
  const x = await fetch(SITE + '/api/session', { method: 'POST',
    headers: { Origin: 'https://evil.example', 'Content-Type': 'application/json' },
    body: JSON.stringify({ token: 'a'.repeat(40) }) });
  check('cross-site session post refused', x.status === 403, String(x.status));
  const p = await fetch(SITE + '/api/pause', { method: 'POST',
    headers: { Origin: 'https://evil.example', 'Content-Type': 'application/json' },
    body: JSON.stringify({ days: 30 }) });
  check('cross-site pause refused', p.status === 403, String(p.status));
  const fake = await fetch(SITE + '/api/session', { method: 'POST',
    headers: { Origin: SITE, Host: 'localhost:3000', 'Content-Type': 'application/json' },
    body: JSON.stringify({ token: 'b'.repeat(43) }) });
  check('made-up token refused', fake.status === 401, String(fake.status));

  // take a break
  await page.goto(SITE + '/dashboard');
  await page.selectOption('select', '7');
  await page.click('text=Take a break');
  await page.click('text=Yes, pause my access');
  await page.waitForSelector('text=Access paused');
  check('pause shows the paused screen with helpline', (await page.content()).includes('1-800-GAMBLER'));
  await page.screenshot({ path: OUT + '06_paused.png', fullPage: true });

  // sign out revokes the session at Edge
  const token = c.value;
  await page.click('text=Sign out');
  await page.waitForURL(SITE + '/');
  const me = await fetch(EDGE + '/auth/me', { headers: { Authorization: 'Bearer ' + token } });
  check('sign-out revokes the session at Edge', me.status === 401, String(me.status));

  // wrong password
  await page.goto(SITE + '/login?mode=login');
  await page.fill('input[type=email]', EMAIL);
  await page.fill('input[type=password]', 'wrong password!!');
  await page.click('button.btn');
  await page.waitForSelector('p.error');
  check('wrong password message', (await page.locator('p.error').innerText()).includes('not right'));

  const real = errors.filter(e => !e.includes('status of 401'));
  check('no browser console errors (apart from the expected wrong-password 401)', real.length === 0, real.join(' / '));
  await browser.close();
  for (const r of results) console.log(r.join('  '));
  console.log(results.filter(r => r[0] === 'PASS').length + '/' + results.length);
})().catch(e => { console.error(e); process.exit(1); });
