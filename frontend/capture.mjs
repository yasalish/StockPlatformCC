/**
 * capture.mjs — drive the real page in a real browser and record what it shows.
 *
 * Used for the before/after comparison the order requires. Runs against the
 * system Chrome/Edge via playwright-core, so nothing is downloaded.
 *
 *   node capture.mjs <baseUrl> <user> <pass> <path> <outPrefix>
 *
 * Writes <outPrefix>.png (full-page screenshot) and <outPrefix>.json
 * (structured extraction of the table: headers, first rows, counts, DOM size).
 */
import { chromium } from "playwright-core";
import { writeFileSync } from "node:fs";

const [, , baseUrl, user, pass, path, outPrefix] = process.argv;

function launch() {
  // --no-proxy-server: this machine runs a local proxy client that intercepts
  // 127.0.0.1 and returns 503, so the browser must be told to go direct.
  const opts = {
    headless: true,
    args: ["--no-proxy-server", "--disable-dev-shm-usage"],
  };
  for (const channel of ["chrome", "msedge"]) {
    try {
      return chromium.launch({ ...opts, channel });
    } catch {
      /* try the next one */
    }
  }
  return chromium.launch(opts);
}

const browser = await launch();
const ctx = await browser.newContext({
  viewport: { width: 1500, height: 1000 },
  locale: "fa-IR",
  deviceScaleFactor: 1,
});
const page = await ctx.newPage();
const consoleErrors = [];
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(m.text());
});
page.on("pageerror", (e) => consoleErrors.push(String(e)));
const failedRequests = [];
page.on("response", (r) => { if (r.status() >= 400) failedRequests.push(r.status() + " " + r.url()); });

// --- log in --------------------------------------------------------------
await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
await page.fill('input[name="username"]', user);
await page.fill('input[name="password"]', pass);
await Promise.all([
  page.waitForNavigation({ waitUntil: "domcontentloaded" }),
  page.click('button[type="submit"], input[type="submit"]'),
]);

// --- the page under test --------------------------------------------------
const t0 = Date.now();
await page.goto(`${baseUrl}${path}`, { waitUntil: "networkidle" });
// The island fetches after load; wait for a populated grid either way.
await page
  .waitForFunction(() => {
    const t = document.querySelector("table.grid");
    return t && t.tBodies[0] && t.tBodies[0].rows.length > 0;
  }, { timeout: 30000 })
  .catch(() => {});
const loadMs = Date.now() - t0;

const data = await page.evaluate(() => {
  const tables = Array.from(document.querySelectorAll("table.grid"));
  const describe = (t) => {
    const headRow = t.tHead ? t.tHead.rows[t.tHead.rows.length - 1] : null;
    const headers = headRow ? Array.from(headRow.cells).map((c) => c.textContent.trim()) : [];
    const bodyRows = Array.from(t.tBodies[0] ? t.tBodies[0].rows : []).filter(
      (r) => !r.classList.contains("vpad"),
    );
    const cellsOf = (r) => Array.from(r.cells).map((c) => c.textContent.trim());
    const headRowEl = headRow;
    return {
      headers,
      tableWidth: t.offsetWidth,
      colWidths: headRowEl ? Array.from(headRowEl.cells).map((c) => c.offsetWidth) : [],
      rowHeights: bodyRows.slice(0, 8).map((r) => r.offsetHeight),
      renderedRows: bodyRows.length,
      firstRows: bodyRows.slice(0, 12).map(cellsOf),
      // The pill class carries the up/down colour; compare it too.
      firstPillClasses: bodyRows
        .slice(0, 12)
        .map((r) => Array.from(r.querySelectorAll(".pill")).map((p) => p.className)),
      stars: bodyRows.slice(0, 12).map((r) => {
        const s = r.querySelector(".watch-star");
        return s ? s.className : null;
      }),
    };
  };
  return {
    title: document.title,
    dir: document.documentElement.getAttribute("dir"),
    lang: document.documentElement.getAttribute("lang"),
    domNodes: document.getElementsByTagName("*").length,
    tables: tables.map(describe),
    headerText: (document.querySelector(".panel-head .muted") || {}).textContent?.trim() ?? "",
    exportHref: (document.querySelector('.head-actions a.btn') || {}).getAttribute?.("href") ?? "",
    filterSelects: Array.from(document.querySelectorAll(".filterbar select")).map((s) => ({
      options: Array.from(s.options).map((o) => o.textContent.trim()).slice(0, 6),
      count: s.options.length,
    })),
  };
});

data.loadMs = loadMs;
data.consoleErrors = consoleErrors;
data.failedRequests = failedRequests;
data.url = page.url();

await page.screenshot({ path: `${outPrefix}.png`, fullPage: false });
writeFileSync(`${outPrefix}.json`, JSON.stringify(data, null, 2), "utf8");

await browser.close();
console.log(
  JSON.stringify({
    ok: true,
    loadMs,
    domNodes: data.domNodes,
    tables: data.tables.map((t) => t.renderedRows),
    consoleErrors: consoleErrors.length,
    failedRequests,
  }),
);
