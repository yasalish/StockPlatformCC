/**
 * widths.ts — the column widths the ORIGINAL page produced.
 *
 * Measured in Chrome at a 1500px viewport against the live dataset, from the
 * server-rendered table before the conversion:
 *
 *   نماد 81 · نام 156 · بازار 62 · گروه 101 · زیرگروه 116 · قیمت پایانی 88 ·
 *   each period column 99 · the chevron 29
 *
 * They have to be stated rather than discovered because a virtualized table
 * only has a handful of rows in the DOM, and the browser would size the columns
 * from those — narrower than the real content needs, and changing as you
 * scrolled. Stating them keeps the layout identical to the old page AND stable.
 */
export const W = {
  symbol: 81,
  name: 156,
  market: 62,
  sector: 101,
  subSector: 116,
  price: 88,
  period: 99,
  chev: 29,
} as const;
