/**
 * designer.ts — entry point for the /filter-designer island.
 *
 * Fetches the node catalogue once (it is static per kind, ~30 KB) and mounts the
 * editor. Everything after that is POSTs of the graph the user drew.
 */
import { createApp, h } from "vue";
import DesignerApp from "./designer/DesignerApp.vue";
import type { Catalog } from "./designer/graph";
import { getJson, revealIslandFallback } from "./http";

async function boot() {
  const host = document.getElementById("designer-app");
  if (!host) return;

  const kind = host.dataset.kind === "etf" ? "etf" : "stock";

  let catalog: Catalog;
  try {
    catalog = await getJson<Catalog>(`/api/designer/catalog?kind=${kind}`);
  } catch (err) {
    console.error("[bn] filter designer failed to load:", err);
    revealIslandFallback(err);
    return;
  }

  document.querySelectorAll<HTMLElement>(".bn-island-noscript").forEach((n) => n.remove());
  host.hidden = false;
  createApp({
    render: () =>
      h(DesignerApp, {
        catalog,
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
