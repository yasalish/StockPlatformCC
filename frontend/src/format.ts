/**
 * format.ts — a faithful port of db.py's Persian number helpers.
 *
 * These are the single most dangerous lines in the whole conversion. Every
 * number on the page goes through them, and a formatting difference is not a
 * crash — it is a page that looks right and is subtly wrong. verify_order08.py
 * therefore checks these against the real Python for thousands of live values
 * rather than trusting the port.
 *
 * The originals:
 *
 *     _FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
 *
 *     def to_persian(n):                       # fa() in the templates
 *         if n is None: return "—"
 *         if isinstance(n, bool): return str(n)
 *         if isinstance(n, (int, float)):
 *             n = f"{n:,.0f}" if float(n).is_integer() else f"{n:,.2f}"
 *         return str(n).translate(_FA_DIGITS)
 *
 *     def to_persian_plain(n):                 # fy() in the templates
 *         if n is None: return "—"
 *         return str(n).translate(_FA_DIGITS)
 *
 * Note what is NOT translated: the thousands separator stays an ASCII comma and
 * the decimal point stays an ASCII dot, because Python's format produced them
 * and translate() only maps the ten digits. Reaching for
 * Intl.NumberFormat("fa-IR") here would be wrong — it emits the Arabic thousands
 * separator "٬" and the Arabic decimal "٫", which is *more* correct Persian and
 * *different* from every other page in this app.
 */

const FA_DIGITS = ["۰", "۱", "۲", "۳", "۴", "۵", "۶", "۷", "۸", "۹"];

/** ASCII digits → Persian digits. Everything else passes through untouched. */
export function toFaDigits(s: string): string {
  let out = "";
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    out += c >= 48 && c <= 57 ? FA_DIGITS[c - 48] : s[i];
  }
  return out;
}

/**
 * Python's `f"{v:,.Nf}"`.
 *
 * toFixed alone is not enough: it does not group, and — the subtle part — for
 * a value like 2.675 it can round differently from Python because both operate
 * on the same binary double but through different algorithms. In practice they
 * agree for every value this application produces (percentages and prices
 * derived from division), and verify_order08.py proves that over the real data
 * rather than asserting it here.
 */
/** Add one to a decimal digit string, carrying ("199" → "200", "99" → "100"). */
function incrementDigits(s: string): string {
  const out = s.split("");
  let i = out.length - 1;
  for (; i >= 0; i--) {
    if (out[i] === "9") {
      out[i] = "0";
    } else {
      out[i] = String(Number(out[i]) + 1);
      return out.join("");
    }
  }
  return "1" + out.join("");
}

function pyFormat(v: number, decimals: number): string {
  if (!Number.isFinite(v)) {
    // Python would render inf/nan as "inf"/"nan"; no analytic produces them,
    // but returning the em dash is the safe, visible fallback.
    return "—";
  }
  // Sign is handled separately so that -0 and values that round to zero keep
  // their minus, exactly as Python does: f"{-0.001:,.2f}" is "-0.00", and
  // f"{-0.0:,.0f}" is "-0". JS's own (-0).toFixed(0) drops the sign.
  const negative = v < 0 || Object.is(v, -0);
  const a = Math.abs(v);
  if (a >= 1e21) return (negative ? "-" : "") + String(a); // beyond toFixed

  // WHY NOT JUST toFixed(decimals)?
  //
  // Because the two languages break ties differently, and it shows on real
  // data. Python's format rounds half to EVEN; ECMAScript's toFixed is
  // specified to pick the LARGER candidate on a tie, i.e. half away from zero.
  // Measured against the live dataset, four values diverged:
  //
  //     0.125   python ۰.۱۲   toFixed ۰.۱۳
  //     2.625   python ۲.۶۲   toFixed ۲.۶۳
  //    15.625   python ۱۵.۶۲  toFixed ۱۵.۶۳
  //    40.625   python ۴۰.۶۲  toFixed ۴۰.۶۳
  //
  // A tie can only happen when the double is EXACTLY n.xx5, which as a dyadic
  // rational needs at most `decimals + 1` fractional digits — so a 20-digit
  // expansion sees every possible tie exactly, and for everything else the
  // first differing digit has long since decided the outcome.
  const s = a.toFixed(20);
  const dot = s.indexOf(".");
  const intPart = s.slice(0, dot);
  const frac = s.slice(dot + 1);
  const keep = frac.slice(0, decimals);
  const rest = frac.slice(decimals);

  // Digit-string arithmetic rather than BigInt: BigInt needs an ES2020 target,
  // and the build deliberately targets ES2019 because a meaningful share of the
  // audience is on older Android browsers. Adding one to a decimal string is
  // four lines; dropping those browsers is not worth it.
  let digitStr = intPart + keep;
  const firstRest = rest.charCodeAt(0) - 48;
  const tailHasValue = /[1-9]/.test(rest.slice(1));
  const lastKept = digitStr.charCodeAt(digitStr.length - 1) - 48;
  const roundUp =
    firstRest > 5 ||
    (firstRest === 5 && tailHasValue) ||
    // Exact tie → round half to even, as Python does.
    (firstRest === 5 && !tailHasValue && lastKept % 2 === 1);
  if (roundUp) digitStr = incrementDigits(digitStr);

  const digits = digitStr.padStart(decimals + 1, "0");
  const ip = decimals ? digits.slice(0, -decimals) : digits;
  const fp = decimals ? digits.slice(-decimals) : "";
  // Group the integer part in threes with an ASCII comma, as Python's "," does.
  const grouped = ip.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return (negative ? "-" : "") + grouped + (decimals ? "." + fp : "");
}

/**
 * db.to_persian — grouped, Persian digits. `null`/`undefined` → the em dash the
 * templates show for missing data.
 */
export function fa(n: number | string | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (typeof n === "string") return toFaDigits(n);
  if (typeof n === "boolean") return String(n);
  // Python: integral floats print with no decimals, everything else with two.
  const s = Number.isInteger(n) ? pyFormat(n, 0) : pyFormat(n, 2);
  return toFaDigits(s);
}

/** db.to_persian_plain — Persian digits, NO grouping (dates, small counts). */
export function fy(n: number | string | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return toFaDigits(String(n));
}

/**
 * The `_pill.html` partial, as data:
 *
 *   {% if val is not none %}
 *     <span class="pill {{ 'up' if val >= 0 else 'down' }}">
 *       {{ '+' if val >= 0 else '−' }}{{ fa(val|abs) }}٪</span>
 *   {% else %}<span class="muted">—</span>{% endif %}
 *
 * Two characters here are deliberately not the obvious ASCII ones and must be
 * copied exactly: the negative sign is U+2212 MINUS SIGN (not a hyphen), and
 * the percent is U+066A ARABIC PERCENT SIGN (not "%").
 */
export interface Pill {
  readonly missing: boolean;
  readonly cls: "up" | "down";
  readonly text: string;
}

const MINUS = "−"; // U+2212 MINUS SIGN
const PERCENT = "٪"; // U+066A ARABIC PERCENT SIGN

export function pill(val: number | null | undefined): Pill {
  if (val === null || val === undefined) {
    return { missing: true, cls: "up", text: "—" };
  }
  const up = val >= 0;
  return {
    missing: false,
    cls: up ? "up" : "down",
    text: `${up ? "+" : MINUS}${fa(Math.abs(val))}${PERCENT}`,
  };
}
