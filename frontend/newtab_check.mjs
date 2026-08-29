/**
 * newtab_check.mjs — does clicking a symbol really open a new tab?
 *
 *   node newtab_check.mjs <baseUrl> <sessionCookie>
 *
 * Prints one JSON object on stdout; verify_new_tab.py turns it into checks.
 * Runs against the system Chrome/Edge through playwright-core, so nothing is
 * downloaded — the same arrangement perf_check.mjs uses.
 *
 * For each page it makes three clicks and watches what the BROWSER does:
 *
 *   ticker  — the <a> in the نماد cell
 *   name    — the <a> in the نام cell, where the table has one
 *   row     — a cell that is neither, which the row handler owns
 *
 * and for each one records the URL of the tab that opened, whether the list
 * page itself navigated (it must not), and how many tabs appeared (exactly one
 * — a link inside a clickable row is the obvious way to open two).
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

const out = { errors: [], pages: {} };
const browser = await launch();
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
if (cookieValue) {
  const url = new URL(baseUrl);
  await ctx.addCookies([{ name: "session", value: cookieValue, domain: url.hostname, path: "/" }]);
}

/** Click `locator` and report what the browser did with it. */
async function clickAndWatch(page, locator) {
  if (!(await locator.count())) return { found: false };
  const before = page.url();
  const opened = [];
  const onPage = (p) => opened.push(p);
  ctx.on("page", onPage);

  const el = locator.first();
  const href = await el.evaluate((node) => {
    const a = node.closest("a[href]");
    if (a) return a.href;
    const tr = node.closest("tr[data-href]");
    return tr ? new URL(tr.dataset.href, location.origin).href : null;
  });

  // The tab this list page is in must be the active one, or the click lands on
  // a background page whose window.open Chrome may decline. The previous
  // assertion in this same function opened and closed a tab, which is exactly
  // how the page ends up in the background.
  await page.bringToFront();
  // Wait for the tab EVENT rather than a fixed sleep: a slow first paint made
  // the fixed wait report "no tab opened" for a tab that appeared 900 ms later.
  const appeared = ctx.waitForEvent("page", { timeout: 8000 }).catch(() => null);
  await el.click();
  await appeared;
  await page.waitForTimeout(400);          // …and let a SECOND one show up
  ctx.off("page", onPage);

  const urls = [];
  for (const p of opened) {
    try {
      await p.waitForLoadState("domcontentloaded", { timeout: 15000 });
    } catch { /* report whatever URL it reached */ }
    urls.push(p.url());
    await p.close();
  }
  return {
    found: true,
    expected: href,
    openedCount: opened.length,
    openedUrl: urls[0] ?? null,
    listNavigated: page.url() !== before,
    listUrl: page.url(),
  };
}

/**
 * @param {string} key      name in the report
 * @param {string} path     page to open
 * @param {string} rowSel   rows that link to a security
 */
async function checkPage(key, path, rowSel) {
  const page = await ctx.newPage();
  page.on("pageerror", (e) => out.errors.push(`${key}: ${e}`));
  const report = { path };
  try {
    await page.goto(`${baseUrl}${path}`, { waitUntil: "load", timeout: 120000 });
    await page.waitForSelector(rowSel, { timeout: 60000 });

    // The same row for all three clicks, so they are comparable.
    const row = page.locator(rowSel).first();
    report.rowTicker = (await row.locator("td.sym").first().innerText()).trim();
    report.ticker = await clickAndWatch(page, row.locator("td.sym a.row-link"));
    report.name = await clickAndWatch(page, row.locator("td.rtl-name a.row-link"));
    // A cell the ROW handler owns rather than a link of its own.
    report.row = await clickAndWatch(page, row.locator("td.num"));

    // The star must still toggle the watchlist rather than open anything.
    // Not on /watchlist itself: un-starring there REMOVES the row by design
    // (app.js toggleWatch), so there would be nothing left to click back on.
    const star = row.locator(".watch-star");
    const isWatchlist = await row.evaluate((tr) => !!tr.closest('[id^="wl-"]'));
    if (isWatchlist) {
      report.starSkipped = true;
    } else if (await star.count()) {
      const opened = [];
      const onPage = (p) => opened.push(p);
      const before = page.url();
      ctx.on("page", onPage);
      await star.first().click();
      await page.waitForTimeout(600);
      ctx.off("page", onPage);
      report.starOpened = opened.length;
      report.starNavigated = page.url() !== before;
      for (const p of opened) await p.close();
      await star.first().click();          // put the watchlist back as it was
      await page.waitForTimeout(400);
    }
  } catch (e) {
    report.error = String(e).split("\n")[0].slice(0, 300);
  }
  await page.close();
  out.pages[key] = report;
}

await checkPage("stocks", "/stocks", "#market-panel-app tbody tr[data-ticker]");
await checkPage("etfs", "/etfs", "#market-panel-app tbody tr[data-ticker]");
await checkPage("performance", "/performance", "#perf-app tbody tr[data-ticker]");
await checkPage("screener", "/screener", "#screener-app tbody tr[data-ticker]");
await checkPage("filters", "/filters", "#scan-app tbody tr[data-ticker]");
await checkPage("strategies", "/strategies", "#scan-app tbody tr[data-ticker]");
await checkPage("watchlist", "/watchlist", "table.grid tbody tr.clickable[data-href]");
await checkPage("dashboard", "/dashboard", "table.grid tbody tr.clickable[data-href]");

await browser.close();
console.log(JSON.stringify(out));
