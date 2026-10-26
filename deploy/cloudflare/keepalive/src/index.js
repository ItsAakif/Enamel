/**
 * Keepalive cron Worker (free split platform).
 *
 * Render Free sleeps an instance after 15 minutes without a request, and an
 * idle Supabase Free project pauses after a week of inactivity. A request
 * every 10 minutes keeps the demo attributable to real traffic: this Worker
 * fetches `/readyz` (the readiness probe that touches Postgres, Redis and the
 * required extensions) on a cron schedule, which is enough to reset both idle
 * counters.
 *
 * Set the variable `KEEPALIVE_URL` in `wrangler.toml` (a placeholder is
 * filled in) and deploy with `wrangler deploy`. `/readyz` must be reachable;
 * use the public hostname, never a tunnel or localhost.
 *
 * No account identifiers are stored in this file: account IDs, zone IDs and
 * the deployed account belong to the operator, not the repository.
 */

const KEEPALIVE_HEADER = "x-keepalive";

export default {
  async scheduled(event, env, ctx) {
    const url = (env.KEEPALIVE_URL || "").trim();
    if (!url) {
      console.error("keepalive: KEEPALIVE_URL is not set; nothing to fetch");
      return;
    }

    ctx.waitUntil(
      (async () => {
        try {
          const response = await fetch(url, {
            headers: {
              "x-keepalive": "1",
            },
            // Never serve a cached probe.
            cache: "no-store",
          });
          if (!response.ok) {
            console.error(
              `keepalive: ${url} answered HTTP ${response.status}`,
            );
            return;
          }
          console.log(`keepalive: ${url} answered HTTP ${response.status}`);
        } catch (error) {
          console.error(`keepalive: request to ${url} failed: ${String(error)}`);
        }
      })(),
    );
  },
};
