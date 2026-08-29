/**
 * loader_check.mjs — the «در حال محاسبه…» overlay must wait, not flash
 *
 *   node loader_check.mjs <baseUrl> <sessionCookie>
 *
 * Prints one JSON object on stdout; verify_nav_loader.py turns it into checks.
 *
 * #nav-loader is a full-screen blurred backdrop with a box in the middle of the
 * screen. It exists for the genuinely slow submits — a big group over a long
 * date range. It used to go up the instant anything was clicked, and a page on
 * this app answers in 50–200 ms, so it appeared and vanished again within a
 * tenth of a second on every single click. Both halves are measured here:
 *
 *   fast — a normal navigation must show NOTHING. Recorded by a MutationObserver
 *          that writes to sessionStorage, because the document is destroyed by
 *          the navigation and anything measured inside it races the browser.
 *   slow — a navigation held back past the delay must still put the box up, or
 *          the fix would have removed the feature instead of the flash.
 */
import { chromium } from "playwright-core";

const [, , baseUrl, cookieValue] = process.argv;

function launch() {
  const opts = { headless: true, args: ["--no-proxy-server", "--disable-dev-shm-usage"] };
  for (const channel of ["chrome", "msedge"]) {
    try {
      return chromium.launch({ ...opts, channel });
    } catch { /* next */ }
  }
  return chromium.launch(opts);
}

//  The observer survives only as long as the document, so it records into
//  sessionStorage, which the NEXT document can be asked about. `atClick` is the
//  synchronous reading: this listener is registered on `document` after
//  app.js's own, same phase, so by the time it runs the overlay has already
//  been shown or not shown for that click.
const WATCH = `
  sessionStorage.removeItem("bnLoaderOn");
  sessionStorage.removeItem("bnLoaderEver");
  sessionStorage.removeItem("bnLoaderShownAt");
  sessionStorage.removeItem("bnLoaderClickAt");
  (function () {
    const el = document.getElementById("nav-loader");
    if (!el) return;
    const mark = () => {
      if (!el.classList.contains("on")) return;
      sessionStorage.setItem("bnLoaderEver", "1");
      if (!sessionStorage.getItem("bnLoaderShownAt")) {
        sessionStorage.setItem("bnLoaderShownAt", String(performance.now()));
      }
    };
    mark();
    new MutationObserver(mark).observe(el, { attributes: true, attributeFilter: ["class"] });
    document.addEventListener("click", () => {
      sessionStorage.setItem("bnLoaderClickAt", String(performance.now()));
      sessionStorage.setItem("bnLoaderOn",
        el.classList.contains("on") ? "immediately" : "not-yet");
    });
  })();
`;

const out = { fast: [], slow: null };
const browser = await launch();
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const url = new URL(baseUrl);
if (cookieValue) {
  await ctx.addCookies([{ name: "session", value: cookieValue, domain: url.hostname, path: "/" }]);
}
const page = await ctx.newPage();
page.on("pageerror", (e) => (out.errors = [...(out.errors ?? []), String(e)]));

async function navClick(label, href) {
  await page.goto(`${baseUrl}/dashboard`, { waitUntil: "load", timeout: 120000 });
  await page.evaluate(WATCH);

  const link = page.locator(`.topnav a[href="${href}"]`).first();
  if (!(await link.count())) return out.fast.push({ label, found: false });

  const t0 = Date.now();
  await link.click();
  await page.waitForLoadState("load", { timeout: 60000 }).catch(() => {});
  const navMs = Date.now() - t0;

  const [atClick, everShown, stillOn] = await page.evaluate(() => [
    sessionStorage.getItem("bnLoaderOn"),
    sessionStorage.getItem("bnLoaderEver") === "1",
    !!document.querySelector("#nav-loader.on"),
  ]);
  out.fast.push({ label, href, found: true, navMs, atClick, everShown, stillOn });
}

await navClick("stocks", "/stocks");
await navClick("etfs", "/etfs");
await navClick("watchlist", "/watchlist");
await navClick("performance", "/performance");
await navClick("heatmap", "/heatmap");

/* ---- and the other half: a navigation slow enough to deserve the box ----

   Measured the same way as the fast case, and for the same reason: while a
   navigation is pending, page.evaluate() is queued behind it, so polling the
   old document from here reports nothing until it is far too late. The
   observer, running inside the page, has no such problem. */
await page.goto(`${baseUrl}/dashboard`, { waitUntil: "load", timeout: 120000 });
await page.evaluate(WATCH);

const HOLD_MS = 1500;
await page.route("**/stocks", async (route) => {
  await new Promise((r) => setTimeout(r, HOLD_MS));
  await route.continue();
});
const t0 = Date.now();
await page.locator('.topnav a[href="/stocks"]').first().click();
await page.waitForLoadState("load", { timeout: 60000 }).catch(() => {});

const slow = await page.evaluate(() => ({
  ever: sessionStorage.getItem("bnLoaderEver") === "1",
  shownAt: Number(sessionStorage.getItem("bnLoaderShownAt") || 0),
  clickAt: Number(sessionStorage.getItem("bnLoaderClickAt") || 0),
}));
out.slow = {
  heldMs: HOLD_MS,
  appeared: slow.ever,
  appearedAfterMs: slow.ever && slow.clickAt
    ? Math.round(slow.shownAt - slow.clickAt) : null,
  totalMs: Date.now() - t0,
};

await browser.close();
console.log(JSON.stringify(out));
