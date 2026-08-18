/** Shapes returned by /api/market/<kind>. Mirrors db.market_gainer() row dicts. */

export interface Row {
  id: number;
  ticker: string;
  name: string;
  market: string | null;
  sector: string | null;
  sub_sector: string | null;
  type: string | null;
  latest: number | null;
  ldate: string | null;
  /** Period gains, keyed p5/p20/… (main table) or d5/d10/… (calculator). */
  [period: string]: unknown;
}

export interface Period {
  key: string;
  n: number;
  label: string;
}

export interface MarketPayload {
  kind: "stock" | "etf";
  as_of: string;
  rows: Row[];
  calc_rows: Row[];
  periods: Period[];
  calc_periods: Period[];
  etf_type_colors: Record<string, string>;
  watched: string[];
  server_ms: number;
}

/* ------------------------------------------------------------------------ *
 * /api/performance/<kind> — the «بازدهٔ دوره‌ای» island.
 *
 * Note what is NOT here: no tops/compare maths. That endpoint returns them
 * already computed, because re-deriving «برترین نماد» in TypeScript would be a
 * second implementation of a number the user reads as authoritative.
 * ------------------------------------------------------------------------ */

/** One period of the wide table. Each renders as a سقف/کف PAIR of columns. */
export interface PerfCol {
  key: string;
  label: string;
  n?: number;
}

export interface PerfTop {
  key: string;
  label: string;
  ticker: string | null;
  gain: number | null;
}

export interface PerfCompareRow {
  label: string;
  yours: number | null;
  top_ticker: string | null;
  top: number | null;
  diff: number | null;
}

export interface PerfPayload {
  kind: "stock" | "etf";
  as_of: string;
  rows: Row[];
  cols: PerfCol[];
  tops: PerfTop[];
  compare: { ticker: string; name: string; latest: number | null } | null;
  comparison: PerfCompareRow[];
  cmp: string;
  groups: string[];
  group: string | null;
  group_label: string;
  subgroups: string[];
  subgroup: string | null;
  markets: string[];
  market: string | null;
  etf_type_colors: Record<string, string>;
  watched: string[];
  server_ms: number;
}

/* ------------------------------------------------------------------------ *
 * /api/scan/<what>/<kind> — the «فیلترها» and «استراتژی‌ها» islands.
 *
 * Normalised on purpose: a symbol matches several sections, so the symbols are
 * sent once and the sections carry ids into them.
 * ------------------------------------------------------------------------ */
export interface ScanRow {
  id: number;
  ticker: string;
  name: string;
  group: string | null;
  latest: number | null;
  rsi: number | null;
  /** ⭐ picks only. */
  score?: number | null;
  signals?: string[];
}

export interface ScanSectionMeta {
  key: string;
  name: string;
  desc?: string;
  source?: string;
  /** filters only: "up" | "down" | "flat", and the category it belongs to. */
  dir?: string;
  cat?: string;
  short?: string;
  ids: number[];
}

export interface ScanPayload {
  what: "filters" | "strategies";
  kind: "stock" | "etf";
  as_of: string;
  scanned: number;
  count: number;
  sections: ScanSectionMeta[];
  symbols: Record<string, ScanRow>;
  groups: string[];
  group: string | null;
  group_label: string;
  subgroups: string[];
  subgroup: string | null;
  categories?: { key: string; name: string }[];
  picks?: { id: number; score: number | null; signals: string[] }[];
  strat_names?: Record<string, string>;
  server_ms: number;
}

/* ---------------- /api/screener/<kind> — the «غربالگر هوشمند» island ------- */
export interface ScreenerRow {
  id: number;
  ticker: string;
  name: string;
  group: string | null;
  latest: number | null;
  score: number;
  verdict: { key: string; label: string; tone: string; score: number };
  trend: number | null;
  momentum: number | null;
  rsi: number | null;
}

export interface ScreenerPayload {
  kind: "stock" | "etf";
  as_of: string;
  scanned: number;
  count: number;
  rows: ScreenerRow[];
  verdict: string | null;
  bands: { min: number; key: string; label: string; tone: string }[];
  groups: string[];
  group: string | null;
  group_label: string;
  subgroups: string[];
  subgroup: string | null;
  etf_type_colors: Record<string, string>;
  watched: string[];
  server_ms: number;
}

/** A column of the grid, described as data so both tables share one component. */
export interface ColumnSpec {
  id: string;
  label: string;
  /** Matches the existing data-sort attribute: "str", "num", or none. */
  sort?: "str" | "num";
  /** thead gets class="numh" (centred) as the Jinja version does. */
  numh?: boolean;
  /**
   * Explicit width in px. Required, because virtualization forces the issue:
   * with the browser's automatic table layout the column widths are computed
   * from the rows that are PRESENT, so rendering 19 of 742 rows produces
   * different widths — and they would shift again on every scroll. These
   * values are the widths the un-virtualized page actually produced, measured
   * in the browser (see verify_order08.py).
   */
  width: number;
  /** Cell renderer kind — keeps the markup identical to the Jinja partials. */
  cell:
    | { type: "symbol" }
    | { type: "name" }
    | { type: "tag" }          // ETF type, coloured pill
    | { type: "muted"; field: "market" | "sector" | "sub_sector" }
    | { type: "price" }
    | { type: "pill"; field: string }
    | { type: "chev" };
}
