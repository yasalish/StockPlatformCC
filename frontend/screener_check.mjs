/**
 * screener_check.mjs — browser-side verification of the «غربالگر هوشمند» island.
 *
 *   node screener_check.mjs <baseUrl> <sessionCookie>
 *
 * Prints one JSON object on stdout; verify_scan_islands.py turns it into checks.
 */
import { chromium } from "playwright-core";

const [, , baseUrl, cookieValue] = process.argv;

function launch() {
  const opts = { headless: true, args: ["--no-proxy-server", "--disable-dev-shm-usage"] };
  for (const channel of ["chrome", "msedge"]) {
    try {
      return chromium.launch({ ...opts, channel });
    } catch {
      /* next */
    }
  }
  return chromium.launch(opts);
}

const out = { errors: [] };
const browser = await launch();
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
if (cookieValue) {
  const u = new URL(baseUrl);
  await ctx.addCookies([{ name: "session", value: cookieValue, domain: u.hostname, path: "/" }]);
}
const page = await ctx.newPage();
page.on("pageerror", (e) => out.errors.push(String(e)));

const t0 = Date.now();
await page.goto(`${baseUrl}/screener`, { waitUntil: "load", timeout: 120000 });
await page.waitForSelector("#screener-app tbody tr[data-ticker]", { timeout: 60000 });
out.interactiveMs = Date.now() - t0;

Object.assign(out, await page.evaluate(() => {
  const n = performance.getEntriesByType("navigation")[0];
  return {
    htmlBytes: n.transferSize,
    nodes: document.getElementsByTagName("*").length,
    renderedRows: document.querySelectorAll("#screener-app tbody tr[data-ticker]").length,
    cards: document.querySelectorAll("#screener-app .stat-card").length,
    badges: document.querySelectorAll("#screener-app .score-badge").length,
    vbadges: document.querySelectorAll("#screener-app .vbadge").length,
    bars: document.querySelectorAll("#screener-app .mini-bar").length,
    firstRank: document.querySelector("#screener-app tbody tr[data-ticker] td")?.textContent?.trim(),
    stars: document.querySelectorAll("#screener-app .watch-star").length,
  };
}));

/** Click a header, wait for Vue to patch, read the sorted column. */
async function sortByScore() {
  await page.evaluate(() => document.querySelectorAll("#screener-app thead th")[5].click());
  await page.waitForTimeout(200);
  return page.evaluate(() =>
    Array.from(document.querySelectorAll("#screener-app tbody tr[data-ticker]"))
      .slice(0, 6).map((r) => parseFloat(r.cells[5].dataset.v || "0")));
}
out.scoreAsc = await sortByScore();
out.scoreDesc = await sortByScore();

// the ranked default order: highest score first, «#» counting from 1
await page.reload({ waitUntil: "load" });
await page.waitForSelector("#screener-app tbody tr[data-ticker]", { timeout: 60000 });
Object.assign(out, await page.evaluate(() => {
  const rows = Array.from(document.querySelectorAll("#screener-app tbody tr[data-ticker]"));
  return {
    defaultScores: rows.slice(0, 6).map((r) => parseFloat(r.cells[5].dataset.v || "0")),
    ranks: rows.slice(0, 3).map((r) => r.cells[0].textContent.trim()),
  };
}));

// the symbol filter
await page.fill("#screenfilter", "فولاد");
await page.waitForTimeout(250);
out.filtered = await page.evaluate(() =>
  Array.from(document.querySelectorAll("#screener-app tbody tr[data-ticker]")).map((r) => r.dataset.ticker));
await page.fill("#screenfilter", "");
await page.waitForTimeout(200);

// the verdict band, without a page reload
let docs = 0;
page.on("request", (r) => { if (r.resourceType() === "document") docs++; });
const t1 = Date.now();
await page.selectOption("#screener-app select >> nth=-1", { index: 1 });
await page.waitForFunction(
  () => !document.querySelector("#screener-app .filterbar .muted.small"), null, { timeout: 30000 });
out.verdictMs = Date.now() - t1;
out.documentLoadsOnVerdict = docs;
Object.assign(out, await page.evaluate(() => ({
  verdictUrl: decodeURIComponent(location.search),
  verdictLabels: Array.from(document.querySelectorAll("#screener-app .vbadge"))
    .slice(0, 5).map((n) => n.textContent.trim()),
})));

// deep scroll keeps the DOM small
await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight * 0.6));
await page.waitForTimeout(600);
Object.assign(out, await page.evaluate(() => {
  const rows = Array.from(document.querySelectorAll("#screener-app tbody tr[data-ticker]"));
  return { deepFirstIndex: Number(rows[0]?.dataset.index ?? -1),
           deepNodes: document.getElementsByTagName("*").length };
}));

await browser.close();
process.stdout.write(JSON.stringify(out));
