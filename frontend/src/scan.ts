/**
 * scan.ts — entry point for the /filters and /strategies islands.
 *
 * One bundle for both pages: they are the same shape (N named sections of
 * matching symbols) and differ only in metadata, which the payload carries.
 * The mount element says which one it is via data-what.
 */
import { createApp, h } from "vue";
import ScanPanel from "./ScanPanel.vue";
import type { ScanPayload } from "./types";

async function boot() {
  const host = document.getElementById("scan-app");
  if (!host) return;

  const what = host.dataset.what === "strategies" ? "strategies" : "filters";
  const kind = (host.dataset.kind ?? "stock") as "stock" | "etf";
  const q = new URLSearchParams(window.location.search);
  // Display-only choices; the payload holds every section already.
  q.delete("kind");
  q.delete("filter");
  q.delete("strategy");
  q.delete("cat");

  let payload: ScanPayload;
  try {
    const res = await fetch(`/api/scan/${what}/${kind}?${q.toString()}`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    payload = (await res.json()) as ScanPayload;
  } catch (err) {
    console.error(`[bn] ${what} data failed to load:`, err);
    document.querySelectorAll<HTMLElement>(".bn-island-fallback").forEach((n) => {
      n.hidden = false;
    });
    return;
  }

  document.querySelectorAll<HTMLElement>(".bn-island-noscript").forEach((n) => n.remove());
  host.hidden = false;
  createApp({
    render: () =>
      h(ScanPanel, {
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
