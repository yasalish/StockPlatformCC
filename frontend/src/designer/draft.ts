/**
 * draft.ts — the handoff between /filter-designer and /filter-designer/result.
 *
 * Pressing «اجرا» is a real page navigation now, so the graph has to survive the
 * trip. It travels through localStorage rather than through the server or the
 * URL, for three reasons:
 *
 *   · a real graph is several kilobytes of JSON — too big for a query string and
 *     too big for a signed session cookie;
 *   · the alternative, a server-side token, means an expiry, a store to put it
 *     in, and a "this result has expired" screen for a user who left the tab
 *     open over lunch. The draft has no expiry and belongs to the browser that
 *     drew it;
 *   · the designer was ALREADY writing this key on every edit so that unsaved
 *     work survives a reload. Running now reads the same record, which means
 *     what runs is exactly what is on the canvas, with no second serialisation
 *     that could drift from the first.
 *
 * A SAVED filter does not need any of this: /filter-designer/result?filter=<id>
 * loads the graph from the database, which is what makes that URL bookmarkable
 * and worth sending to someone else. The draft is the fallback for a graph that
 * exists nowhere but this browser.
 */
import type { Graph } from "./graph";

export const DRAFT_KEY = "boursenegar-designer-draft";

export interface Draft {
  graph: Graph;
  kind: "stock" | "etf";
  name: string;
  id: number | null;
  group: string;
  subgroup: string;
}

/** Read the draft, or null when there is none / it is unreadable. */
export function loadDraft(): Draft | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const d = JSON.parse(raw) as Partial<Draft>;
    if (!d || !d.graph || !Array.isArray(d.graph.nodes)) return null;
    return {
      graph: d.graph as Graph,
      kind: d.kind === "etf" ? "etf" : "stock",
      name: d.name ?? "",
      id: typeof d.id === "number" ? d.id : null,
      group: d.group ?? "",
      subgroup: d.subgroup ?? "",
    };
  } catch {
    // Private browsing throws on the read itself. A missing draft is a normal
    // state (a first visit), never an error worth showing.
    return null;
  }
}

export function saveDraft(d: Draft): void {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(d));
  } catch {
    /* quota or private mode — the canvas still works, it just will not survive */
  }
}

/** The URL «اجرا» goes to, and the one the results page rewrites as the scope
 *  changes. `filter` is present only for a graph that exists in the database. */
export function resultUrl(o: {
  id?: number | null;
  kind: string;
  group?: string;
  subgroup?: string;
}): string {
  const q = new URLSearchParams();
  if (o.id) q.set("filter", String(o.id));
  if (o.kind && o.kind !== "stock") q.set("kind", o.kind);
  if (o.group) q.set("group", o.group);
  if (o.subgroup) q.set("subgroup", o.subgroup);
  const s = q.toString();
  return "/filter-designer/result" + (s ? `?${s}` : "");
}

/** The way back to the canvas — carrying the saved filter's id when there is
 *  one, so «بازگشت» reopens THAT filter rather than whatever the draft holds. */
export function designerUrl(id?: number | null): string {
  return "/filter-designer" + (id ? `?filter=${id}` : "");
}
