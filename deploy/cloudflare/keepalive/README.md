# Cloudflare keepalive Worker

A scheduled Worker that fetches the demo service's `/readyz` endpoint every
10 minutes. This is what keeps the free split platform from going to sleep:

- **Render Free** sleeps an instance after 15 minutes without a request
  (waking takes about a minute). A request each 10 minutes resets that timer.
- **Supabase Free** pauses a project after a week of inactivity. `/readyz`
  opens the Postgres and Redis connections, so the project stays active.

`/readyz` checks the database, Redis and the required PostgreSQL extensions,
so a `200` here means the demo is fully reachable, not merely awake.

## Files

- `src/index.js` - the scheduled handler (fetches `KEEPALIVE_URL`, logs any
  non-2xx response).
- `wrangler.toml` - the cron trigger (`*/10 * * * *`) and the `KEEPALIVE_URL`
  variable (a placeholder until you set the real hostname).

## Deploy

1. Install the Wrangler CLI (needs a Cloudflare account; everything here fits
   in the free Workers plan, no payment card is required):
   ```powershell
   npm install --location=global wrangler
   wrangler login
   ```
2. Edit `wrangler.toml` and replace
   `https://<service>.onrender.com/readyz` with the public hostname of the
   deployed Render web service.
3. Deploy from this directory:
   ```powershell
   wrangler deploy
   ```
4. Verify: the Cloudflare dashboard shows the `enamel-keepalive` Worker with
   an every-10-minutes cron trigger, and the logs show
   `answered HTTP 200` after the next trigger fires.

## Notes

- The fetch carries an `x-keepalive: 1` header so the request is always
  identifiable as a keepalive probe in the access logs.
- The keepalive counts against the Render Free allotment of 750 instance
  hours per month (about 720 hours of a month are covered), and it uses a
  fraction of the free Workers limit (100 K requests/day).
- There are no account identifiers in this directory; the deployed account
  belongs to whoever runs `wrangler deploy`.
- Disabling it (Workers dashboard -> pause the trigger) is part of the
  documented rollback path in `docs/deployment/DEPLOYMENT_GUIDE.md`.
