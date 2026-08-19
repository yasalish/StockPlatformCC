# PORT_CONTRACT.md — حساب کاربری، اشتراک، تنظیمات، پوسته، راهنما، درباره

Working contract for the account/subscription/settings/theme/help/about port into
بورس‌نگار, modelled on the sibling project فیلم‌نگار
(`C:\Users\ASUS\Desktop\filmnegar` — `plans.py`, `guards.py`, `account.py`,
`billing.py`, `frontend/src/features/theme/useTheme.ts`).

`plans.py` **already exists and is final** — read it first; its six numbered
rules in the docstring govern everything here. Do not edit it.

This file is the single source of truth for names, signatures and shapes so that
files written in parallel fit together on the first try. If you believe a
signature here is wrong, implement it as written and say so in your report —
do not silently diverge, because someone else is coding against it right now.

---

## 0. House style — non-negotiable, this repo already reads this way

* **Every module, function and non-obvious block carries a comment that explains
  *why*, not *what*.** Read `db.py:51-75` (`_required_env`), `app.py:56-70`
  (the `STOCK_SECRET` block) and `plans.py` for the register: it explains the
  failure mode being avoided, names it concretely, and says what breaks if the
  code is changed back. Match that density and that voice. A file of bare code
  will be rejected.
* **Persian UI text, Persian digits, RTL.** UI strings are Persian; numbers
  shown to a user go through `db.to_persian` / `db.to_persian_plain` (exposed to
  templates as `fa` / `fy`). Never store or compare Persian digits — they are
  presentation only.
* **CSS: logical properties only.** `margin-inline-start`, `inset-inline-end`,
  `padding-inline`, `text-align: start`. Never `left`/`right`. The existing
  `style.css` is already written this way; keep it that way.
* **psycopg2, not psycopg3.** This repo uses `psycopg2` with
  `db.get_db()` / `db.release(conn)` and `psycopg2.extras.RealDictCursor`.
  Every borrow is paired with a `try: ... finally: release(conn)`.
* **No ORM.** Raw SQL only.
* **Timestamps are naive-UTC ISO strings in TEXT columns.** That is what
  `db._utcnow()` produces and what `users.created_at` already is. It is not
  ideal and it is not being changed here.
* Persian dates: `jdatetime` (already a dependency). Server-side only.

---

## 1. Schema — `migrations/versions/0005_accounts_plans_prefs.py`

Alembic, hand-written SQL (`op.execute`, **one statement per call** — a
semicolon-joined multi-statement string is rejected). `down_revision = "0004"`
(check the real revision id in `migrations/versions/0004_order06_job_tables.py`
and use that literal). Follow the `_has()` / `IF NOT EXISTS` idempotence pattern
already used by `0001_baseline_schema.py`.

### 1.1 `users` — three new columns

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS plan            TEXT NOT NULL DEFAULT 'free'
ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_expires_at TEXT
ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_source     TEXT NOT NULL DEFAULT 'default'
```

`plan_expires_at` is **nullable with no default** — NULL means *never expires*,
which is how both a free account and a grandfathered one are stored
(`plans.is_expired`). A default of `''` would be fine too but NULL is clearer;
either way `plans.is_expired` treats empty and NULL identically.

### 1.2 Grandfathering — rule 4, and the reason this rollout is safe

Immediately after adding the columns, in the **same** migration:

```sql
UPDATE users SET plan = 'pro', plan_expires_at = NULL, plan_source = 'grandfathered'
 WHERE plan_source = 'default' AND plan = 'free'
```

Every account that existed before plans did keeps حرفه‌ای permanently. Without
this, an existing user with 40 starred symbols would find their دیده‌بان over
the new free ceiling of 15 the moment this deploys. Comment it to that effect.

### 1.3 `plan_orders`

```
id            BIGSERIAL PRIMARY KEY
user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE
plan          TEXT NOT NULL
period        TEXT NOT NULL
amount_rial   BIGINT NOT NULL          -- ریال. The column is named for the unit
                                       -- on purpose; see plans.py rule 1.
status        TEXT NOT NULL DEFAULT 'pending'   -- pending|paid|cancelled|failed
gateway       TEXT NOT NULL DEFAULT 'manual'
gateway_ref   TEXT DEFAULT ''
note          TEXT DEFAULT ''
activated_by  BIGINT REFERENCES users(id) ON DELETE SET NULL
created_at    TEXT NOT NULL
paid_at       TEXT DEFAULT ''
```

Indexes: `(user_id, id DESC)` for the user's order list, and a partial
`WHERE status='pending'` index for the admin queue.

`activated_by` is the audit trail a manual-payment flow lives or dies by: it
records *which admin* accepted the receipt. `ON DELETE SET NULL` rather than
CASCADE — deleting a staff account must not delete the order history it touched.

### 1.4 `user_prefs` — one row per user, updated in place

```
user_id       BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE
theme         TEXT NOT NULL DEFAULT 'light'
digits        TEXT NOT NULL DEFAULT 'fa'      -- fa|en
default_kind  TEXT NOT NULL DEFAULT 'stock'   -- stock|etf
rows_per_page INTEGER NOT NULL DEFAULT 50
default_period TEXT NOT NULL DEFAULT 'p20'
density       TEXT NOT NULL DEFAULT 'comfortable'  -- comfortable|compact
reduce_motion BOOLEAN NOT NULL DEFAULT FALSE
updated_at    TEXT NOT NULL
```

A row per user updated in place, not an event log — the same reasoning as
فیلم‌نگار's `writing_activity` rollup. `prefs.py` (§3) owns what these values
may be; the column defaults must equal `prefs.DEFAULTS` exactly or a fresh
account and a saved-then-reset account would render differently.

### 1.5 `saved_screens`

```
id         BIGSERIAL PRIMARY KEY
user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE
name       TEXT NOT NULL
kind       TEXT NOT NULL           -- stock|etf
page       TEXT NOT NULL           -- which screen: market|screener|performance|strategies
query      TEXT NOT NULL DEFAULT ''-- the query string, verbatim, without a leading '?'
created_at TEXT NOT NULL
UNIQUE (user_id, name)
```

Storing the query string verbatim rather than parsed columns is deliberate: the
filters on those pages change shape as the platform grows, and a preset that is
just "the URL that worked" cannot go stale in a way that needs a migration.
`UNIQUE (user_id, name)` so re-saving under an existing name is a rename-collision
the API can report, not a silent duplicate.

### 1.6 `downgrade()`

Drops `plan_orders`, `user_prefs`, `saved_screens` and the three `users` columns.
Unlike the baseline's refusal this one is genuinely reversible — it destroys
subscription bookkeeping, which is bad, but not six million price rows. Comment
that distinction, and note that dropping the columns loses who paid for what.

---

## 2. `db.py` — new data functions

Append a clearly-headed section (`# --- اشتراک و تنظیمات / plans, prefs, screens ---`).
Reuse `_user_row`, `_rows`, `_one`, `_utcnow`, `get_db`, `release`.
**Do not modify existing functions** except as listed in §2.6.

### 2.1 Order status constants (module level, near the new section)

```python
ORDER_PENDING   = "pending"
ORDER_PAID      = "paid"
ORDER_CANCELLED = "cancelled"
ORDER_FAILED    = "failed"
```

`billing.py` imports these by name; do not inline the strings.

### 2.2 Plan state

```python
def plan_state(user_id) -> dict
```
Returns exactly:
```python
{"plan": str,            # what is STORED
 "effective": str,       # plans.effective_id(plan, expires_at) — what is IN FORCE
 "expires_at": str|None,
 "source": str,
 "is_admin": bool,       # users.role == 'admin'
 "days_left": int|None}  # plans.days_left(expires_at)
```
`plan` and `effective` differ when a paid subscription has lapsed, and the UI has
to be able to say so rather than quietly showing رایگان. Note in a comment that
`is_admin` derives from the existing `users.role` column — this repo has no
`is_admin` column, unlike فیلم‌نگار.
A missing user returns the free/default shape rather than raising.

```python
def effective_plan(user_id) -> str      # plan_state(user_id)["effective"]
def set_user_plan(user_id, plan, *, expires_at=None, source=None) -> None
def set_admin(user_id, is_admin) -> None    # writes users.role = 'admin'|'user'
```

### 2.3 Usage counts

```python
def plan_usage(user_id) -> dict
```
`{"watchlist_symbols": int, "saved_screens": int, "price_alerts": 0}`

Keys are the `plans.LIMIT_*` constants so the اشتراک screen can zip usage
against `plans.payload(...)["limits"]` without a translation table. One query per
count is fine; comment that `price_alerts` is a declared quota with no feature
behind it yet and therefore reports 0 — better an honest zero on the pricing
page than a row that silently disappears.

Also: `def count_watchlist(user_id) -> int` (or reuse the existing
`watch_count`; if you reuse it, say so and do not add a second name for the
same count).

### 2.4 Orders

```python
def create_plan_order(user_id, plan, period, amount_rial, *, gateway="manual", note="") -> dict
def get_plan_order(order_id) -> dict|None
def close_order(order_id, status, *, gateway_ref="", note=None, activated_by=None) -> dict|None
def list_plan_orders(user_id, limit=50) -> list[dict]
def list_pending_orders(limit=200) -> list[dict]   # joins users.username, users.display_name
```

`close_order` sets `paid_at = _utcnow()` **only** when `status == ORDER_PAID`,
and leaves `note` untouched when passed `None` (as opposed to `""`, which
clears it) — `billing.py` relies on both. All of them return the full row as a
dict so callers never re-read.

### 2.5 Prefs and saved screens

```python
def get_prefs(user_id) -> dict    # prefs.DEFAULTS merged under the stored row;
                                  # a user with no row gets the defaults, not None
def set_prefs(user_id, values: dict) -> dict   # UPSERT (ON CONFLICT (user_id) DO UPDATE),
                                  # only keys present in `values` are written,
                                  # returns the full merged row
def list_screens(user_id) -> list[dict]
def get_screen(screen_id) -> dict|None
def create_screen(user_id, name, kind, page, query) -> dict|None   # None on UNIQUE violation
def delete_screen(screen_id) -> bool
def count_screens(user_id) -> int
```

`get_prefs` merging over `prefs.DEFAULTS` is what makes adding a preference a
one-line change instead of a migration plus a backfill. **`db.py` must not
import `prefs`** if that creates a cycle — it will not (`prefs.py` is pure and
imports nothing from this repo), so `import prefs` at the top of the new section
is correct and intended.

### 2.6 The one existing-function change

`update_user_profile(user_id, display_name, email)` and
`set_user_password(user_id, password_hash)` and
`email_taken(email, exclude_user_id=None)` **do not exist yet in this repo**
(they exist in فیلم‌نگار's `db.py` — port them). Add them alongside the existing
`create_user` / `get_user` block, not in the new plans section, because that is
where a reader looks for them.

---

## 3. `prefs.py` — new, pure module

No Flask, no database, no network. Same discipline as `plans.py`: it is the only
place that decides what a preference may be.

```python
DEFAULTS = {"theme": "light", "digits": "fa", "default_kind": "stock",
            "rows_per_page": 50, "default_period": "p20",
            "density": "comfortable", "reduce_motion": False}

THEMES = [...]        # see §6 — id, label, family ('light'|'dark'), swatch (2 hex)
THEME_IDS = (...)
DIGIT_MODES = ("fa", "en")
KINDS = ("stock", "etf")
ROWS_CHOICES = (25, 50, 100, 200)
DENSITIES = ("comfortable", "compact")

def theme_option(theme_id) -> dict|None
def family_of(theme_id) -> str     # 'light' for anything unknown — see §6
def normalize(values: dict) -> dict    # keep only known keys, coerce types,
                                       # drop anything invalid (do NOT raise)
def validate(values: dict) -> str|None # a Persian error message, or None
def payload(stored: dict) -> dict      # DEFAULTS | normalize(stored), plus
                                       # 'theme_family' for the template
```

`normalize` **drops** an invalid value back to the default rather than raising:
these arrive from a `<select>` in a browser, and a stale tab posting last week's
option must not 500. `validate` exists for the API to give a real message when
the client sent something structurally wrong. Say all of that in the docstring.

`rows_per_page` must be coerced from the string a form posts (`"50"` → `50`) and
clamped to `ROWS_CHOICES`; `reduce_motion` accepts `True/'1'/'true'/'on'`.

`default_period` must be validated against `db.PERIODS` keys — but `prefs.py` is
pure and may not import `db`. Resolve this by declaring the valid periods as a
literal tuple in `prefs.py` **and** adding a test that asserts it equals
`tuple(db.PERIODS)`, so the two cannot drift silently. That test is the only
place the two meet.

---

## 4. `guards.py` — new

Direct port of فیلم‌نگار's `guards.py`, minus the project/scene/sequence role
helpers (this app has no shared resources). Read that file.

```python
def current_plan() -> str                       # db.effective_plan(current_user.id)
def _deny_json()                                # ({"error": "دسترسی مجاز نیست"}, 404)
def _upgrade_json(message, *, plan_id, needed=None, limit=None)   # 402
def require_quota(key, count, *, plan_id=None)  # None, or the 402 tuple to return
def require_feature(feature, *, plan_id=None)   # None, or the 402 tuple to return
def require_admin()                             # None, or _deny_json()
```

**The two rules that matter:**

* **A paywall answers HTTP 402 with `upgrade: true`, never 403.** A paywall is
  not a permission error; the client keys off that flag to open the اشتراک
  screen instead of toasting an error the user cannot act on. Include `limit` in
  the body so the screen can state the ceiling without hard-coding the catalogue
  in the browser.
* **`require_admin` answers 404, not 403** — a 403 tells an attacker the route
  exists.

Every gate asks `db.effective_plan`, never the stored plan: a lapsed
subscription is entitled to رایگان and `plans.effective_id` is where that is
decided.

Returned-response style (`refusal = require_quota(...); if refusal: return refusal`)
rather than raising, matching فیلم‌نگار so the routes stay flat.

---

## 5. Blueprints

### 5.1 `account.py` — the signed-in user's own record

Port فیلم‌نگار's `account.py`, including **both** docstring warnings (why not
`profile.py`: it shadows the stdlib `profile` and breaks `import cProfile`
process-wide; why not inside `auth.py`: auth endpoints are reachable
unauthenticated by design). Blueprint name `account`, variable `account_bp`.

Every route here acts on `current_user` and takes no user id, so there is no
object to authorise. Use `@login_required` on each (this app *does* use
Flask-Login's decorator — unlike فیلم‌نگار, which has a global `_require_auth`).

```
GET   /api/me                 -> _me()
PATCH /api/me                 -> display_name (required, ≤64), email (absent=leave,
                                 present-empty=clear); 409 on a duplicate email
POST  /api/me/password        -> new_password + confirm, and current_password
                                 UNLESS the account is Google-only (empty hash),
                                 where asking for one would lock the owner out
GET   /api/me/prefs           -> prefs.payload(db.get_prefs(...))
PATCH /api/me/prefs           -> prefs.validate then db.set_prefs; returns payload
GET   /api/me/screens         -> {"screens": [...]}
POST  /api/me/screens         -> require_quota(plans.SCREENS, db.count_screens(...))
                                 BEFORE the insert; 409 on a duplicate name
DELETE /api/me/screens/<int:screen_id>  -> 404 (not 403) if it is not yours
```

Validation as pure module-level functions (`validate_display_name`,
`validate_email`, `validate_new_password`) so tests can cover them with no
database and no request context. Import `MIN_PASSWORD` from `auth.py`.
`EMAIL_RE` is deliberately permissive — comment why (the only authority on
whether an address works is a message arriving at it; this app sends no mail).

`_me()` returns: `username, display_name, email, has_password, google_linked,
created_at, created_jalali, last_login, plan, plan_name, plan_expired,
grandfathered, is_admin`. `has_password` is a **boolean derived from the hash —
never the hash itself**.

The **username is not editable**. Comment why: it is the login identity and the
handle an admin is given over the phone; a rename frees the old name for someone
else to claim.

### 5.2 `billing.py` — orders and activation

Port فیلم‌نگار's `billing.py` nearly verbatim, adapted to this repo's db API.
Blueprint `billing`, variable `billing_bp`. Keep the `_GATEWAYS` seam, the
`manual` gateway, and the unimplemented-on-purpose `_zarinpal_start` **with its
docstring explaining why a half-written payment integration is worse than an
honest refusal**.

```
GET  /api/plans                                   (login required)
GET  /api/me/subscription
GET  /api/plan-orders
POST /api/plan-orders                             -> 201 {"order":…, "payment":…}
POST /api/plan-orders/<int:order_id>/cancel
GET  /api/admin/plan-orders                       -> require_admin()
POST /api/admin/plan-orders/<int:order_id>/activate
POST /api/admin/users/<username>/plan
```

`activate_order(order_id, *, activated_by=None, gateway_ref="", note=None)`
returns `(ok: bool, payload_or_error_message)` and is **the only place a plan is
granted against an order**. Carry over both of its hard-won behaviours and their
comments:

* **Renewing stacks; upgrading does not.** Buying more of the plan you already
  hold extends the expiry you have (`plans.expiry_after(extend_from=…)`); moving
  to a different tier starts a fresh term, because carrying the remainder of a
  cheaper plan onto a dearer one sells time at the wrong price.
* **An already-paid order is refused, not replayed.** A gateway *will* deliver
  the same callback twice, and a second activation hands out a second month for
  one payment.

The **price is looked up from `plans.amount_rial`, never taken from the request
body**. A client-supplied amount is a client-supplied invoice. Comment it.

`MANUAL_PAYMENT_INFO` comes from the environment with a default that **names no
account at all** — a card number is deployment data, not source code. Add it to
`.env.example` with a comment.

---

## 6. Themes — `static/css/style.css`

`:root` today **is** the light theme (warm cream + gold, `--bg:#f4f2ea`,
`--brand:#b89529`). Keep it exactly as it is: it is what every existing user
sees, and it must not shift by one hex digit.

Add five sibling blocks, matching فیلم‌نگار's ids and labels so the two products
feel like one house:

| id | label | family | surface | accent |
|---|---|---|---|---|
| *(`:root`)* | روشن | light | `#f4f2ea` | `#b89529` |
| `sepia` | سپیا | light | `#efe3cd` | `#a8781f` |
| `paper` | کاغذ سفید | light | `#f4f5f7` | `#b08e24` |
| `dark` | تاریک | dark | `#16232c` | `#c7ab4d` |
| `midnight` | نیمه‌شب | dark | `#0b1020` | `#c8b06a` |
| `graphite` | ذغالی | dark | `#1b1c1e` | `#c7ab4d` |

**Three rules, and the first is the one that will actually bite:**

1. **Each block must redefine every custom property `:root` defines** — all of
   `--bg --panel --ink --muted --line --brand --brand2 --brand-soft --header1
   --header2 --header3 --up --up-bg --down --down-bg --gold-grad --shadow`
   (`--radius` is geometry, not colour; leave it). A block that redefines half
   inherits the rest from `:root` and renders as a broken light theme. Verify by
   diffing the property names in each block against `:root`.
2. **The hardcoded colours have to go first.** `style.css` contains ~70 literal
   hex values, and the ones that break a dark theme are the light-surface
   literals: **`#fff` appears 25 times**, plus `#ffffff`, `#f6f2e6`, `#f4ecd2`,
   `#fff8e6`, `#fff7f0`, `#fff7e8`, `#fff2d6`, `#f0f3fa`, `#f0ece0`, `#e8f0fe`,
   `#fbf1da`, and the ink literals `#1c2830` (×3). Replace each with the
   variable it is standing in for (`var(--panel)`, `var(--brand-soft)`,
   `var(--ink)`, …), introducing a new token only where none fits. This is an
   **appearance-preserving refactor of the light theme**: after it, the rendered
   light page must be pixel-identical. Do it as its own pass, before adding the
   dark blocks, and state in your report which literals you could not resolve
   and why. Leave the TradingView block's `--tv-*` values alone — they are that
   widget's own palette, and note that limitation rather than guessing.
3. **`--up` / `--down` (green/red) keep their meaning in every theme** but need
   more luminance on dark surfaces to stay legible; `--up-bg` / `--down-bg` must
   become dark tints, not the light ones. A red that is unreadable on a dark
   background is a number an investor misreads.

Also: an unknown `data-theme` value must fall back to the light `:root`. Write
the same orientation comment فیلم‌نگار's `style.css:65-80` carries — a short note
saying what a new theme must do and where the picker list lives, so the next
person adding one does not have to infer these rules.

Then add the picker styles (`.theme-row`, `.theme-swatch`, `.theme-swatch.active`)
where each swatch paints its own theme so the list is its own legend, and the
user-menu / settings / plans / help / about component styles the templates need.

---

## 7. `static/js/theme.js` + `templates/base.html`

### 7.1 Pre-paint application — the part that must be inline

An **inline** `<script>` in `<head>`, before the stylesheet link, that reads
`localStorage['boursenegar-theme']` and sets `data-theme` on `<html>`. It has to
be inline and it has to be in the head: applied from an external file it lands
after first paint and the user sees a flash of the wrong theme on every
navigation. Keep it to a few lines, wrapped in `try{}catch{}` because
localStorage throws in private mode.

Storage key: `boursenegar-theme` (the sibling app uses `filmnegar-theme`; same
shape, different product). For a signed-in user the server value is authoritative
— render `data-theme="{{ prefs.theme }}"` on `<html>` from the context processor
and let the inline script only *fill in* when that attribute is absent, so a
logged-in user's saved theme is not overwritten by another device's localStorage.

### 7.2 `theme.js`

`setTheme(id)` writes the attribute + localStorage, and for a signed-in user
PATCHes `/api/me/prefs` (fire-and-forget: a failed sync must not revert the
click). `toggle()` crosses to the *other family's default* (`light` ⇄ `dark`),
never to the alternate the user last picked there — a control whose whole job is
being predictable must not depend on history. Expose `BN.theme`.

### 7.3 `base.html`

* `<html lang="fa" dir="rtl" data-theme="…">` + the inline pre-paint script.
* Replace the bare `.user-box` with a **user menu dropdown**: display name, the
  نوع حساب badge (`«بنیان‌گذار»` when grandfathered, `«پایان‌یافته»` when a paid
  plan has lapsed), then حساب کاربری / تنظیمات / اشتراک / راهنما / درباره /
  admin-only مدیریت سفارش‌ها / خروج. Keyboard accessible: Escape closes, focus
  visible, `aria-expanded`.
* A theme toggle button (☾/☀) in the topbar for one-click light⇄dark.
* Footer: راهنما · درباره · اشتراک links beside the existing data-source line.
* Do **not** touch the existing nav links, search box, `#nav-loader` or the
  flash block. This is an addition, not a redesign.

### 7.4 `app.py` context processor

Extend the existing `inject_helpers` / add `inject_account`:

```python
{"prefs": prefs.payload(db.get_prefs(current_user.id)) if authenticated
          else prefs.payload({}),
 "plan_badge": {...}}   # plan id, name, expired, grandfathered — or None
```

One cheap query for signed-in users; anonymous users get the defaults with no
query at all. The badge is on every screen, so it must not cost a round trip.

---

## 8. Pages — `app.py` routes + templates

All extend `base.html` and reuse the existing class vocabulary (`.card`,
`.btn`, `.pill`, `.topbar`, `.muted`, `.flash`). Add **new** classes only for
genuinely new components. No new CSS framework, no CDN.

| route | endpoint | template | auth |
|---|---|---|---|
| `/account` | `account_page` | `account.html` | login |
| `/settings` | `settings_page` | `settings.html` | — (theme works for anonymous) |
| `/plans` | `plans_page` | `plans.html` | — (pricing is public) |
| `/help` | `help_page` | `help.html` | — |
| `/about` | `about_page` | `about.html` | — |
| `/admin/orders` | `admin_orders_page` | `admin_orders.html` | login + admin |

* **`account.html`** — profile form (display name, email), password section
  (worded «تعیین گذرواژه» for a Google-only account and «تغییر گذرواژه»
  otherwise), identity facts (username, عضویت از in Jalali, آخرین ورود, Google
  link state), the subscription card (plan, expiry with `days_left`, source
  badge), a usage block (`plan_usage` vs the plan's limits as labelled meters),
  and the دیده‌بان count. Saved غربالگرها list with delete.
* **`settings.html`** — the theme picker (six swatches, each painting itself),
  then digits / default kind / rows per page / default period / density /
  reduce-motion. Persists immediately via `/api/me/prefs` for a signed-in user;
  for an anonymous visitor the theme still works from localStorage and the rest
  is disabled with one sentence saying why («برای ذخیرهٔ تنظیمات وارد شوید»).
* **`plans.html`** — three cards from `plans.catalog()`, a monthly/yearly toggle
  that shows `yearly_saving_toman`, the comparison table from
  `plans.feature_matrix()` (a `None` limit renders «نامحدود», `0` renders «—»,
  a `True` renders ✓), the current plan marked, and an order button that POSTs
  `/api/plan-orders` then shows the manual-payment instructions and the order
  number. **State the never-paywalled rule on this page** (§ plans.py rule 6) —
  the person reading a pricing page to decide whether to pay is exactly who
  needs to know the market data is not what is being sold.
* **`help.html`** — sections for every screen that exists *right now*
  (خانه، داشبورد، سهام، صندوق‌ها، غربالگر، بازدهٔ بازه، استراتژی‌ها، فیلترها،
  دیده‌بان، حساب، تنظیمات، اشتراک، به‌روزرسانی for admins), each saying what the
  page answers and how to read its columns; a section on the analytics
  definitions (بازده / سقف / کف, computed in **trading days** — take the formulas
  from `db.py`'s module docstring, do not invent them); and a section on Persian
  dates and digits. A help page that describes a control which is not there is
  worse than no help page.
* **`about.html`** — what the platform is, its data source (پایگاه دادهٔ بورس
  تهران via `finpy_tse`), the plan summary **with the never-paywalled sentence**,
  and a «فارسی، جدی گرفته‌شده» note (self-hosted وزیرمتن — no CDN, Jalali dates,
  Persian digits). **Invent nothing**: no support email, no phone number, no URL,
  no company name, no version string that is not in the repo. An About page that
  names a channel nobody answers is worse than one that names none.
* **`admin_orders.html`** — the pending queue with username, plan, period,
  amount in **both** تومان and ریال, and an activate form taking `gateway_ref`
  (the receipt reference) + note.

Client JS goes in a new `static/js/account.js` (profile, prefs, screens, orders,
theme picker wiring). `static/js/app.js` is **append-only** here — do not
restructure it.

### 8.1 Quota enforcement in existing routes — the only edits to existing behaviour

`POST /api/watchlist/toggle` (`app.py:826`): before an **add** (not a remove),
`refusal = guards.require_quota(plans.WATCHLIST, db.watch_count(current_user.id))`
and return it if set. A **remove must never be refused** — being over quota
must not trap someone's existing symbols. Comment that asymmetry.

Nothing else changes. No existing public page becomes login-only or paid.

---

## 9. Tests — `tests/` + `pytest.ini`

`pytest.ini` at the repo root: `[pytest]` / `testpaths = tests` / `-q`.
Add `pytest>=8.0` to `requirements.txt` under a comment marking it a dev-only
dependency.

Pure functions only — **no database, no Flask app, no Redis, no network.** The
suites, mirroring فیلم‌نگار's `tests/test_plans.py`:

* `tests/test_plans.py` — entitlements; `None` vs `0` (rule 2) including the
  free plan's real `0` for alerts; expiry downgrade and that NULL never reads as
  expired (rule 3); `effective_id` for a grandfathered NULL expiry (rule 4); an
  unknown plan id resolving to free (rule 5); price arithmetic in both units and
  that `toman()` is the only divisor (rule 1); `amount_rial` returning None for
  a free plan and for a bad period; `expiry_after` stacking via `extend_from`
  and *ignoring* a past date; `days_left` flooring at 0; `remaining()` never
  negative; `feature_matrix` covering every id in `PLAN_IDS` and every key in
  `LIMIT_LABELS` + `FEATURE_LABELS`; every refusal message naming the ceiling in
  Persian digits.
* `tests/test_prefs.py` — `normalize` dropping unknown keys and invalid values
  to defaults without raising; `rows_per_page` coerced from a string and clamped;
  `reduce_motion` truthiness forms; `family_of` answering `'light'` for an
  unknown id (and *why*: an unknown `data-theme` renders as `:root`, which is
  light — guessing dark would put a light-looking page under dark-mode logic);
  every `THEMES` entry having a matching `[data-theme="…"]` block in
  `static/css/style.css` (read the file — this is the test that catches a theme
  added to the picker but not to the stylesheet); `DEFAULTS` keys matching
  `payload({})`.
* `tests/test_account_validation.py` — the three validators, including that an
  empty email is valid (it means "remove the one I had") and that the length
  cap and the mismatch case produce Persian messages.
* `tests/test_billing_pure.py` — `_order_payload` carrying both currencies and
  never leaking a column the client should not see. If importing `billing`
  requires a Flask context, test `plans`-level arithmetic instead and say so —
  do not stand up a database to reach it.
* One test asserting `prefs`' period tuple equals `tuple(db.PERIODS)` (§3). This
  one may import `db`; if `db` cannot be imported without the environment, mark
  it `skipif` on that condition with a message saying what to set, rather than
  deleting the check.

Do not write a test that needs `STOCK_DB_PASSWORD`. `pytest -q` must pass on a
laptop with no PostgreSQL running.

---

## 10. Wiring — `app.py`

```python
import plans, prefs, guards
from account import account_bp
from billing import billing_bp
app.register_blueprint(account_bp)
app.register_blueprint(billing_bp)
```

Register beside the existing `app.register_blueprint(auth_bp)`. The pages in §8
are plain `@app.route` handlers next to the existing ones. Do not move existing
routes.

---

## 11. Docs

`README.md` gets a new section — **اشتراک و حساب کاربری** — covering the plan
catalogue and where it is decided (`plans.py`, and that it is the only place),
the Rial/Toman rule, the grandfathering behaviour of migration 0005, how a
manual payment is activated (admin → `/admin/orders`), how to make someone an
admin (the existing `UPDATE users SET role='admin'` one-liner — check the README
for how it is already phrased and match it), the themes and how to add one, and
that `pytest -q` runs without a database. Match the file's existing bilingual
heading style (`## اشتراک / Subscriptions`).

`.env.example`: `MANUAL_PAYMENT_INFO`, `PAYMENT_GATEWAY`, and a commented-out
`ZARINPAL_MERCHANT_ID`, each with a sentence of why.
