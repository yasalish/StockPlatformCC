/**
 * marketdata_check.mjs — browser-side verification of the three new pages.
 *
 *   node marketdata_check.mjs <baseUrl> <sessionCookie> [shotDir]
 *
 * Prints one JSON object on stdout; verify_marketdata.py turns it into checks.
 * Runs against the system Chrome/Edge through playwright-core, so nothing is
 * downloaded — the same arrangement perf_check.mjs and capture.mjs use.
 *
 * WHAT THIS CATCHES THAT PYTHON CANNOT
 *
 * All three pages carry inline scripts, and two of the behaviours that matter
 * only exist in a browser: the grouped nav menus have to actually OPEN (the bar
 * they live in used to be an overflow scroller, which clips an absolutely
 * positioned panel on both axes), and the order-book drawers have to survive a
 * column sort still attached to their own symbol. A server-rendered assertion
 * can see neither.
 */
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

const [, , baseUrl, cookieValue, shotDir] = process.argv;

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

if (shotDir) {
  try { mkdirSync(shotDir, { recursive: true }); } catch { /* already there */ }
}
const shoot = async (name) => {
  if (!shotDir) return;
  await page.screenshot({ path: `${shotDir}/${name}.png`, fullPage: true });
};

// ---------------------------------------------------------------------------
// 1. THE GROUPED NAV — does a menu open, and is the panel actually visible?
//
// `hidden` coming off is not enough. The bar it sits in was an overflow-x
// scroller, which clips an absolutely positioned child on BOTH axes, so the
// panel could be un-hidden and still be an invisible sliver. The height of its
// bounding box is what says otherwise.
// ---------------------------------------------------------------------------
await page.goto(`${baseUrl}/indices`, { waitUntil: "load", timeout: 120000 });
out.navTriggers = await page.locator('[data-role="nav-trigger"]').count();
await page.locator('[data-role="nav-trigger"]').first().click();
await page.waitForTimeout(120);
const pop = page.locator('[data-role="nav-pop"]').first();
out.navOpens = await pop.isVisible();
const box = await pop.boundingBox();
out.navPopHeight = box ? Math.round(box.height) : 0;
out.navPopLinks = await pop.locator("a").count();
await shoot("nav-open");
// Escape closes it and hands focus back — the keyboard path, not just the mouse.
await page.keyboard.press("Escape");
await page.waitForTimeout(80);
out.navCloses = !(await pop.isVisible());

// ---------------------------------------------------------------------------
// 2. «شاخص‌ها» — the chart draws, and picking another index redraws it
// ---------------------------------------------------------------------------
out.indexChartSvg = await page.locator("#idx-chart svg").count();
out.indexRows = await page.locator("#idx-market tbody tr").count();
out.sectorRows = await page.locator("#idx-sector tbody tr").count();
const firstTitle = await page.locator("#idx-chart-title").textContent();
const opts = await page.locator("#idx-pick option").count();
if (opts > 1) {
  const second = await page.locator("#idx-pick option").nth(1).getAttribute("value");
  await page.selectOption("#idx-pick", second);
  await page.waitForTimeout(900);
  out.chartTitleChanged = (await page.locator("#idx-chart-title").textContent()) !== firstTitle;
  out.chartStillDrawn = (await page.locator("#idx-chart svg").count()) > 0;
  // the URL follows, so the chart being looked at survives a reload
  out.focusInUrl = page.url().includes("focus=");
}
await shoot("indices");

// ---------------------------------------------------------------------------
// 3. «پول حقیقی و حقوقی»
// ---------------------------------------------------------------------------
await page.goto(`${baseUrl}/moneyflow`, { waitUntil: "load", timeout: 120000 });
out.flowRows = await page.locator("#flow-table tbody tr").count();
out.flowSortable = await page.locator("#flow-table thead th[data-sort]").count();
await shoot("moneyflow");

// ---------------------------------------------------------------------------
// 4. «تابلوی زنده» — the order-book drawer, and the sort that must not scatter it
// ---------------------------------------------------------------------------
await page.goto(`${baseUrl}/live`, { waitUntil: "load", timeout: 120000 });
out.liveRows = await page.locator("#live-table tbody tr.live-row").count();
out.liveDrawers = await page.locator("#live-table tbody tr.ob-drawer").count();

if (out.liveRows > 2) {
  const firstTicker = await page.locator("tr.live-row").first().getAttribute("data-ticker");
  await page.locator("tr.live-row").first().click();
  await page.waitForTimeout(1200);
  out.drawerOpens = await page.locator("tr.ob-drawer").first().isVisible();
  out.drawerHasBook = await page.locator("tr.ob-drawer .ob-table").count();

  // Sort by a column, then assert every drawer is STILL the sibling of its own
  // symbol. This is the regression that made BN.initTable learn data-nosort:
  // the sorter moves <tr>s, and a drawer sorted as if it were a record ends up
  // under someone else's row.
  //
  // The row ORDER is recorded either side of the click, because "every drawer
  // is still next to its row" is trivially true of a sort that never ran —
  // and a sort that throws before it moves anything leaves exactly that state.
  // That false pass is what hid the data-nosort bug the first time round.
  const orderBefore = await page.evaluate(() =>
    Array.from(document.querySelectorAll("#live-table tbody tr.live-row"))
      .map((r) => r.dataset.ticker).join(","));
  await page.locator("#live-table thead th[data-sort]").nth(2).click();
  await page.waitForTimeout(250);
  const orderAfter = await page.evaluate(() =>
    Array.from(document.querySelectorAll("#live-table tbody tr.live-row"))
      .map((r) => r.dataset.ticker).join(","));
  out.sortActuallyReordered = orderBefore !== orderAfter;
  out.sortedAscending = await page.evaluate(() =>
    Array.from(document.querySelectorAll("#live-table tbody tr.live-row"))
      .map((r) => parseFloat(r.cells[2].dataset.v || "0"))
      .every((v, i, a) => i === 0 || a[i - 1] <= v));
  out.drawersStillPaired = await page.evaluate(() =>
    Array.from(document.querySelectorAll("#live-table tbody tr.ob-drawer"))
      .every((d) => {
        const prev = d.previousElementSibling;
        return prev && prev.classList.contains("live-row") &&
               prev.dataset.ticker === d.dataset.for;
      }));
  out.openDrawerFollowedItsRow = await page.evaluate((tk) => {
    const d = document.querySelector(`tr.ob-drawer[data-for="${CSS.escape(tk)}"]`);
    return !!d && !d.hidden && d.previousElementSibling?.dataset.ticker === tk;
  }, firstTicker);
}
await shoot("live");

// ---------------------------------------------------------------------------
// 5. «به‌روزرسانی» — the data-type selector drives the form
// ---------------------------------------------------------------------------
await page.goto(`${baseUrl}/update`, { waitUntil: "load", timeout: 120000 });
out.datasetOptions = await page.locator("#kind-select option").count();
out.datasetGroups = await page.locator("#kind-select optgroup").count();
out.coverageCells = await page.locator(".cover-grid .cover-cell").count();
out.noteFilledForPrice = ((await page.locator("#ds-note").textContent()) || "").trim().length > 10;
// A snapshot dataset has no date range and no single-symbol scope, so both must
// disappear — a form that keeps showing controls the run ignores is lying.
await page.selectOption("#kind-select", "watch");
await page.waitForTimeout(150);
out.snapshotHidesDates = await page.evaluate(() =>
  document.getElementById("lbl-start").style.display === "none" &&
  document.getElementById("lbl-end").style.display === "none" &&
  document.getElementById("lbl-ticker").style.display === "none");
out.noteChangedForSnapshot = ((await page.locator("#ds-note").textContent()) || "").includes("عکس");
// …and picking a dated one brings them back.
await page.selectOption("#kind-select", "stock_ri");
await page.waitForTimeout(150);
out.datedShowsDates = await page.evaluate(() =>
  document.getElementById("lbl-start").style.display !== "none" &&
  document.getElementById("lbl-ticker").style.display !== "none");
await shoot("update");

console.log(JSON.stringify(out));
await browser.close();
