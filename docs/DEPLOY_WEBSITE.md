# Deploy the Edge Intelligence website (Vercel + Render)

The website (`web/`, Next.js) goes on **Vercel**. The API it reads is the
existing **Render** web service `edge-intelligence`
(`https://edge-intelligence.onrender.com`, V1.5.0, deployed from GitLab
`stage2-shared-ledger`). Nothing new is created on Render: the API only needs
to be told the website's address.

Everything the site shows is the paper-only ledger. Payments stay off: Pro
($14.99/mo) and Elite ($29.99/mo) are read from the API's `/auth/tiers` and show
"Opens after the beta". No step below turns on live recommendations, Stripe or
model learning.

## What is in this repository for it

| File | What it does |
|---|---|
| `web/vercel.json` | Next.js on Vercel, `npm ci`, functions in `pdx1` (Portland, next to Render Oregon and the Supabase us-west-2 database), 30 s function limit so a sleeping Render API can wake up |
| `web/next.config.mjs` | Security headers. On Vercel the build **stops** if `EDGE_API_URL` or `NEXT_PUBLIC_EDGE_API_URL` is missing or not `https://`, so the site can never deploy with sign-in silently broken |
| `web/.env.example` | The two settings, filled in with the live API address |
| `.github/workflows/web.yml` | CI on every change to `web/`: install, type check, production build, the missing-address check, and a check that the page security policy allows the Edge API |

## Settings

### Vercel (project settings, Environment Variables, Production and Preview)

| Name | Value |
|---|---|
| `EDGE_API_URL` | `https://edge-intelligence.onrender.com` |
| `NEXT_PUBLIC_EDGE_API_URL` | `https://edge-intelligence.onrender.com` |

No trailing slash. There are no keys or secrets in the website. Do **not** put
`NBA_API_KEY`, `NBA_ADMIN_KEY`, `DATABASE_URL` or any Stripe key on Vercel.
`NEXT_PUBLIC_` values are built into the page, so after changing one, redeploy.

### Render (service `edge-intelligence`, Environment tab)

| Name | Value |
|---|---|
| `EDGE_APP_ORIGINS` | The website's address, for example `https://edge-intelligence-web.vercel.app`. Several are comma-separated (add the custom domain later). Scheme and host only: no path, no trailing slash. |

And under Settings, **Health Check Path**: `/health`.

`EDGE_APP_ORIGINS` lets browsers on that address call the API's sign-in and
sign-up. Any other origin is refused (tested: an unlisted origin gets 400 on the
preflight). Saving an environment variable makes Render redeploy the same
commit; no code or database change comes with it.

## Step by step

Steps 2 and 4 act on the live accounts.

1. **Merge** this pull request. CI (`Website` and `Tests`) must be green.
   Note: merging to `main` also redeploys the older `nba-first-basket-api`
   Render service (v1.3.0, auto-deploy from `main`). That is expected and
   harmless: it builds only the Python app and ignores `web/`.
2. **Create the Vercel project.**
   1. vercel.com, Add New, Project, import `dduffield84/nba-first-basket-api`
      (grant the Vercel GitHub app access to that repository if asked).
   2. **Root Directory: `web`**. Framework preset: Next.js (detected).
      Leave the build and install commands as they are (they come from
      `vercel.json`).
   3. Add the two environment variables above, for Production and Preview.
   4. Deploy. Note the address it gives, for example
      `https://edge-intelligence-web.vercel.app`.
3. **Check the site before allowing sign-in.** Open the address. The home page
   and `/responsible` load; `/pricing` shows Free $0, Pro $14.99, Elite $29.99,
   with paid plans not purchasable. (The first visit may take up to a minute
   while the free Render API wakes up.) Signing in will still fail at this
   point, because the API does not yet know the site's address.
4. **Tell the API about the site.** In Render, service `edge-intelligence`:
   set `EDGE_APP_ORIGINS` to the exact Vercel address from step 2, and set the
   Health Check Path to `/health`. Save; wait for the redeploy to show "Live".
5. **Verify end to end** on a phone:
   - [ ] `https://edge-intelligence.onrender.com/health` says `status: ok`, `version: 1.5.0-dev`
   - [ ] Sign-up refuses without the 21+ box; sign-up with it works
   - [ ] The dashboard shows at most 3 games for a Free member, with Pro locks
   - [ ] The record says paper only, and unverified rows are labelled as such
   - [ ] "Take a break" shows the paused screen with 1-800-GAMBLER
   - [ ] Sign out, then the dashboard asks to sign in again
   - [ ] Pricing: paid plans say they open after the beta
6. **Custom domain (optional).** Vercel, project, Settings, Domains. Then add
   the domain to `EDGE_APP_ORIGINS`, comma-separated after the Vercel address,
   and redeploy nothing else.

## Things to know

- **Previews cannot sign in.** Vercel preview deployments get their own
  addresses, which are not in `EDGE_APP_ORIGINS`. Public pages work there;
  sign-in works only on the production address and listed domains.
- **Render free plan sleeps** after about 15 minutes idle, and the first
  request then takes up to a minute. The site says "may be waking up; try
  again". A paid Render plan removes the wait. That is a billing choice for
  the account owner.
- **Live API version.** Render runs commit `441b9b9` (V1.5.0, deployed
  2026-11-12), which already has the accounts, tiers and CBB ledger the site
  uses. The later audit fixes on `nba-v7` branch `edge-v1.5.0` (PRs 4 and 12)
  are not deployed yet; that is a separate deploy through `edge-deploy.yml`
  with its own backup and approval. The site works with either.
- **Not ready for a public launch:** there are no terms or privacy pages,
  password reset or account deletion yet, and no legal review. Keep the
  address private until those exist.

## Rollback

| Problem | Do this |
|---|---|
| A website deploy is broken | Vercel, Deployments, pick the previous one, Promote to Production (instant, no rebuild) |
| Take the site offline | Vercel, project Settings, Deployment Protection, or remove the domain. The API and ledger are untouched |
| Stop the site's browsers reaching the API | Remove the address from `EDGE_APP_ORIGINS` in Render. Sign-in from the site stops; nothing else changes |
| Health check causes restarts | Clear the Health Check Path in Render |

## Tested before this was opened

- `npm run typecheck` and a production build with the live API address.
- The build refuses a missing or non-https address when `VERCEL=1`.
- The production build's security policy allows `https://edge-intelligence.onrender.com` and denies framing.
- `web/tests/e2e.js` (24 checks: Free/Pro/Elite views, 21+ gate, httpOnly cookie, cross-site refusals, pause, sign-out, no console errors) against a production build of the site and a local copy of the V1.5.0 API from `edge-v1.5.0`: **24/24 passed**.
- CORS on that API: the listed site origin is allowed, an unlisted origin is refused.

Not tested: an actual Vercel or Render deploy (none was made), and the live
API from this environment (its network blocks `onrender.com`).

`npm audit` reports a PostCSS advisory through Next.js 15. PostCSS runs only
at build time on this site's own stylesheet, so it is not reachable by
visitors; the fix needs Next.js 16, a major upgrade left for later.
