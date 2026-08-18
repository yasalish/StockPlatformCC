/**
 * interact.mjs — exercise the island's behaviour in a real browser.
 *
 * The screenshot comparison proves the page still LOOKS right. This proves it
 * still WORKS: filters without a reload, the URL and Back button, sorting, the
 * text filter, the export link, and the watchlist star.
 *
 *   node interact.mjs <baseUrl> <user> <pass>
 *
 * Prints one JSON object of results for verify_order08.py to assert on.
 */
import { chromium } from "playwright-core";

const [, , baseUrl, user, pass] = process.argv;

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

const browser = await launch();
const page = await (await browser.newContext({ viewport: { width: 1500, height: 1000 }, locale: "fa-IR" })).newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
await page.fill('input[name="username"]', user);
await page.fill('input[name="password"]', pass);
await Promise.all([page.waitForNavigation(), page.click('button[type="submit"], input[type="submit"]')]);

const R = {};

// Count real DOCUMENT requests, so "no round trip" is measured, not assumed.
// framenavigated is the wrong signal here: history.pushState fires it too, and
// pushState is precisely what the island does instead of reloading.
let navigations = 0;
const documentRequests = [];
page.on("request", (r) => {
  if (r.resourceType() === "document") {
    navigations++;
    documentRequests.push(r.url());
  }
});

await page.goto(`${baseUrl}/stocks`, { waitUntil: "networkidle" });
await page.waitForSelector("#market-panel-app table.grid tbody tr", { timeout: 30000 });
navigations = 0;

const mainCount = () =>
  page.$eval("#market-panel-app .head-actions .muted", (n) => n.textContent.trim());
const mainRowsInDom = () =>
  page.$$eval("#market-panel-app table.grid tbody tr:not(.vpad)", (rs) => rs.length);

R.initialCount = await mainCount();
R.initialDomRows = await mainRowsInDom();

// ---- 1. the market filter, client-side -----------------------------------
const SEL0 = "#market-panel-app .filterbar select:nth-of-type(1)";
const marketOptions = await page.$$eval(SEL0 + " option", (os) => os.map((o) => o.value).filter(Boolean));
R.marketOption = marketOptions[0];
const t0 = Date.now();
await page.selectOption(SEL0, marketOptions[0]);
await page.waitForTimeout(250);
R.filterMs = Date.now() - t0;
R.afterFilterCount = await mainCount();
R.navLoaderVisibleDuringFilter = await page.$eval("#nav-loader", (n) => n.classList.contains("on"));
R.navigationsAfterFilter = navigations;
R.urlAfterFilter = new URL(page.url()).search;

// ---- 2. the export link follows the filter --------------------------------
R.exportHref = await page.$eval("#market-panel-app .head-actions a.btn", (a) => a.getAttribute("href"));

// ---- 3. Back returns to the unfiltered view -------------------------------
await page.goBack();
await page.waitForTimeout(300);
R.urlAfterBack = new URL(page.url()).search;
R.countAfterBack = await mainCount();
R.navigationsAfterBack = navigations;

// ---- 4. the text filter ----------------------------------------------------
await page.fill("#tablefilter", "فولاد");
await page.waitForTimeout(250);
R.textFilterCount = await mainCount();
R.textFilterTickers = await page.$$eval(
  "#market-panel-app table.grid tbody tr:not(.vpad) td.sym",
  (tds) => tds.slice(0, 5).map((t) => t.textContent.replace("★", "").trim()),
);
await page.fill("#tablefilter", "");
await page.waitForTimeout(200);

// ---- 4b. the watchlist star -------------------------------------------
// Pinned to a specific ticker: the island re-renders after a toggle and the
// virtualizer may swap which rows are in the DOM, so "the first star" is not a
// stable target. Runs before the sort/scroll tests for the same reason.
const TICKER = await page.$eval(
  "#market-panel-app table.grid tbody tr:not(.vpad) td.sym",
  (t) => t.textContent.replace("★", "").trim(),
);
const STAR = `#market-panel-app .watch-star[data-ticker="${TICKER}"]`;
R.starTicker = TICKER;
R.starBefore = await page.$eval(STAR, (b) => b.className);
// Dispatch the click in the page rather than through Playwright's actionability
// checks: it auto-scrolls to the element first, and scrolling a virtualized
// table detaches the very node it is about to click.
await page.evaluate((sel) => document.querySelector(sel).click(), STAR);
await page.waitForFunction(
  (sel) => document.querySelector(sel)?.classList.contains("on"),
  STAR,
  { timeout: 5000 },
).catch(() => {});
R.starAfter = await page.$eval(STAR, (b) => b.className);
R.navBadge = await page.$eval("body", (b) => {
  const n = b.querySelector(".nav-badge");
  return n ? n.textContent.trim() : null;
});
// Both tables show the same symbol; the star must flip in both.
R.starInCalcTable = await page.$$eval(
  `.watch-star[data-ticker="${TICKER}"]`,
  (bs) => bs.map((b) => b.className),
);
// Toggle back so the test leaves no state behind.
await page.evaluate((sel) => document.querySelector(sel).click(), STAR);
await page.waitForFunction(
  (sel) => !document.querySelector(sel)?.classList.contains("on"),
  STAR,
  { timeout: 5000 },
).catch(() => {});
R.starRestored = await page.$eval(STAR, (b) => b.className);

// ---- 5. sorting ------------------------------------------------------------
const TH0 = "#market-panel-app table.grid thead th:nth-child(1)";
await page.click(TH0); // نماد, ascending
await page.waitForTimeout(250);
R.sortedAscFirst = await page.$$eval(
  "#market-panel-app table.grid tbody tr:not(.vpad) td.sym",
  (tds) => tds.slice(0, 3).map((t) => t.textContent.replace("★", "").trim()),
);
R.sortIndicatorAsc = await page.$eval(TH0, (h) => h.className);
await page.click(TH0); // descending
await page.waitForTimeout(250);
R.sortedDescFirst = await page.$$eval(
  "#market-panel-app table.grid tbody tr:not(.vpad) td.sym",
  (tds) => tds.slice(0, 3).map((t) => t.textContent.replace("★", "").trim()),
);
R.sortIndicatorDesc = await page.$eval(TH0, (h) => h.className);

// ---- 6. virtualization actually virtualizes -------------------------------
// Scroll to the very BOTTOM rather than to a pre-measured offset. The table
// above this one grows as its own rows are measured for real, so any position
// computed beforehand drifts; the bottom of the document is the one place whose
// meaning cannot drift — the last rows of the last table must be there.
R.domRowsAtTop = await mainRowsInDom();
R.tickersAtTop = await page.$$eval(
  "#market-panel-app table.grid tbody tr:not(.vpad) td.sym",
  (tds) => tds.slice(0, 3).map((t) => t.textContent.replace("★", "").trim()),
);
for (let i = 0; i < 6; i++) {
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(350);
}
R.domRowsAtBottom = await mainRowsInDom();
R.tickersAtBottom = await page.$$eval(
  "#market-panel-app table.grid tbody tr:not(.vpad) td.sym",
  (tds) => tds.slice(-3).map((t) => t.textContent.replace("★", "").trim()),
);
R.virtualWindowMoved =
  JSON.stringify(R.tickersAtTop) !== JSON.stringify(R.tickersAtBottom);
// Never more than a small slice of 742 rows in the DOM, wherever we are.
R.maxDomRows = Math.max(R.domRowsAtTop, R.domRowsAtBottom);
await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(400);

// ---- 8. clicking a row still navigates to the detail page -----------------
// Dispatched in the page and waited on explicitly: after the scroll test the
// virtualizer has re-rendered, so a handle taken beforehand is stale, and
// Playwright's auto-scroll would re-render it again mid-click.
const rowInfo = await page.evaluate(() => {
  const tr = document.querySelector("#market-panel-app table.grid tbody tr:not(.vpad)");
  const ticker = tr.querySelector("td.sym").textContent.replace("★", "").trim();
  tr.querySelector("td.rtl-name").click();
  return { ticker };
});
await page.waitForURL(/\/stock\/\d+/, { timeout: 15000 }).catch(() => {});
R.rowClickUrl = new URL(page.url()).pathname;
R.rowClickTicker = rowInfo.ticker;

R.pageErrors = errors;
R.totalNavigations = navigations;
R.documentRequests = documentRequests;

await browser.close();
console.log(JSON.stringify(R, null, 2));
