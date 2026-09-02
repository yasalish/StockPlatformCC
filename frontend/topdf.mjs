/**
 * topdf.mjs — render a local HTML file to PDF through the system Chrome/Edge.
 *
 *   node topdf.mjs <input.html> <output.pdf>
 *
 * Uses playwright-core against an installed browser, the same arrangement
 * capture.mjs and the *_check.mjs verifiers use, so nothing is downloaded.
 * `printBackground` is on because the document's tables carry their meaning in
 * shaded header rows and highlighted key columns — printing it without them
 * would drop information, not just colour.
 */
import { chromium } from "playwright-core";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const [, , input, output] = process.argv;
if (!input || !output) {
  console.error("usage: node topdf.mjs <input.html> <output.pdf>");
  process.exit(2);
}

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

const browser = await launch();
const page = await browser.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

await page.goto(pathToFileURL(resolve(input)).href, { waitUntil: "load", timeout: 60000 });
// The @font-face files are loaded from disk; without waiting for them the first
// page renders in the fallback and the Persian text is measured wrong.
await page.evaluate(() => document.fonts.ready);
await page.waitForTimeout(400);

await page.pdf({
  path: resolve(output),
  format: "A4",
  printBackground: true,
  // The page's own @page rule owns the margins; passing them here as well would
  // add a second set on top of it.
  margin: { top: "0", right: "0", bottom: "0", left: "0" },
  preferCSSPageSize: true,
});

console.log(JSON.stringify({ ok: true, output: resolve(output), errors }));
await browser.close();
