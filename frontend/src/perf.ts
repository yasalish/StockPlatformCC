/**
 * perf.ts — entry point for the «بازدهٔ دوره‌ای» island (performance.html).
 *
 * Same contract as market.ts: Jinja renders the page shell, this owns one
 * region. The first payload is fetched rather than embedded, because it is the
 * megabyte the conversion exists to keep out of the HTML.
 */
import { createApp, h } from "vue";
import PerfPanel from "./PerfPanel.vue";
import type { PerfPayload } from "./types";

async function boot() {
  const host = document.getElementById("perf-app");
  if (!host) return;

  const kind = (host.dataset.kind ?? "stock") as "stock" | "etf";
  // Everything the page was already filtered by — so the island's first render
  // matches the URL the user arrived on, including a bookmarked date range.
  const q = new URLSearchParams(window.location.search);
  q.delete("kind");

  let payload: PerfPayload;
  try {
    const res = await fetch(`/api/performance/${kind}?${q.toString()}`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    payload = (await res.json()) as PerfPayload;
  } catch (err) {
    console.error("[bn] performance data failed to load:", err);
    document.querySelectorAll<HTMLElement>(".bn-island-fallback").forEach((n) => {
      n.hidden = false;
    });
    return;
  }

  // The "loading" line goes only once real data is in hand.
  document.querySelectorAll<HTMLElement>(".bn-island-noscript").forEach((n) => n.remove());

  // Unhide BEFORE mounting: a hidden host measures as zero, and the window
  // virtualizer would then think the table starts at the top of the document
  // and render the wrong slice (the lesson MarketGrid.vue records).
  host.hidden = false;
  createApp({
    render: () =>
      h(PerfPanel, {
        payload,
        detailBaseStock: host.dataset.detailBaseStock ?? "",
        detailBaseEtf: host.dataset.detailBaseEtf ?? "",
      }),
  }).mount(host);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  void boot();
}
