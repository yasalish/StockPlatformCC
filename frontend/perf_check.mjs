/**
 * perf_check.mjs — browser-side verification of the «بازدهٔ دوره‌ای» island.
 *
 *   node perf_check.mjs <baseUrl> <sessionCookie>
 *
 * Prints one JSON object on stdout; verify_perf_island.py turns it into checks.
 * Runs against the system Chrome/Edge through playwright-core, so nothing is
 * downloaded — the same arrangement capture.mjs uses.
 */
import { chromium } from "playwright-core";

const [, , baseUrl, cookieValue] = process.argv;

function launch() {
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

const out = { errors: [] };
const browser = await launch();
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
if (cookieValue) {
  const url = new URL(baseUrl);
  await ctx.addCookies([{ name: "session", value: cookieValue, domain: url.hostname, path: "/" }]);
}
const page = await ctx.newPage();
page.on("pageerror", (e) => out.errors.push(String(e)));

const t0 = Date.now();
await page.goto(`${baseUrl}/performance`, { waitUntil: "load", timeout: 120000 });
await page.waitForSelector("#perf-app tbody tr[data-ticker]", { timeout: 60000 });
out.interactiveMs = Date.now() - t0;

Object.assign(out, await page.evaluate(() => {
  const n = performance.getEntriesByType("navigation")[0];
  return {
    htmlBytes: n.transferSize,
    ttfbMs: Math.round(n.responseStart - n.requestStart),
    nodes: document.getElementsByTagName("*").length,
    renderedRows: document.querySelectorAll("#perf-app tbody tr[data-ticker]").length,
    sortableHeaders: document.querySelectorAll("#perf-app thead th[data-sort]").length,
    topCards: document.querySelectorAll("#perf-app .perf-tops .stat-card").length,
    stickyFirst: getComputedStyle(
      document.querySelector("#perf-app tbody tr[data-ticker] td")).position === "sticky",
  };
}));

/** Click the nth sortable header and read the sorted column's raw values. */
async function sortAndRead(nth, cellIndex) {
  await page.evaluate((n) => document.querySelectorAll("#perf-app thead th[data-sort]")[n].click(), nth);
  await page.waitForTimeout(150);
  return page.evaluate(
    (ci) => Array.from(document.querySelectorAll("#perf-app tbody tr[data-ticker]"))
      .slice(0, 8)
      .map((r) => parseFloat(r.cells[ci].dataset.v || "0")),
    cellIndex,
  );
}
out.floorAsc = await sortAndRead(4, 4);
out.floorDesc = await sortAndRead(4, 4);
out.priceAsc = await sortAndRead(2, 2);

// the text filter narrows without a round trip
await page.fill("#tablefilter", "فولاد");
await page.waitForTimeout(200);
out.filtered = await page.evaluate(() =>
  Array.from(document.querySelectorAll("#perf-app tbody tr[data-ticker]")).map((r) => r.dataset.ticker));
await page.fill("#tablefilter", "");
await page.waitForTimeout(150);

// a dropdown change must not reload the document
let docs = 0;
page.on("request", (r) => { if (r.resourceType() === "document") docs++; });
const tSel = Date.now();
const groupIndex = await page.evaluate(() => {
  const sel = document.querySelectorAll("#perf-app select")[2];
  return sel && sel.options.length > 3 ? 3 : 1;
});
await page.selectOption("#perf-app select >> nth=2", { index: groupIndex });
await page.waitForFunction(
  () => !document.querySelector("#perf-app .filterbar .muted.small"), null, { timeout: 30000 });
out.filterMs = Date.now() - tSel;
out.documentLoadsOnFilter = docs;
Object.assign(out, await page.evaluate(() => ({
  filterUrl: decodeURIComponent(location.search),
  rowsAfterFilter: document.querySelectorAll("#perf-app tbody tr[data-ticker]").length,
  countLine: document.querySelectorAll("#perf-app .panel-head .muted")[1]?.textContent?.trim() ?? "",
  topAfterFilter: document.querySelector("#perf-app .perf-tops .small.muted")?.textContent?.trim() ?? "",
})));

// Back steps out of the filter, as a page reload used to
await page.goBack({ waitUntil: "commit" });
await page.waitForTimeout(700);
out.urlAfterBack = decodeURIComponent(await page.evaluate(() => location.search));

// scrolling deep into the list keeps rendering rows and nothing else
await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight * 0.6));
await page.waitForTimeout(700);
Object.assign(out, await page.evaluate(() => {
  const rows = Array.from(document.querySelectorAll("#perf-app tbody tr[data-ticker]"));
  return {
    deepRows: rows.length,
    deepFirstIndex: Number(rows[0]?.dataset.index ?? -1),
    deepNodes: document.getElementsByTagName("*").length,
  };
}));

await browser.close();
process.stdout.write(JSON.stringify(out));
