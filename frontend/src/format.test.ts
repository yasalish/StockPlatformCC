/**
 * format.test.ts — the Persian formatting layer (review finding M-5)
 *
 * `frontend/package.json` defined dev, build and typecheck and nothing else. No
 * component tests, no unit tests, and vue-tsc catches type errors but never
 * behaviour. This file covers format.ts because it is pure, it is cheap to
 * cover, and it is the module a bug in would be least visible in review and
 * most visible to users — every number on every screen goes through it.
 *
 * These are not decorative assertions. Each one pins a decision the source
 * comments call out as deliberate and easy to "clean up" by mistake:
 *
 *   - the negative sign is U+2212 MINUS SIGN, not a hyphen
 *   - the percent is U+066A ARABIC PERCENT SIGN, not "%"
 *   - integral floats print with NO decimals, non-integral with exactly two,
 *     matching Python's db.to_persian so a server-rendered table and an
 *     island-rendered one cannot disagree
 *   - fy() does NOT group, because a Jalali year is 1405 and never ۱,۴۰۵
 */
import { describe, expect, it } from "vitest";
import { fa, fy, pill, toFaDigits } from "./format";

const FA = "۰۱۲۳۴۵۶۷۸۹";

describe("toFaDigits", () => {
  it("maps every Latin digit and leaves everything else alone", () => {
    expect(toFaDigits("0123456789")).toBe(FA);
    // Separators, letters and Persian text must survive untouched — this runs
    // over strings that already contain «٪» and «/».
    expect(toFaDigits("1,234.56")).toBe("۱,۲۳۴.۵۶");
    expect(toFaDigits("1405/06/11")).toBe("۱۴۰۵/۰۶/۱۱");
    expect(toFaDigits("فولاد 7")).toBe("فولاد ۷");
    expect(toFaDigits("")).toBe("");
  });
});

describe("fa — db.to_persian", () => {
  it("returns the em-dash placeholder for missing values", () => {
    expect(fa(null)).toBe("—");
    expect(fa(undefined)).toBe("—");
  });

  it("prints integers with grouping and no decimals", () => {
    expect(fa(0)).toBe("۰");
    expect(fa(7)).toBe("۷");
    expect(fa(1234)).toBe("۱,۲۳۴");
    expect(fa(1234567)).toBe("۱,۲۳۴,۵۶۷");
  });

  it("treats an integral FLOAT as an integer, like Python does", () => {
    // 12.0 must not print as ۱۲.۰۰ — a server-rendered cell says ۱۲ and the two
    // appear in the same column.
    expect(fa(12.0)).toBe("۱۲");
  });

  it("renders a raw negative with an ASCII hyphen — pill() is where U+2212 lives", () => {
    // Worth pinning because the two differ ON PURPOSE and it looks like a bug.
    // fa() is the plain number formatter and keeps the hyphen it gets from
    // toLocaleString; pill() builds the ±badge and substitutes U+2212 MINUS
    // SIGN, which is the character the Jinja macro emits. Anyone "fixing" fa()
    // to match pill() would change every raw number in the app.
    expect(fa(-3)).toBe("-۳");
    expect(fa(-3).includes("−")).toBe(false); // no U+2212 here
    expect(pill(-3).text.includes("−")).toBe(true); // but yes here
  });

  it("prints non-integral numbers with exactly two decimals", () => {
    expect(fa(1.5)).toBe("۱.۵۰");
    expect(fa(184.43496801705757)).toBe("۱۸۴.۴۳");
    expect(fa(0.005)).toBe("۰.۰۱");
  });

  it("passes strings through digit conversion untouched otherwise", () => {
    expect(fa("1405-06-11")).toBe("۱۴۰۵-۰۶-۱۱");
  });
});

describe("fy — db.to_persian_plain", () => {
  it("converts digits WITHOUT grouping", () => {
    // The whole reason fy exists: a Jalali year is ۱۴۰۵, never ۱,۴۰۵.
    expect(fy(1405)).toBe("۱۴۰۵");
    expect(fy("1405/06/11")).toBe("۱۴۰۵/۰۶/۱۱");
    expect(fy(20)).toBe("۲۰");
  });
  it("still handles missing values", () => {
    expect(fy(null)).toBe("—");
    expect(fy(undefined)).toBe("—");
  });
});

describe("pill — the ±۱۲.۳۴٪ badge", () => {
  it("uses U+2212 MINUS SIGN for negatives, not a hyphen", () => {
    const p = pill(-5.67);
    expect(p.cls).toBe("down");
    expect(p.text.startsWith("−")).toBe(true);
    expect(p.text.includes("-")).toBe(false);
  });

  it("uses U+066A ARABIC PERCENT SIGN, not %", () => {
    expect(pill(1).text.endsWith("٪")).toBe(true);
    expect(pill(1).text.includes("%")).toBe(false);
  });

  it("prints the ABSOLUTE value after the sign", () => {
    // −۵.۶۷٪ and not −−۵.۶۷٪: the sign is added, so fa() must see |val|.
    expect(pill(-5.67).text).toBe("−۵.۶۷٪");
    expect(pill(12.34).text).toBe("+۱۲.۳۴٪");
  });

  it("treats exactly zero as up, matching the Jinja macro's `val >= 0`", () => {
    const p = pill(0);
    expect(p.cls).toBe("up");
    expect(p.text).toBe("+۰٪");
  });

  it("reports missing values without pretending they are zero", () => {
    const p = pill(null);
    expect(p.missing).toBe(true);
    expect(p.text).toBe("—");
  });
});
