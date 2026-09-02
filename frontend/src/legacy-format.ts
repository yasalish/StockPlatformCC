/**
 * legacy-format.ts — one formatting implementation for BOTH front ends (M-2)
 *
 * THE PROBLEM THIS SOLVES
 *
 * Persian number formatting existed in three places:
 *
 *   frontend/src/format.ts        the TypeScript one, used by the Vue islands
 *   static/js/app.js:5            `faDigits` / `fmt`, in the BN IIFE
 *   static/js/app.js:251          `FA_DIGITS` again, a second copy in the same
 *                                 file for the SVG chart
 *
 * — and a fourth, authoritative one in Python (db.to_persian). A rounding or
 * grouping bug therefore had to be found and fixed four times, and the review
 * is right that they will drift: they already had, since app.js's `fmt` rounds
 * to an integer while format.ts's `fa` prints two decimals for a non-integral
 * value.
 *
 * WHY A GLOBAL RATHER THAN AN ES IMPORT
 *
 * The review's fix says "a plain ES module the vanilla pages import". That
 * would mean turning app.js into `<script type="module">`, which changes its
 * load semantics — modules are deferred, so `BN` would stop existing at the
 * moment the inline scripts in nine templates call it, and every one of those
 * would have to move. That is the island conversion, page by page, which is a
 * separate job and the same one the review recommends finishing.
 *
 * This is the smaller change that removes the DRIFT now: format.ts stays the
 * single implementation, this entry publishes it on `window.BN_FORMAT`, and
 * app.js delegates to it. One source of truth, no change to how any existing
 * script loads. When the islands do finish, this file is deleted and the
 * imports become real.
 */
import { fa, fy, pill, toFaDigits } from "./format";

declare global {
  interface Window {
    BN_FORMAT?: {
      toFaDigits: typeof toFaDigits;
      fa: typeof fa;
      fy: typeof fy;
      pill: typeof pill;
    };
  }
}

window.BN_FORMAT = { toFaDigits, fa, fy, pill };
