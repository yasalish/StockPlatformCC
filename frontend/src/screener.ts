/**
 * screener.ts — entry point for the «غربالگر هوشمند» island (screener.html).
 * Same contract as the other three: Jinja renders the shell, this owns the
 * filter bar, the summary cards and the ranked table.
 */
import { createApp, h } from "vue";
import ScreenerPanel from "./ScreenerPanel.vue";
import type { ScreenerPayload } from "./types";
import { getJson, fetchWatched, revealIslandFallback } from "./http";

async function boot() {
  const host = document.getElementById("screener-app");
  if (!host) return;

  const kind = (host.dataset.kind ?? "stock") as "stock" | "etf";
  const q = new URLSearchParams(window.location.search);
  q.delete("kind");

  let payload: ScreenerPayload;
  try {
        // H-1. The stars are their own request now, in PARALLEL with this
    // one, so the shared payload stays a document nginx can cache and
    // hand to every user. Promise.all rather than sequentially: the two
    // are independent and the page should not wait for both in series.
    const [main, watched] = await Promise.all([
      getJson<ScreenerPayload>(`/api/screener/${kind}?${q.toString()}`),
      fetchWatched(),
    ]);
    // Merged in so every panel's `payload.watched` keeps working with no
    // change; fetchWatched() cannot throw, so this cannot fail the table.
    payload = { ...main, watched };
  } catch (err) {
    console.error("[bn] screener data failed to load:", err);
    revealIslandFallback(err);
    return;
  }

  document.querySelectorAll<HTMLElement>(".bn-island-noscript").forEach((n) => n.remove());
  host.hidden = false;
  createApp({
    render: () =>
      h(ScreenerPanel, {
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
