/**
 * market.ts — the island's entry point.
 *
 * Mounts into the two <div>s market.html provides and nothing else. Jinja still
 * renders the page shell, the nav, the search box, the auth box, the flash
 * messages and the calculator's comparison tables; this file owns two tables
 * and one filter bar. If the bundle fails to load, the page is still a working
 * page — see the server-rendered fallback in market.html.
 */
import { createApp, h } from "vue";
import MarketPanel from "./MarketPanel.vue";
import CalcGrid from "./CalcGrid.vue";
import type { MarketPayload } from "./types";

function readJson<T>(el: HTMLElement, attr: string, fallback: T): T {
  const raw = el.dataset[attr];
  if (!raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

async function boot() {
  const panelEl = document.getElementById("market-panel-app");
  const calcEl = document.getElementById("calc-grid-app");
  if (!panelEl && !calcEl) return;

  const host = (panelEl ?? calcEl)!;
  const kind = (host.dataset.kind ?? "stock") as "stock" | "etf";
  const asOf = host.dataset.asOf ?? "";

  const url = `/api/market/${kind}${asOf ? `?as_of=${encodeURIComponent(asOf)}` : ""}`;
  let payload: MarketPayload;
  try {
    const res = await fetch(url, { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    payload = (await res.json()) as MarketPayload;
  } catch (err) {
    // Leave the server-rendered fallback in place and say why, rather than
    // replacing a working table with an empty one.
    console.error("[bn] market data failed to load:", err);
    document.querySelectorAll<HTMLElement>(".bn-island-fallback").forEach((n) => {
      n.hidden = false;
    });
    return;
  }

  // The fallback markup is only removed once real data is in hand.
  document.querySelectorAll<HTMLElement>(".bn-island-noscript").forEach((n) => n.remove());

  if (panelEl) {
    // Unhide BEFORE mounting. Mounting into a `hidden` element means
    // getBoundingClientRect() returns zeros, so the grid measures its own
    // document offset as 0 — and a table that really starts 46,000px down then
    // renders the wrong rows and makes the browser jump on load.
    panelEl.hidden = false;
    createApp({
      render: () =>
        h(MarketPanel, {
          payload,
          title: panelEl.dataset.title ?? "",
          exportUrl: panelEl.dataset.exportUrl ?? "",
          detailBase: panelEl.dataset.detailBase ?? "",
        }),
    }).mount(panelEl);
  }

  if (calcEl) {
    calcEl.hidden = false;
    createApp({
      render: () =>
        h(CalcGrid, {
          payload,
          cat: calcEl.dataset.cat ?? "",
          pinned: readJson<string[]>(calcEl, "pinned", []),
          detailBase: calcEl.dataset.detailBase ?? "",
        }),
    }).mount(calcEl);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  void boot();
}
