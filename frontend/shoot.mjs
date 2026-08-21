/**
 * shoot.mjs — screenshot a page WITHOUT logging in.
 *
 * capture.mjs always signs in first, which is right for the app itself and
 * useless for the one screen you cannot be signed in on. Same launcher (system
 * Chrome or Edge via playwright-core, nothing downloaded), same full-page
 * output, no session.
 *
 *   node shoot.mjs <baseUrl> <path> <outFile> [theme] [width] [height]
 *
 * `theme` writes localStorage("boursenegar-theme") before the first paint, so a
 * dark palette can be photographed without an account to store the preference
 * on.
 */
import { chromium } from "playwright-core";

const [, , baseUrl, path, outFile, theme, w, h] = process.argv;
if (!baseUrl || !path || !outFile) {
  console.error("usage: node shoot.mjs <baseUrl> <path> <outFile> [theme] [w] [h]");
  process.exit(2);
}

function launch() {
  const opts = { headless: true, args: ["--no-proxy-server", "--disable-dev-shm-usage"] };
  for (const channel of ["chrome", "msedge"]) {
    try {
      return chromium.launch({ ...opts, channel });
    } catch { /* try the next one */ }
  }
  return chromium.launch(opts);
}

const browser = await launch();
const page = await browser.newPage({
  viewport: { width: Number(w) || 1500, height: Number(h) || 1000 },
  deviceScaleFactor: 1,
});

if (theme) {
  // The pre-paint script in base.html reads this key, so setting it here is
  // what a returning visitor's browser would look like.
  await page.addInitScript((t) => {
    try { localStorage.setItem("boursenegar-theme", t); } catch { /* private mode */ }
  }, theme);
}

await page.goto(baseUrl + path, { waitUntil: "networkidle" });
await page.waitForTimeout(500);
await page.screenshot({ path: outFile, fullPage: false });
await browser.close();
console.log("wrote " + outFile);
