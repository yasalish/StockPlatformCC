/**
 * landing_shots.mjs — photograph the real pages for the landing-page carousel.
 *
 * shoot.mjs takes one screenshot per invocation, which means one Chrome launch
 * per screenshot: for the five shots this page needs that is five cold starts
 * and five fresh caches. This drives all of them in a single session instead,
 * and — unlike shoot.mjs — waits for each page to actually have its DATA before
 * the shutter, which is the whole point when the subject is a market table that
 * fetches after load.
 *
 *   node landing_shots.mjs <baseUrl> <outDir> [width] [height]
 *
 * The caller is responsible for pointing baseUrl at an instance that does not
 * demand a login (landing_shots.py boots one). Writes <outDir>/<name>.png; the
 * Python side converts to WebP, because Pillow is already a dependency and
 * asking npm for an image encoder is not worth a new package.
 */
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

const [, , baseUrl, outDir, wArg, hArg] = process.argv;
if (!baseUrl || !outDir) {
  console.error("usage: node landing_shots.mjs <baseUrl> <outDir> [w] [h]");
  process.exit(2);
}
const width = Number(wArg) || 1600;
const height = Number(hArg) || 900;

/* Each entry: the route, the output name, and how to know the page is READY.
   `ready` is evaluated in the page until it returns true — a fixed sleep either
   photographs a spinner or wastes seconds, and on a market table the difference
   between the two is one network round trip. */
const SHOTS = [
  {
    path: "/dashboard",
    name: "dashboard",
    ready: () =>
      document.querySelectorAll(".stat-num, .mini-list li, .mini-list a").length > 3 &&
      !document.querySelector(".dash-loading"),
  },
  {
    path: "/performance",
    name: "performance",
    ready: () => {
      const t = document.querySelector("table");
      return !!(t && t.tBodies[0] && t.tBodies[0].rows.length > 5);
    },
  },
  {
    path: "/heatmap",
    name: "heatmap",
    // heatmap.js builds ~780 tiles as one HTML string and assigns it to
    // #hm-root in a single write, so a populated `.t-sym` count is the signal —
    // there is no canvas (the first guess here) and no `.tile` class (the
    // second): the tile's symbol label is `.t-sym`. Both wrong guesses cost the
    // full 45 s timeout on every run before falling through to the shutter.
    ready: () => document.querySelectorAll("#hm-root .t-sym").length > 20,
  },
  {
    path: "/filter-designer",
    name: "designer",
    // The Vue island renders the palette; before that the page is an empty div.
    ready: () => document.querySelectorAll("[class*='node'], [class*='palette'] *").length > 4,
  },
  {
    path: "/moneyflow",
    name: "moneyflow",
    ready: () => {
      const t = document.querySelector("table");
      return (
        !!(t && t.tBodies[0] && t.tBodies[0].rows.length > 3) ||
        document.querySelectorAll(".flow-bar-row").length > 2
      );
    },
  },
];

function launch() {
  // --no-proxy-server: this machine runs a local proxy client that intercepts
  // 127.0.0.1 and answers 503, so the browser has to be told to go direct.
  const opts = { headless: true, args: ["--no-proxy-server", "--disable-dev-shm-usage"] };
  for (const channel of ["chrome", "msedge"]) {
    try {
      return chromium.launch({ ...opts, channel });
    } catch {
      /* try the next one */
    }
  }
  return chromium.launch(opts);
}

mkdirSync(outDir, { recursive: true });

const browser = await launch();
const ctx = await browser.newContext({
  viewport: { width, height },
  locale: "fa-IR",
  deviceScaleFactor: 1,
});
const page = await ctx.newPage();

// A landing-page screenshot should not be photographed mid-transition, and this
// app has a «کاهش حرکت» preference that turns every one of them off. Setting it
// before the first paint is cheaper and more reliable than sleeping past them.
await page.addInitScript(() => {
  try {
    localStorage.setItem(
      "boursenegar-prefs",
      JSON.stringify({ reduce_motion: true, theme: "light", density: "comfortable" }),
    );
    localStorage.setItem("boursenegar-theme", "light");
  } catch {
    /* private mode — the defaults are fine */
  }
});

const results = [];
for (const shot of SHOTS) {
  const t0 = Date.now();
  let status = "ok";
  try {
    const resp = await page.goto(baseUrl + shot.path, {
      waitUntil: "networkidle",
      timeout: 60000,
    });
    if (resp && resp.status() >= 400) status = "http" + resp.status();
    await page.waitForFunction(shot.ready, null, { timeout: 45000 });
    // One extra frame so web fonts and the final chart paint have landed. The
    // readiness check above proves the DATA arrived, not that it is drawn.
    await page.waitForTimeout(900);
  } catch (e) {
    status = "not-ready";
  }
  const file = `${outDir}/${shot.name}.png`;
  await page.screenshot({ path: file, fullPage: false });
  results.push({ name: shot.name, path: shot.path, status, ms: Date.now() - t0, file });
  console.error(`  ${status === "ok" ? "OK  " : status.padEnd(4)} ${shot.path} -> ${shot.name}.png (${Date.now() - t0}ms)`);
}

await browser.close();
console.log(JSON.stringify({ width, height, shots: results }, null, 2));
