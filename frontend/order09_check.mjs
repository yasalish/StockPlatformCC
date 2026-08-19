/**
 * order09_check.mjs — browser-side verification of order 09: the mirrored table
 * scrollbars, the six themes, the تنظیمات screen, نقشهٔ بازار and نبض بازار.
 *
 *   node order09_check.mjs <baseUrl> <user> <pass> <shotsDir>
 *
 * Prints one JSON object on stdout; verify_order09.py turns it into checks.
 * Everything asserted here is a thing a user can see — a scrollbar that scrolls
 * the table, a theme that survives a reload — because that is the only kind of
 * claim a screenshot cannot argue with.
 */
import { chromium } from "playwright-core";

const [, , baseUrl, user, pass, shotsDir] = process.argv;

function launch() {
  // --no-proxy-server: this machine runs a local proxy client that intercepts
  // 127.0.0.1 and answers 503, so the browser must be told to go direct.
  // HEADED=1 runs a visible browser. It matters for one check: headless Chrome
// draws OVERLAY scrollbars, which ignore ::-webkit-scrollbar sizing entirely,
// so the mirrored bar's real thickness can only be measured in a headed run —
// the one the user's own Chrome will do.
const opts = { headless: !process.env.HEADED, args: ["--no-proxy-server", "--disable-dev-shm-usage"] };
  for (const channel of ["chrome", "msedge"]) {
    try {
      return chromium.launch({ ...opts, channel });
    } catch {
      /* next */
    }
  }
  return chromium.launch(opts);
}

const out = { errors: [], pages: {}, shots: [], headed: !!process.env.HEADED };
const browser = await launch();
const ctx = await browser.newContext({ viewport: { width: 1500, height: 1000 }, locale: "fa-IR" });
const page = await ctx.newPage();
page.on("pageerror", (e) => out.errors.push(String(e)));
page.on("console", (m) => {
  if (m.type() !== "error") return;
  const loc = m.location() || {};
  // The URL matters more than the message: "failed to load resource" without it
  // is untraceable, and the browser reports it on the page, not the request.
  out.errors.push("console: " + m.text() + " @ " + (loc.url || page.url()));
});
out.failedRequests = [];
page.on("response", (r) => {
  if (r.status() >= 400) out.failedRequests.push(r.status() + " " + r.url());
});

async function shot(name) {
  const file = `${shotsDir}/${name}.png`;
  await page.screenshot({ path: file, fullPage: false });
  out.shots.push(file);
}

// --- log in ---------------------------------------------------------------
await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
await page.fill('input[name="username"]', user);
await page.fill('input[name="password"]', pass);
await Promise.all([
  page.waitForNavigation({ waitUntil: "domcontentloaded" }),
  page.click('button[type="submit"], input[type="submit"]'),
]);

// --- the table pages ------------------------------------------------------
// Each one is measured for: how many scrollers exist, how many mirror bars sit
// above them (exactly one per scroller — two would mean the island's own bar
// came back), whether the mirror actually drives the table, and how thick the
// bar is.
async function inspect(path, waitFor) {
  await page.goto(`${baseUrl}${path}`, { waitUntil: "load", timeout: 120000 });
  if (waitFor) await page.waitForSelector(waitFor, { timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(900);          // islands mount, columns settle

  const info = await page.evaluate(() => {
    const scrollers = Array.from(document.querySelectorAll(".table-scroll"));
    const bars = Array.from(document.querySelectorAll(".tbl-bar"));
    const mirrors = Array.from(document.querySelectorAll(".table-scroll-top"));
    const wide = scrollers.filter((s) => s.scrollWidth > s.clientWidth + 2);
    const visibleMirrors = mirrors.filter((m) => !m.hidden);
    const barBeforeScroller = bars.filter(
      (b) => b.nextElementSibling && b.nextElementSibling.classList.contains("table-scroll"));
    return {
      scrollers: scrollers.length,
      bars: bars.length,
      mirrors: mirrors.length,
      wide: wide.length,
      visibleMirrors: visibleMirrors.length,
      barsAboveATable: barBeforeScroller.length,
      // The mirror must be sticky and positioned under the site header, or it
      // scrolls away with the table it is supposed to control.
      sticky: bars.length ? getComputedStyle(bars[0]).position : null,
      sbarHeight: getComputedStyle(document.documentElement).getPropertyValue("--sbar-h").trim(),
      topbarVar: getComputedStyle(document.documentElement).getPropertyValue("--topbar-h").trim(),
      tools: document.querySelectorAll(".tbl-tools .tbl-btn").length,
      // The RENDERED height of the mirror, not the CSS variable. A variable set
      // to 20px proves nothing: setting `scrollbar-color` alongside the
      // ::-webkit-scrollbar rules made Chrome ignore the height entirely and
      // the bar measured 5px on screen while every CSS check still passed.
      mirrorHeight: (() => {
        const m = document.querySelector(".table-scroll-top:not([hidden])");
        return m ? Math.round(m.getBoundingClientRect().height) : null;
      })(),
      // offsetHeight − clientHeight IS the scrollbar: the space it takes out of
      // the element. 0 means the engine drew an overlay scrollbar (headless
      // Chrome, macOS, a trackpad on Firefox) rather than a classic one.
      scrollbarPx: (() => {
        const m = document.querySelector(".table-scroll-top:not([hidden])");
        return m ? m.offsetHeight - m.clientHeight : null;
      })(),
      // Regression guard for a bug this probe caught: a sticky <thead> inside a
      // horizontally-scrolling container is offset from THAT container, not the
      // viewport, so a non-zero `top` printed the header over the first rows of
      // data. The header must start above the first body row.
      headAboveBody: (() => {
        const t = document.querySelector("table.grid tbody tr td");
        const h = document.querySelector("table.grid thead th");
        if (!t || !h) return null;
        return h.getBoundingClientRect().top <= t.getBoundingClientRect().top + 1;
      })(),
      headStickyTop: (() => {
        const h = document.querySelector("table.grid thead th");
        return h ? getComputedStyle(h).top : null;
      })(),
      theme: document.documentElement.getAttribute("data-theme"),
      bodyBg: getComputedStyle(document.body).backgroundColor,
    };
  });

  // Move the mirror and see whether the table under it moved. This is the whole
  // feature; everything else on this page is decoration around it.
  //
  // Two things this has to get right. In an RTL container scrollLeft is 0 at the
  // right edge and NEGATIVE going left, so adding to it is clamped back to 0 and
  // proves nothing — the direction is read off the element instead of assumed.
  // And no synthetic event is dispatched: setting scrollLeft makes the browser
  // fire a real `scroll`, which is the listener tables.js actually installs, so
  // waiting for it is what tests the real path.
  info.sync = await page.evaluate(() => {
    const bar = document.querySelector(".tbl-bar");
    if (!bar) return null;
    const mirror = bar.querySelector(".table-scroll-top");
    const scroller = bar.nextElementSibling;
    if (!mirror || mirror.hidden || !scroller) return null;
    const rtl = getComputedStyle(scroller).direction === "rtl";
    const before = scroller.scrollLeft;
    window.__bnBefore = before;
    mirror.scrollLeft = before + (rtl ? -240 : 240);
    return { rtl, before, mirrorAfterSet: mirror.scrollLeft };
  });
  await page.waitForTimeout(200);
  Object.assign(info.sync || {}, await page.evaluate(() => {
    const bar = document.querySelector(".tbl-bar");
    const mirror = bar && bar.querySelector(".table-scroll-top");
    const scroller = bar && bar.nextElementSibling;
    if (!mirror || !scroller) return {};
    const after = scroller.scrollLeft;
    // …and the other way round: moving the TABLE must move the mirror back.
    scroller.scrollLeft = window.__bnBefore;
    return { after, moved: Math.abs(after - window.__bnBefore) > 50 };
  }));
  await page.waitForTimeout(200);
  Object.assign(info.sync || {}, await page.evaluate(() => {
    const bar = document.querySelector(".tbl-bar");
    const mirror = bar && bar.querySelector(".table-scroll-top");
    if (!mirror) return {};
    return { mirrorFollows: Math.abs(mirror.scrollLeft - window.__bnBefore) < 50 };
  }));

  out.pages[path] = info;
  return info;
}

await inspect("/stocks", "#market-panel-app table");
await shot("stocks-light");
await inspect("/performance", "#perf-app table");
await shot("performance-light");
await inspect("/screener", "#screener-app table");
await inspect("/watchlist", null);
await inspect("/dashboard", ".breadth-bar");

// --- نبض بازار ------------------------------------------------------------
out.breadth = await page.evaluate(() => {
  const bar = document.querySelector(".breadth-bar");
  if (!bar) return null;
  const seg = (cls) => {
    const el = bar.querySelector("." + cls);
    return el ? el.getBoundingClientRect().width : 0;
  };
  return {
    present: true,
    up: seg("b-up"), down: seg("b-down"),
    legend: document.querySelectorAll(".breadth-legend span").length,
    lists: document.querySelectorAll(".mini-list a").length,
    groups: document.querySelectorAll(".mini-list .chip").length,
  };
});
await shot("dashboard-light");

// --- نقشهٔ بازار ----------------------------------------------------------
await page.goto(`${baseUrl}/heatmap`, { waitUntil: "load", timeout: 120000 });
await page.waitForSelector(".hm-tile", { timeout: 60000 }).catch(() => {});
out.heatmap = await page.evaluate(() => {
  const tiles = Array.from(document.querySelectorAll(".hm-tile"));
  const colours = new Set(tiles.slice(0, 200).map((t) => getComputedStyle(t).backgroundColor));
  const grows = tiles.slice(0, 200).map((t) => parseFloat(getComputedStyle(t).flexGrow));
  return {
    tiles: tiles.length,
    groups: document.querySelectorAll(".hm-group").length,
    distinctColours: colours.size,
    // Tiles must not all be the same size: that is the difference between a
    // heat map and a grid of squares.
    distinctSizes: new Set(grows).size,
    links: tiles.filter((t) => t.getAttribute("href")).length,
  };
});
await shot("heatmap-light");

// filter box narrows the map
await page.fill("#hm-filter", "فولاد").catch(() => {});
await page.waitForTimeout(350);
out.heatmapFiltered = await page.evaluate(() => document.querySelectorAll(".hm-tile").length);

// --- تنظیمات: pick a dark theme and make sure it survives a reload ---------
await page.goto(`${baseUrl}/settings`, { waitUntil: "load", timeout: 120000 });
out.settings = await page.evaluate(() => ({
  swatches: document.querySelectorAll("[data-theme-id]").length,
  switches: document.querySelectorAll(".switch input").length,
  segments: document.querySelectorAll(".seg").length,
  selects: document.querySelectorAll("select[data-pref]").length,
  active: document.querySelectorAll(".theme-swatch.active").length,
}));
await shot("settings-light");

await page.click('[data-theme-id="midnight"]');
await page.waitForTimeout(600);
out.afterClick = await page.evaluate(() => ({
  theme: document.documentElement.getAttribute("data-theme"),
  bodyBg: getComputedStyle(document.body).backgroundColor,
  panelBg: getComputedStyle(document.querySelector(".panel")).backgroundColor,
  ink: getComputedStyle(document.body).color,
}));
await shot("settings-midnight");

await page.reload({ waitUntil: "load" });
await page.waitForTimeout(400);
out.afterReload = await page.evaluate(() => ({
  theme: document.documentElement.getAttribute("data-theme"),
  bodyBg: getComputedStyle(document.body).backgroundColor,
  // The pre-paint script must have run before the stylesheet painted: if the
  // attribute is on <html> at DOMContentLoaded there is no flash.
  fromServer: document.documentElement.getAttribute("data-prefs"),
}));

// the dark theme on a real table page — the one that matters for legibility
await page.goto(`${baseUrl}/stocks`, { waitUntil: "load", timeout: 120000 });
await page.waitForSelector("#market-panel-app table", { timeout: 60000 }).catch(() => {});
await page.waitForTimeout(800);
out.darkTable = await page.evaluate(() => {
  const cell = document.querySelector("table.grid tbody td");
  const pill = document.querySelector(".pill");
  return {
    theme: document.documentElement.getAttribute("data-theme"),
    bodyBg: getComputedStyle(document.body).backgroundColor,
    cellInk: cell ? getComputedStyle(cell).color : null,
    pillInk: pill ? getComputedStyle(pill).color : null,
    pillBg: pill ? getComputedStyle(pill).backgroundColor : null,
    // A control in the PAGE, not the header search box: that one is a
    // translucent white over the dark header in every theme, so it would pass
    // this check without proving anything.
    inputBg: (() => {
      const el = document.querySelector(".container input[type=text], .container select");
      return el ? getComputedStyle(el).backgroundColor : null;
    })(),
  };
});
await shot("stocks-midnight");

// --- the symbol page: rows_per_page and the sticky header -----------------
// This is the only screen where «تعداد ردیف در هر صفحه» and «سرستون چسبان» have
// anything to act on (the OHLCV history is the app's one vertically-scrolling
// table), so a claim that either setting does something can only be tested here.
await page.goto(`${baseUrl}/stocks`, { waitUntil: "load", timeout: 120000 });
await page.waitForSelector("#market-panel-app table tbody .watch-star[data-id]", { timeout: 60000 }).catch(() => {});
// The island's rows navigate on click rather than wrapping their cells in an
// <a> (MarketGrid.vue: `go()` sets location.href), and the row's own id lives
// on its دیده‌بان star — which is where this reads it from.
const symbolHref = await page.evaluate(() => {
  const star = document.querySelector("#market-panel-app table tbody .watch-star[data-id]");
  return star ? "/stock/" + star.getAttribute("data-id") : null;
});
if (symbolHref) {
  await page.goto(baseUrl + symbolHref, { waitUntil: "load", timeout: 120000 });
  await page.click('[data-tab="history"]').catch(() => {});
  await page.waitForSelector(".hist-table tbody tr", { timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(500);
  out.detailTabs = await page.evaluate(() => {
    // Every table on this page must be in a scroll container with a bar above
    // it — the returns tables and the key/value tables included, which were the
    // last ones in the app that were not.
    const scrollers = document.querySelectorAll(".table-scroll");
    const bars = document.querySelectorAll(".tbl-bar");
    const bare = Array.from(document.querySelectorAll("table.grid"))
      .filter((t) => !t.closest(".table-scroll") && !t.closest(".hist-scroll")).length;
    return { scrollers: scrollers.length, bars: bars.length, bareTables: bare };
  });
  out.detail = await page.evaluate(() => {
    const th = document.querySelector(".hist-table thead th");
    return {
      rows: document.querySelectorAll(".hist-table tbody tr").length,
      stickyHead: th ? getComputedStyle(th).position : null,
      recents: document.querySelectorAll("#bn-recents a").length,
      hasCsv: !!document.querySelector('[data-role="csv"]'),
    };
  });
  // …and the visit is remembered, which is what «بازدیدهای اخیر» is
  await page.goto(`${baseUrl}/dashboard`, { waitUntil: "load", timeout: 120000 });
  await page.waitForTimeout(400);
  out.recentsAfterVisit = await page.evaluate(() =>
    document.querySelectorAll("#bn-recents a").length);
}

// --- the other preferences, exercised through the real controls -----------
await page.goto(`${baseUrl}/settings`, { waitUntil: "load" });
// Click the LABEL, not the input: the radio itself is visually hidden (the
// segmented control is drawn by its label), which is exactly how a real user
// operates it — and it is what a screen reader still sees as a radio group.
async function pick(key, value) {
  await page.click(`.seg label:has(input[data-pref="${key}"][value="${value}"])`);
  await page.waitForTimeout(150);
}
await pick("scrollbar_size", "xl");
await pick("density", "compact");
await pick("digits", "en");
await page.waitForTimeout(500);
out.prefsApplied = await page.evaluate(() => ({
  sbar: getComputedStyle(document.documentElement).getPropertyValue("--sbar-h").trim(),
  density: document.documentElement.getAttribute("data-density"),
  digits: document.documentElement.getAttribute("data-digits"),
  persianDigitsLeft: /[۰-۹]/.test(document.body.innerText),
}));

// …and that they survive onto another page, from the database
await page.goto(`${baseUrl}/stocks`, { waitUntil: "load", timeout: 120000 });
await page.waitForSelector("#market-panel-app table", { timeout: 60000 }).catch(() => {});
await page.waitForTimeout(900);
out.prefsCarried = await page.evaluate(() => {
  const td = document.querySelector("table.grid tbody td");
  return {
    sbar: getComputedStyle(document.documentElement).getPropertyValue("--sbar-h").trim(),
    density: document.documentElement.getAttribute("data-density"),
    cellPadding: td ? getComputedStyle(td).paddingTop : null,
    // The islands render AFTER the digit rewrite starts: if the observer is
    // working, a virtualized table full of numbers still has no Persian digit.
    persianDigitsInTable: /[۰-۹]/.test(document.querySelector("table.grid").innerText),
  };
});
await shot("stocks-compact-latin");

// put the account back the way it was, so a re-run starts from the defaults
await page.goto(`${baseUrl}/settings`, { waitUntil: "load" });
await page.evaluate(() => fetch("/api/me/prefs/reset", { method: "POST" }));
await page.waitForTimeout(300);

await browser.close();
process.stdout.write(JSON.stringify(out, null, 1));
