/**
 * nav.ts — opening a security's detail page from a table row.
 *
 * Every grid on the platform used `window.location.href = …`, which replaces
 * the table the user is working in. Looking at six symbols meant six round
 * trips through the list, and on the market and performance pages that list is
 * a filtered, sorted, scrolled view that the back button rebuilds from the top.
 * The rows now open in a NEW TAB, so the list stays exactly where it was.
 *
 * Two paths reach here, and they must not both fire for one click:
 *
 *   · the ticker and the name are real <a target="_blank" rel="noopener">
 *     elements, so hovering shows the URL, ctrl/cmd- and middle-click do the
 *     native thing, and the keyboard can reach them. Their own handler stops
 *     the event before the row sees it;
 *   · a click anywhere ELSE in the row lands on the row handler, which calls
 *     openDetail() below.
 */

/** The row-level click: same destination as the anchors, same new tab. */
export function openDetail(href: string) {
  if (!href) return;
  // `noopener` cannot be passed as a feature here: a window opened with it
  // returns null, which is indistinguishable from being blocked. Severing
  // `opener` afterwards is the equivalent, and it leaves the return value
  // meaningful — so a browser that refuses the tab falls back to navigating
  // rather than swallowing the click and looking broken.
  const win = window.open(href, "_blank");
  if (win) win.opener = null;
  else window.location.href = href;
}

/**
 * True when a row click should be ignored because something inside the row has
 * already handled it: the watchlist star, or one of the anchors above (whose
 * default action is the new tab we would otherwise open a second time).
 */
export function handledInRow(event: MouseEvent) {
  const el = event.target as HTMLElement | null;
  return !!el?.closest("a, button, .watch-star");
}
