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
import { getJson, revealIslandFallback } from "./http";

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
        // No star fetch here, unlike the other three islands: /api/scan was
    // SENDING `watched` and nothing on this page ever read it — ScanPanel has
    // no star column, and ScanPayload never even declared the field. So H-1's
    // per-user query is simply gone from this endpoint rather than moved.
    payload = await getJson<ScanPayload>(`/api/scan/${what}/${kind}?${q.toString()}`);
  } catch (err) {
    console.error(`[bn] ${what} data failed to load:`, err);
    revealIslandFallback(err);
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
