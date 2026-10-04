// Optional: precise trigger for the GitHub Actions scanner using a FREE Cloudflare Worker.
// (Alternative to cron-job.org - use one of them, not both.)
//
// 1. dash.cloudflare.com > Workers & Pages > Create > "Hello World" worker, paste this file, Deploy.
// 2. Settings > Variables and Secrets:
//      GH_TOKEN  (secret)  fine-grained GitHub token, this repo only, permission "Actions: Read and write"
//      GH_REPO   (text)    yourname/index-options-scanner
// 3. Settings > Triggers > Cron Triggers (times are UTC):
//      30 2 * * 1-5     -> 08:00 IST session A
//      25 8 * * 1-5     -> 13:55 IST session B
export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },
  async fetch(req, env) {           // lets you test by opening the worker URL with ?key=<GH_TOKEN last 6 chars>
    const key = new URL(req.url).searchParams.get("key");
    if (!key || !env.GH_TOKEN.endsWith(key)) return new Response("forbidden", { status: 403 });
    const r = await dispatch(env);
    return new Response(`dispatch -> ${r.status}`);
  },
};

async function dispatch(env) {
  return fetch(`https://api.github.com/repos/${env.GH_REPO}/actions/workflows/scanner.yml/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "scanner-dispatcher",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({ ref: "main", inputs: { command: "auto" } }),
  });
}
