/**
 * graph.test.ts — the filter-designer graph model (review finding M-5)
 *
 * The review names this module alongside format.ts as "pure and cheap to
 * cover". It is also the model behind user-authored logic that the server then
 * walks as data, so a bug here produces a filter that silently means something
 * other than what the canvas shows.
 *
 * rekey() gets the most attention because it is the one function whose failure
 * mode is silent: an edge that survives with a stale endpoint id does not throw,
 * it just quietly disconnects part of the graph.
 */
import { describe, expect, it } from "vitest";
import { clipCaption, newId, rekey, valueAt, type Graph } from "./graph";

function graph(): Graph {
  return {
    nodes: [
      { id: "a", type: "price", x: 0, y: 0, params: { field: "close" } },
      { id: "b", type: "sma", x: 100, y: 0, params: { n: 20 } },
      { id: "c", type: "gt", x: 200, y: 0, params: {} },
    ],
    edges: [
      { from: "a", to: "b", fromPort: "out", toPort: "src" },
      { from: "b", to: "c", fromPort: "out", toPort: "left" },
    ],
  } as unknown as Graph;
}

describe("valueAt — reading back down a series", () => {
  const tail = [1, 2, 3, 4, 5]; // oldest → newest

  it("0 is the LAST element, not the first", () => {
    expect(valueAt(tail, 0)).toBe(5);
    expect(valueAt(tail, 1)).toBe(4);
    expect(valueAt(tail, 4)).toBe(1);
  });

  it("clamps past the start rather than returning undefined", () => {
    // A `close-99` node on a 5-bar series must read the oldest bar, not blow a
    // hole in the middle of an expression.
    expect(valueAt(tail, 99)).toBe(1);
  });

  it("returns undefined for an empty series", () => {
    expect(valueAt([], 0)).toBeUndefined();
  });
});

describe("newId", () => {
  it("never repeats, even called in a tight loop", () => {
    // Date.now() has millisecond resolution, so the sequence counter is what
    // actually guarantees this. A collision would silently merge two nodes.
    const ids = new Set(Array.from({ length: 2000 }, () => newId()));
    expect(ids.size).toBe(2000);
  });

  it("honours the prefix", () => {
    expect(newId("z").startsWith("z")).toBe(true);
  });
});

describe("rekey — pasting a graph into another one", () => {
  it("gives every node a fresh id", () => {
    const g = rekey(graph());
    const ids = g.nodes.map((n) => n.id);
    expect(new Set(ids).size).toBe(3);
    expect(ids).not.toContain("a");
    expect(ids).not.toContain("b");
    expect(ids).not.toContain("c");
  });

  it("rewrites edge endpoints to the NEW ids, keeping the wiring", () => {
    const g = rekey(graph());
    const [a, b, c] = g.nodes.map((n) => n.id);
    expect(g.edges).toHaveLength(2);
    expect(g.edges[0]).toMatchObject({ from: a, to: b, fromPort: "out", toPort: "src" });
    expect(g.edges[1]).toMatchObject({ from: b, to: c, fromPort: "out", toPort: "left" });
  });

  it("drops edges whose endpoints are not in the graph", () => {
    // A hand-edited or truncated saved filter can carry a dangling edge. Keeping
    // it would produce an edge pointing at nothing.
    const g = graph();
    (g.edges as unknown[]).push({ from: "a", to: "MISSING", fromPort: "out", toPort: "x" });
    expect(rekey(g).edges).toHaveLength(2);
  });

  it("does not mutate the input", () => {
    const g = graph();
    const before = JSON.stringify(g);
    rekey(g);
    expect(JSON.stringify(g)).toBe(before);
  });

  it("copies params rather than sharing them", () => {
    // Shared param objects meant editing the pasted copy also edited the
    // original — the classic shallow-clone bug in a paste path.
    const src = graph();
    const out = rekey(src);
    (out.nodes[1].params as Record<string, unknown>).n = 50;
    expect(src.nodes[1].params.n).toBe(20);
  });

  it("handles an empty graph", () => {
    expect(rekey({ nodes: [], edges: [] } as Graph)).toEqual({ nodes: [], edges: [] });
  });
});

describe("clipCaption", () => {
  it("leaves a short caption alone", () => {
    expect(clipCaption("SMA 20")).toBe("SMA 20");
  });

  it("ends a clipped caption with a single ellipsis character", () => {
    const long = "x".repeat(200);
    const out = clipCaption(long);
    expect(out.length).toBeLessThan(long.length);
    expect(out.endsWith("…")).toBe(true);
    // One U+2026, not three dots — it has to fit the chip's measured width.
    expect(out.includes("...")).toBe(false);
  });
});
