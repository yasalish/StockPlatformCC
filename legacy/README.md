# بایگانی / Legacy archive

اسکریپت‌های نسل قبل (Streamlit) و ابزارهای یک‌بارمصرف. **هیچ‌کدام توسط اپلیکیشن
Flask استفاده نمی‌شوند** — صرفاً به‌عنوان مرجع منطق تحلیل نگه داشته شده‌اند.

Previous-generation Streamlit scripts and one-off tools. **None of these are used
by the Flask app** — they are kept only as reference for the analytics logic,
which now lives in `db.py`.

Archived 2026-08-15.

## `streamlit/` — نسل قبل رابط کاربری
The Streamlit UI that the Flask platform replaced. These require `streamlit`,
which is **not** in `requirements.txt`, so they will not run without installing it.

`dashboard.py` is the entry point; it dynamically loads `stock_gainer.py` and
`etf_gainer.py` by file path, so those three must stay together.

Note: these are divergent versions, not exact copies — e.g. `search.py` and
`search2.py` differ on ~680 lines, and `stock_gain.py` (147 lines) is unrelated
to `stock_gain copy.py` (906 lines) despite the name.

## `importers/` — درج اولیهٔ داده
One-off scripts used to seed the PostgreSQL tables. Superseded by
`stock_updater.py` / `etf_updater.py` in the project root.

`etf.py` reads `Gold.xlsx` via a path relative to the **current working
directory**, so run it from `legacy/data/` if you ever need it again.

## `data/` — فایل‌های ورودی و تصاویر
Source spreadsheets for the importers above, plus screenshots and `Sandogh.js`
(a TSETMC filter snippet, unrelated to the web app).
