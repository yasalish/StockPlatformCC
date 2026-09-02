/**
 * designer_backtest.ts — entry point for /filter-backtest.
 *
 * The third designer page. Boots exactly like designer_result.ts — it needs the
 * catalogue for the node metadata (the read-only diagram labels its chips from
 * it) and takes the graph itself from a saved filter or from the canvas draft.
 */
import { createApp, h } from "vue";
import BacktestApp from "./designer/BacktestApp.vue";
import type { Catalog } from "./designer/graph";
import { getJson, revealIslandFallback } from "./http";

async function boot() {
  const host = document.getElementById("designer-backtest");
  if (!host) return;

  const q = new URLSearchParams(window.location.search);
  const kind = q.get("kind") === "etf" ? "etf" : "stock";
  const group = q.get("group") ?? "";
  const rawId = Number(host.dataset.filterId || q.get("filter") || 0);
  const filterId = Number.isFinite(rawId) && rawId > 0 ? rawId : null;

  let catalog: Catalog;
  try {
    const cq = new URLSearchParams({ kind });
    if (group) cq.set("group", group);
    catalog = await getJson<Catalog>(`/api/designer/catalog?${cq}`);
  } catch (err) {
    console.error("[bn] filter backtest page failed to load:", err);
    revealIslandFallback(err);
    return;
  }

  document.querySelectorAll<HTMLElement>(".bn-island-noscript").forEach((n) => n.remove());
  host.hidden = false;
  createApp({
    render: () =>
      h(BacktestApp, {
        catalog,
        filterId,
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
