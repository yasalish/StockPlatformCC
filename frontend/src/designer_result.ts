/**
 * designer_result.ts — entry point for /filter-designer/result.
 *
 * The page «اجرا» lands on. It shares the catalogue endpoint with the editor
 * (it needs the node metadata to draw the read-only diagram and to label the
 * explanation rows) and everything else it needs comes from the graph itself —
 * either a saved filter named in the URL, or the draft the canvas left behind.
 */
import { createApp, h } from "vue";
import ResultApp from "./designer/ResultApp.vue";
import type { Catalog } from "./designer/graph";
import { getJson, revealIslandFallback } from "./http";

async function boot() {
  const host = document.getElementById("designer-result");
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
    console.error("[bn] filter result page failed to load:", err);
    revealIslandFallback(err);
    return;
  }

  document.querySelectorAll<HTMLElement>(".bn-island-noscript").forEach((n) => n.remove());
  host.hidden = false;
  createApp({
    render: () =>
      h(ResultApp, {
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
