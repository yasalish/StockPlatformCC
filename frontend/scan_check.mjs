/**
 * scan_check.mjs — browser-side verification of the /filters and /strategies
 * islands.
 *
 *   node scan_check.mjs <baseUrl> <sessionCookie> <path>
 *
 * Prints one JSON object on stdout; verify_scan_islands.py turns it into checks.
 */
import { chromium } from "playwright-core";

const [, , baseUrl, cookieValue, path] = process.argv;

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
await page.goto(baseUrl + path, { waitUntil: "load", timeout: 120000 });
await page.waitForSelector("#scan-app section.panel", { timeout: 60000 });
out.interactiveMs = Date.now() - t0;

Object.assign(out, await page.evaluate(() => {
  const n = performance.getEntriesByType("navigation")[0];
  return {
    htmlBytes: n.transferSize,
    nodes: document.getElementsByTagName("*").length,
    sections: document.querySelectorAll("#scan-app section.panel").length,
    tablesMounted: document.querySelectorAll("#scan-app table").length,
    cards: document.querySelectorAll("#scan-app .stat-card").length,
    height: Math.round(document.body.scrollHeight),
  };
}));

// walk down the page: sections must materialise as they come into view, and the
// document height must not run away as they do
for (let i = 0; i < 20; i++) {
  await page.evaluate(() => window.scrollBy(0, window.innerHeight * 0.9));
  await page.waitForTimeout(110);
}
await page.waitForTimeout(500);
Object.assign(out, await page.evaluate(() => ({
  nodesAfterScroll: document.getElementsByTagName("*").length,
  tablesAfterScroll: document.querySelectorAll("#scan-app table").length,
  rowsAfterScroll: document.querySelectorAll("#scan-app tbody tr").length,
  heightAfterScroll: Math.round(document.body.scrollHeight),
})));
await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(300);

// Sorting inside a mounted table. The click and the read must be separate
// evaluations: Vue patches the DOM on its own tick, so reading in the same turn
// would see the order from before the click.
async function clickPriceHeader() {
  await page.evaluate(() => {
    const table = document.querySelector("#scan-app table");
    table.tHead.rows[0].cells[3].click();              // قیمت پایانی
  });
  await page.waitForTimeout(200);
  return page.evaluate(() =>
    Array.from(document.querySelector("#scan-app table").tBodies[0].rows)
      .slice(0, 6)
      .map((r) => parseFloat(r.cells[3].dataset.v || "0")));
}
const asc = await clickPriceHeader();
const desc = await clickPriceHeader();
out.sorted = { asc, desc };

// a dropdown change must not reload the page
let docs = 0;
page.on("request", (r) => { if (r.resourceType() === "document") docs++; });
const tSel = Date.now();
await page.selectOption("#scan-app select >> nth=1", { index: 2 });    // گروه
await page.waitForFunction(
  () => !document.querySelector("#scan-app .filterbar .muted.small"), null, { timeout: 30000 });
out.filterMs = Date.now() - tSel;
out.documentLoadsOnFilter = docs;
Object.assign(out, await page.evaluate(() => ({
  filterUrl: decodeURIComponent(location.search),
  sectionsAfterFilter: document.querySelectorAll("#scan-app section.panel").length,
})));

// narrowing to ONE section is a display-only change: no fetch, no reload
const beforeDocs = docs;
await page.selectOption("#scan-app select >> nth=-1", { index: 1 });
await page.waitForTimeout(400);
Object.assign(out, await page.evaluate(() => ({
  sectionsWhenOneSelected: document.querySelectorAll("#scan-app section.panel").length,
  selectUrl: decodeURIComponent(location.search),
})));
out.documentLoadsOnSelect = docs - beforeDocs;

await browser.close();
process.stdout.write(JSON.stringify(out));
