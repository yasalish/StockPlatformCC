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
/**
 * Revised with the redesign (ui.css): a percentage inside a data grid is now
 * plain coloured text rather than a filled pill, so each numeric column no
 * longer needs the pill's 18px of horizontal padding. The names and the sector
 * columns lose their generous allowance too — cells truncate with an ellipsis
 * instead of wrapping, which is what used to force a 67px row height.
 */
export const W = {
  symbol: 78,
  name: 150,
  market: 58,
  sector: 96,
  subSector: 110,
  price: 84,
  //  A three-digit return is real («+۲۶۸.۸۴٪» = 9 glyphs), and at 80px the
  //  cell truncated it to «۰۲.۵۵٪…», which is worse than a narrower table:
  //  a number you cannot read is not a column. 9 glyphs x 8px + 20px of
  //  padding.
  period: 92,
  chev: 26,
} as const;
