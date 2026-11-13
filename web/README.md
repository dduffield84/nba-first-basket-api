# Edge Intelligence website

The public website and member dashboard for Edge Intelligence, built with Next.js. It began as a starter with its own Supabase sign-in, Stripe checkout and sample predictions. In this version the Edge API is the only backend:

| Starter had | Now |
|---|---|
| Supabase email sign-in and a `subscriptions` table | Edge accounts (`/auth/signup`, `/auth/login`, `/auth/me`, `/auth/logout`): 21+ and terms at sign-up, hashed passwords, rate limits |
| Tier read from Supabase | Tier read from Edge `/auth/me`; Edge itself hides Pro and Elite fields |
| `/api/predictions` calling a `/public/predictions` endpoint that does not exist | The dashboard reads Edge `/ledger/board` and `/ledger/performance` with the member's own session |
| Prices hard-coded at $29.99 and $79.99 | Names, prices and features read from Edge `/auth/tiers` |
| Stripe checkout, webhook and billing portal | Removed. Paid plans show "Opens after the beta"; tiers are set by staff with `/admin/users/tier` |
| Sample "edge" figures on the home page | Removed. No numbers are shown that are not from the ledger |

Everything shown is the paper-only ledger: model predictions and paper bets, never real wagers.

## How sign-in works

1. The sign-in form posts straight to the Edge API from the browser. That way Edge's per-address sign-in limits see each member's own address, not the website server's.
2. The browser hands the session token to this site's `/api/session`. The site checks it with Edge and stores it in an httpOnly, SameSite=Lax cookie, so page scripts never keep it.
3. Server pages call Edge with `Authorization: Bearer <token>`. Sign-out revokes the session at Edge.
4. The site's own POST routes refuse requests from other sites (Origin check).

"Take a break" calls Edge `/auth/pause`. A pause can be extended but never shortened, and a paused member sees only the paused screen with 1-800-GAMBLER.

## Settings

Copy `.env.example` to `.env.local`:

```
EDGE_API_URL=https://YOUR-EDGE-SERVICE.onrender.com
NEXT_PUBLIC_EDGE_API_URL=https://YOUR-EDGE-SERVICE.onrender.com
```

On the Edge API service, set `EDGE_APP_ORIGINS` to this website's address (for example `https://edgeintelligence.example`) so the browser may call `/auth/*`. There are no keys in this site.

## Run on Windows

1. Install Node.js 20 or newer.
2. In this `web` folder: `npm install`, then `npm run dev`.
3. Open http://localhost:3000.

## Tests

`tests/e2e.js` drives the site on an iPhone-sized browser against a local Edge API. It signs up, checks Free, Pro and Elite views, the cookie, cross-site refusals, the pause and sign-out. It needs a local Edge server started with a throwaway write key (never the live key):

```
# Edge API repo, local SQLite
DB_PATH=data/web_e2e.db NBA_API_KEY=<throwaway> EDGE_APP_ORIGINS=http://localhost:3000 uvicorn main:app --port 8765
EDGE_WRITE_KEY=<throwaway> python tests/seed_local_edge.py
# this folder
NEXT_PUBLIC_EDGE_API_URL=http://127.0.0.1:8765 npm run build
EDGE_API_URL=http://127.0.0.1:8765 npm start
EDGE_WRITE_KEY=<throwaway> node tests/e2e.js
```

## Not done yet

- Payments. Adding Stripe later means a server-only webhook that calls Edge `/admin/users/tier` with the write key; Edge stays the source of truth for tiers.
- Password reset, account deletion, terms and privacy pages, and a legal review.
- Hosting. The site has not been deployed anywhere.
