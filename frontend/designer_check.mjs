/**
 * designer_check.mjs — browser-side verification of «طراحی فیلتر».
 *
 *   node designer_check.mjs <baseUrl> <sessionCookie> [shotDir]
 *
 * Prints one JSON object on stdout; verify_designer.py turns it into checks.
 *
 * This one has to do more than the other island checks. The others render a
 * table and are proved by counting rows; a node editor is only proved by USING
 * it, so this drags a chip, pulls a wire between two ports, runs the filter and
 * opens the per-symbol explanation — the four things that, if any one of them
 * silently stopped working, would leave a page that still looks perfect.
 */
import { chromium } from "playwright-core";

const [, , baseUrl, cookieValue, shotDir] = process.argv;

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

const out = { errors: [], console: [] };
const browser = await launch();
const ctx = await browser.newContext({ viewport: { width: 1680, height: 1050 } });
if (cookieValue) {
  const u = new URL(baseUrl);
  await ctx.addCookies([{ name: "session", value: cookieValue, domain: u.hostname, path: "/" }]);
}
const page = await ctx.newPage();
page.on("pageerror", (e) => out.errors.push(String(e)));
page.on("console", (m) => {
  if (m.type() === "error") out.console.push(m.text().slice(0, 200));
});

const t0 = Date.now();
await page.goto(`${baseUrl}/filter-designer`, { waitUntil: "load", timeout: 120000 });
// The board opens on the first example, so chips exist without any interaction.
await page.waitForSelector(".dz-node", { timeout: 60000 });
out.interactiveMs = Date.now() - t0;

/**
 * Wait for the board to stop moving before touching it.
 *
 * The canvas frames the graph on the first animation frame after mount, so for
 * one frame every chip is somewhere else. Measuring a bounding box before that
 * and clicking on it after means pressing the mouse on empty board — which the
 * editor correctly reads as "deselect and pan", and which looked exactly like a
 * broken drag when this check first ran.
 */
async function settle() {
  let last = "";
  for (let i = 0; i < 40; i++) {
    const now = await page.evaluate(() => document.querySelector(".dz-layer")?.style.transform ?? "");
    if (now && now === last) return;
    last = now;
    await page.waitForTimeout(50);
  }
}
await settle();

/* ---------------------------------------------------------- what rendered */
Object.assign(
  out,
  await page.evaluate(() => ({
    nodes: document.querySelectorAll(".dz-node").length,
    wires: document.querySelectorAll("path.dz-wire").length,
    chips: document.querySelectorAll(".dz-chip").length,
    captions: document.querySelectorAll(".dz-cat").length,
    inPorts: document.querySelectorAll(".dz-in").length,
    outPorts: document.querySelectorAll(".dz-out").length,
    shelves: document.querySelectorAll(".dz-shelf").length,
    subheads: document.querySelectorAll(".dz-subhead").length,
    parts: document.querySelectorAll(".dz-part").length,
    // Every sub-shelf caption, so the Python side can assert the palette really
    // is two levels and not one long list with headings that all say the same.
    subLabels: [...new Set([...document.querySelectorAll(".dz-subhead span:nth-child(2)")]
      .map((n) => n.textContent.trim()))],
    // Every chip caption, so the Python side can assert the reference product's
    // own vocabulary is on the board («close-1», «SMA ۵۰ final», «And»).
    titles: [...document.querySelectorAll(".dz-chip-t")].map((n) => n.textContent.trim()),
    cats: [...new Set([...document.querySelectorAll(".dz-cat")].map((n) => n.textContent.trim()))],
    // The chip fill has to be a real colour, not a fallback grey.
    fills: [...new Set([...document.querySelectorAll(".dz-chip")].map((n) => getComputedStyle(n).backgroundColor))],
    boardW: Math.round(document.querySelector(".dz-board")?.getBoundingClientRect().width ?? 0),
    // The board must fit the window. It once grew to the palette's natural
    // height (1,607 px in a 1,180 px window) because the grid row had only a
    // min-height, which pushed the auto-framed graph below the fold.
    boardH: Math.round(document.querySelector(".dz-board")?.getBoundingClientRect().height ?? 0),
    viewportH: window.innerHeight,
    // Every chip has to be inside the board after the opening auto-fit.
    chipsInBoard: (() => {
      const b = document.querySelector(".dz-board").getBoundingClientRect();
      return [...document.querySelectorAll(".dz-node")].filter((n) => {
        const r = n.getBoundingClientRect();
        return r.left >= b.left - 1 && r.right <= b.right + 1 &&
               r.top >= b.top - 1 && r.bottom <= b.bottom + 1;
      }).length;
    })(),
    ltr: document.querySelector(".dz-board")?.getAttribute("dir"),
    hScroll: document.documentElement.scrollWidth > document.documentElement.clientWidth + 2,
  })),
);

/* --------------------------------------------------------- drag one chip */
{
  const chip = page.locator(".dz-node").first();
  const before = await chip.boundingBox();
  await page.mouse.move(before.x + before.width / 2, before.y + 8);
  await page.mouse.down();
  await page.mouse.move(before.x + before.width / 2 + 90, before.y + 68, { steps: 12 });
  await page.mouse.up();
  const after = await chip.boundingBox();
  out.dragDx = Math.round(after.x - before.x);
  out.dragDy = Math.round(after.y - before.y);
  out.dragSelected = await page.locator(".dz-node.is-sel").count();
  // The inspector must have opened onto the chip that was grabbed.
  out.inspectorFields = await page.locator(".dz-field").count();
}

/* ------------------------------------------------- add chips and wire them */
{
  const wiresBefore = await page.locator("path.dz-wire").count();
  const nodesBefore = await page.locator(".dz-node").count();

  // Added by CLICKING the palette (drag-and-drop between two elements is the
  // one gesture Playwright cannot reproduce faithfully). Two nodes, and the
  // second one's inputs are EMPTY — wiring into an input that already has a
  // wire replaces it, so the edge count would not move and the check would pass
  // whether or not anything connected.
  async function addPart(label) {
    await page.locator(".dz-search input").fill(label);
    await page.locator(".dz-part").first().click();
    await page.waitForTimeout(180);
  }
  await addPart("عدد ثابت");
  await addPart("مقایسه");
  out.addedNode = (await page.locator(".dz-node").count()) - nodesBefore;
  out.addedSelected = await page.locator(".dz-node.is-sel").count();

  const byCat = (t) => page.locator(".dz-node", { has: page.locator(".dz-cat", { hasText: t }) });
  const src = byCat("عدد ثابت").last().locator(".dz-out").first();
  const dst = byCat(/^مقایسه$/).last().locator('[data-in-port="a"]');
  const a = await src.boundingBox();
  const b = await dst.boundingBox();
  out.wireGeometry = !!(a && b);
  if (a && b) {
    await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
    await page.mouse.down();
    await page.mouse.move(b.x + b.width / 2 - 50, b.y + b.height / 2, { steps: 10 });
    out.liveWire = await page.locator("path.dz-wire.is-live").count();
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 6 });
    await page.mouse.up();
    await page.waitForTimeout(180);
  }
  out.wiredDelta = (await page.locator("path.dz-wire").count()) - wiresBefore;

  // …and cutting a wire by clicking it. The same wire, so this proves the one
  // just made is a real edge and not a stray path element.
  // wirePath() runs out, down the column at the horizontal midpoint, then in —
  // so the midpoint of the two ports is ON the vertical segment.
  const cutFrom = await src.boundingBox();
  const cutTo = await dst.boundingBox();
  if (cutFrom && cutTo) {
    const mx = (cutFrom.x + cutFrom.width / 2 + cutTo.x + cutTo.width / 2) / 2;
    const my = (cutFrom.y + cutFrom.height / 2 + cutTo.y + cutTo.height / 2) / 2;
    await page.mouse.move(mx, my);
    out.hotWire = await page.locator("path.dz-wire.is-hot").count();
    await page.mouse.click(mx, my);
    await page.waitForTimeout(180);
  }
  out.afterCut = (await page.locator("path.dz-wire").count()) - wiresBefore;
}

/* ---------------------------------------------------------------- undo it */
// Click the board first: focus is still in the palette's search box, where
// Ctrl+Z belongs to the text field and the editor deliberately keeps its hands
// off it.
await page.mouse.click(12, (await page.locator(".dz-board").boundingBox()).y + 12);
for (let i = 0; i < 6; i++) {
  await page.keyboard.press("Control+z");
  await page.waitForTimeout(80);
}
out.afterUndoNodes = await page.locator(".dz-node").count();

/* ----------------------------------------- run: it must NAVIGATE, not inline */
{
  // Back to a known-good graph first.
  const picker = page.locator(".dz-sel", { hasText: "نمونه‌ها" }).locator("select");
  await picker.selectOption({ index: 2 }); // the golden-cross example
  await settle();
  out.exampleNodes = await page.locator(".dz-node").count();

  const t1 = Date.now();
  await Promise.all([
    page.waitForURL(/\/filter-designer\/result/, { timeout: 60000 }),
    page.getByRole("button", { name: /اجرا/ }).click(),
  ]);
  out.navigated = new URL(page.url()).pathname;

  // …and the results page runs it and fills the table.
  await page.waitForSelector(".dz-results .dz-count", { timeout: 180000 });
  await page.waitForTimeout(150);
  out.runMs = Date.now() - t1;
  out.resultRows = await page.locator(".dz-results tbody tr[data-ticker]").count();
  out.resultCols = await page.locator(".dz-results table.grid thead th").count();
  out.countLabel = (await page.locator(".dz-count").first().textContent().catch(() => "")) ?? "";
  out.hasCsv = await page.getByRole("button", { name: /CSV/ }).count();
  out.backLink = await page.locator('a[href^="/filter-designer"]').count();
  // The diagram starts COLLAPSED — the table is what this page is for.
  out.graphHiddenAtFirst = !(await page.locator(".dzr-graph .dz-node").first().isVisible().catch(() => false));

  // «فیلترهای دیگر» — pick another filter from the rail and it must re-run in
  // place, without a page load and without going back to the canvas.
  out.railItems = await page.locator(".dzr-rail-item").count();
  out.railActive = await page.locator(".dzr-rail-item.is-on").count();
  const firstCount = (await page.locator(".dz-count").first().textContent()) ?? "";
  const target = page.locator(".dzr-rail-item").nth(out.railItems - 1);
  out.railTargetName = (await target.textContent())?.trim() ?? "";
  const urlBefore = page.url();
  await target.click();
  await page.waitForFunction(
    (prev) => {
      const el = document.querySelector(".dz-count");
      return el && el.textContent.trim() !== prev;
    },
    firstCount.trim(),
    { timeout: 180000 },
  );
  await page.waitForTimeout(200);
  out.railSwitchedCount = (await page.locator(".dz-count").first().textContent())?.trim() ?? "";
  out.railStillSamePage = page.url().split("?")[0] === urlBefore.split("?")[0];
  out.railHeading = (await page.locator(".dz-results .panel-head h2").textContent())?.trim() ?? "";
  out.railActiveAfter = await page.locator(".dzr-rail-item.is-on").count();
  out.railRows = await page.locator(".dz-results tbody tr[data-ticker]").count();
  out.resultHScroll = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 2,
  );
}

/* --------------------- «چرا؟» opens the diagram and paints the answer on it */
if (out.resultRows > 0) {
  await page.locator(".dz-results tbody tr").first().locator("button", { hasText: "چرا" }).click();
  await page.waitForSelector(".dz-verdict", { timeout: 60000 });
  await page.waitForTimeout(300);
  Object.assign(
    out,
    await page.evaluate(() => ({
      verdicts: document.querySelectorAll(".dz-verdict").length,
      verdictOn: document.querySelectorAll(".dz-verdict.is-on").length,
      tintedWires: document.querySelectorAll("path.dz-wire.is-on, path.dz-wire.is-off").length,
      graphOpened: !!document.querySelector(".dzr-graph .dz-node"),
      explainRows: document.querySelectorAll(".dzr-explain tbody tr").length,
      explainVerdictCells: document.querySelectorAll(".dzr-explain td.up-t, .dzr-explain td.down-t").length,
      // NOT `chipsInBoard` — that name belongs to the editor's measurement at
      // the top of this file, and reusing it here silently overwrote it with a
      // different page's number.
      resultChipsInBoard: (() => {
        const b = document.querySelector(".dzr-graph .dz-board")?.getBoundingClientRect();
        if (!b) return 0;
        return [...document.querySelectorAll(".dzr-graph .dz-node")].filter((n) => {
          const r = n.getBoundingClientRect();
          return r.left >= b.left - 1 && r.right <= b.right + 1 &&
                 r.top >= b.top - 1 && r.bottom <= b.bottom + 1;
        }).length;
      })(),
      resultNodes: document.querySelectorAll(".dzr-graph .dz-node").length,
    })),
  );

  // The diagram here is a REFERENCE, not a workspace: dragging a chip must move
  // the BOARD (pan), never the chip, because this page has no canvas to save
  // an edit into. Measured on style.left/top — the graph coordinate — because
  // the on-screen box moves either way once the board pans, which is exactly
  // what makes this failure invisible to a bounding-box check.
  const chip = page.locator(".dzr-graph .dz-node").first();
  const coords = () => chip.evaluate((n) => `${n.style.left}|${n.style.top}`);
  const layer = () => page.evaluate(() => document.querySelector(".dzr-graph .dz-layer").style.transform);
  const beforeXY = await coords();
  const beforeLayer = await layer();
  const before = await chip.boundingBox();
  await page.mouse.move(before.x + before.width / 2, before.y + 6);
  await page.mouse.down();
  await page.mouse.move(before.x + before.width / 2 + 70, before.y + 60, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(120);
  out.readonlyChipMoved = (await coords()) !== beforeXY;
  out.readonlyPanned = (await layer()) !== beforeLayer;
  out.readonlySelected = await page.locator(".dzr-graph .dz-node.is-sel").count();
  out.readonlyEdges = await page.locator(".dzr-graph path.dz-wire").count();
  // …and clicking a wire must not cut it.
  const wire = page.locator(".dzr-graph path.dz-wire").first();
  const wb = await wire.boundingBox();
  if (wb) await page.mouse.click(wb.x + wb.width / 2, wb.y + wb.height / 2);
  await page.waitForTimeout(120);
  out.readonlyEdgesAfterClick = await page.locator(".dzr-graph path.dz-wire").count();
}

/* --------------------------------- back to the canvas, with the graph intact */
{
  await page.locator('a[href^="/filter-designer"]').first().click();
  await page.waitForURL(/\/filter-designer(\?|$)/, { timeout: 30000 });
  await page.waitForSelector(".dz-node", { timeout: 30000 });
  await settle();
  out.backNodes = await page.locator(".dz-node").count();
  out.backEditable = await page.locator(".dz-palette .dz-part").count();
  out.backName = (await page.locator(".dz-name b").textContent().catch(() => "")) ?? "";
}

/* ------------------------------------------------------------ zoom / fit */
{
  const pct = () => page.locator(".dz-zoom-pct").first().textContent();
  const before = await pct();
  await page.locator(".dz-zoom .icon-btn").first().click();
  await page.waitForTimeout(120);
  out.zoomChanged = (await pct()) !== before;
}

/* ----------------------------------------------------------- dark theme */
await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
await page.waitForTimeout(180);
Object.assign(
  out,
  await page.evaluate(() => {
    const board = document.querySelector(".dz-board");
    const cat = document.querySelector(".dz-cat");
    // Compare the chips against the PALETTE SWATCHES rather than against a
    // remembered hex: the invariant is "a chip is painted its category's colour
    // in both themes", and pinning one chip's value only held while the check
    // happened to be looking at the same graph it started with.
    const fills = [...new Set([...document.querySelectorAll(".dz-chip")]
      .map((n) => getComputedStyle(n).backgroundColor))];
    const swatches = new Set([...document.querySelectorAll(".dz-swatch")]
      .map((n) => getComputedStyle(n).backgroundColor));
    return {
      darkBoardBg: getComputedStyle(board).backgroundColor,
      darkCatInk: getComputedStyle(cat).color,
      darkFills: fills,
      darkFillsFromPalette: fills.every((f) => swatches.has(f)),
    };
  }),
);

if (shotDir) {
  await page.screenshot({ path: `${shotDir}/designer-dark.png`, fullPage: false });
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
  await page.waitForTimeout(200);
  await page.screenshot({ path: `${shotDir}/designer-light.png`, fullPage: false });
}

/* ------------------------------------------ a big result must be virtualized
   LAST on purpose. It navigates away and loads a different example, so running
   it any earlier would leave every check after it looking at another page —
   which is exactly what happened the first time, and showed up as «بازگشت»
   restoring 12 chips instead of 9. */
{
  // …and it must be VIRTUALIZED. Run a filter that matches hundreds and check
  // that the DOM holds a window of rows rather than all of them — the mistake
  // /screener and /performance already exist to avoid.
  await page.goto(`${baseUrl}/filter-designer`, { waitUntil: "load" });
  await page.waitForSelector(".dz-node");
  await settle();
  await page.locator(".dz-sel", { hasText: "نمونه‌ها" }).locator("select")
    .selectOption({ index: 4 });   // «نزدیک سقف یک‌ساله» — hundreds of matches
  await settle();
  await Promise.all([
    page.waitForURL(/\/filter-designer\/result/, { timeout: 60000 }),
    page.getByRole("button", { name: /اجرا/ }).click(),
  ]);
  await page.waitForSelector(".dz-results .dz-count", { timeout: 180000 });
  await page.waitForTimeout(250);
  Object.assign(out, await page.evaluate(() => {
    const label = document.querySelector(".dz-count")?.textContent ?? "";
    const fa = "۰۱۲۳۴۵۶۷۸۹";
    const n = parseInt([...label.split(" ")[0]].map((c) => {
      const i = fa.indexOf(c);
      return i >= 0 ? String(i) : c;
    }).join("").replace(/\D/g, ""), 10);
    return {
      bigMatched: n,
      bigDomRows: document.querySelectorAll(".dz-results tbody tr[data-ticker]").length,
      bigPads: document.querySelectorAll(".dz-results tbody tr.vpad").length,
      bigCols: document.querySelectorAll(".dz-results colgroup col").length,
      bigHeaders: document.querySelectorAll(".dz-results thead th").length,
    };
  }));
}

await browser.close();
console.log(JSON.stringify(out));
