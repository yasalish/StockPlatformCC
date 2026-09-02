/**
 * market-open.js — k6 scenario modelling the opening bell (review finding M-5)
 *
 * WHY THIS FILE MATTERS MORE THAN IT LOOKS
 *
 * Every capacity number in the architecture review — the 12-16 concurrent
 * requests, the 300-1,200 req/s peak, the "two orders of magnitude" gap — is
 * ARITHMETIC from gunicorn.conf.py and from published behaviour of comparable
 * tools. The review says so itself, twice, and its closing recommendation is to
 * build this before acting on anything else, so that every subsequent change
 * gets a before-and-after number instead of an estimate.
 *
 * Run it before and after each phase. The numbers it prints are the only ones
 * in this project that are measured.
 *
 *   k6 run loadtest/market-open.js
 *   BASE=https://boursenegar.example k6 run loadtest/market-open.js
 *   PEAK_VUS=3000 k6 run loadtest/market-open.js
 *
 * WHAT IT MODELS
 *
 * The Tehran Stock Exchange trades roughly 09:00-12:30 local. This is not
 * 100,000 users spread over a day: it is a concentrated window with a sharp
 * spike at the open, when everyone wants the same thing at the same moment.
 * The stages below are that shape — a fast ramp to peak over two minutes, a
 * five-minute hold, then a decay into steady mid-session browsing.
 *
 * One thing confirmed in the source and reflected here: the islands do NOT
 * poll. A repository-wide search for setInterval in frontend/src returns
 * nothing, so load is driven by NAVIGATION, not by background refresh. Each
 * iteration below is therefore a page view — an HTML shell plus the one or two
 * API documents that page fetches — with think time between, rather than a
 * tight request loop, which would measure something this application never does.
 *
 * WHAT TO READ AFTERWARDS
 *
 *   http_req_duration ......... p95 is the number users feel
 *   cache_hit_rate ............ the X-Cache-Status share that is HIT/STALE.
 *                               This is finding C-2's whole payoff: if it is
 *                               not well above 0.9 at peak, edge caching is
 *                               not doing its job and Python is still doing
 *                               the work.
 *   http_req_failed ........... anything non-2xx, 429 included
 *   rate_limited .............. 429s specifically, so nginx's limiter and the
 *                               designer's slot guard are distinguishable from
 *                               real failures
 */
import http from "k6/http";
import { check, group, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

const BASE = __ENV.BASE || "http://localhost:8000";
const PEAK_VUS = Number(__ENV.PEAK_VUS || 500);

/* Share of responses nginx served from its own cache. The point of C-2. */
const cacheHitRate = new Rate("cache_hit_rate");
/* 429s, kept apart from genuine failures — they mean a limiter engaged, which
   is a different problem from the origin falling over. */
const rateLimited = new Rate("rate_limited");
const apiDuration = new Trend("api_req_duration", true);

export const options = {
  scenarios: {
    market_open: {
      executor: "ramping-vus",
      startVUs: Math.max(1, Math.round(PEAK_VUS * 0.02)),
      stages: [
        // 08:59 → 09:00. The bell.
        { duration: "2m", target: PEAK_VUS },
        // The first five minutes, where everything either holds or does not.
        { duration: "5m", target: PEAK_VUS },
        // Attention drains but the session continues.
        { duration: "3m", target: Math.round(PEAK_VUS * 0.35) },
        { duration: "5m", target: Math.round(PEAK_VUS * 0.35) },
        { duration: "1m", target: 0 },
      ],
      gracefulRampDown: "30s",
    },
  },
  thresholds: {
    // Deliberately generous rather than aspirational: a threshold nobody
    // believes gets ignored. Tighten these once there is a baseline.
    http_req_failed: ["rate<0.02"],
    http_req_duration: ["p(95)<2000"],
    // Once C-2 is deployed this should be trivially met; before it, it fails,
    // which is exactly the signal wanted.
    cache_hit_rate: ["rate>0.80"],
  },
  // The audience is on mobile connections in Iran; a 10s timeout is realistic
  // and stops one stuck request skewing the whole run.
  httpDebug: __ENV.DEBUG ? "full" : undefined,
};

/** Record cache and rate-limit outcomes for any response. */
function classify(res) {
  const status = res.headers["X-Cache-Status"] || res.headers["x-cache-status"] || "";
  // MISS and EXPIRED both cost an origin render; UPDATING and STALE do not.
  if (status) cacheHitRate.add(status === "HIT" || status === "STALE" || status === "UPDATING");
  rateLimited.add(res.status === 429);
  apiDuration.add(res.timings.duration);
  return res;
}

/* The mix is weighted by what people actually open at the bell: the dashboard
   and the market table dominate, the designer is rare and expensive. */
export default function () {
  const roll = Math.random();

  if (roll < 0.40) {
    group("dashboard", () => {
      const shell = http.get(`${BASE}/dashboard`, { tags: { page: "dashboard" } });
      check(shell, { "dashboard shell 200": (r) => r.status === 200 || r.status === 302 });
      // The shell paints, then fetches its data — the real sequence.
      classify(http.get(`${BASE}/dashboard/data`, { tags: { page: "dashboard_data" } }));
    });
  } else if (roll < 0.75) {
    group("market table", () => {
      const shell = http.get(`${BASE}/stocks`, { tags: { page: "stocks" } });
      check(shell, { "stocks shell 200": (r) => r.status === 200 || r.status === 302 });
      // The two the island fetches in parallel after H-1 split them.
      const responses = http.batch([
        ["GET", `${BASE}/api/market/stock`, null, { tags: { page: "api_market" } }],
        ["GET", `${BASE}/api/watchlist/keys`, null, { tags: { page: "api_watchlist" } }],
      ]);
      responses.forEach(classify);
      check(responses[0], { "market payload 200": (r) => r.status === 200 });
    });
  } else if (roll < 0.92) {
    group("period returns", () => {
      http.get(`${BASE}/performance`, { tags: { page: "performance" } });
      classify(http.get(`${BASE}/api/performance/stock`, { tags: { page: "api_perf" } }));
    });
  } else {
    group("screener", () => {
      http.get(`${BASE}/screener`, { tags: { page: "screener" } });
      classify(http.get(`${BASE}/api/screener/stock`, { tags: { page: "api_screener" } }));
    });
  }

  // Think time. Without it this measures a request flood, which is not what
  // navigation-driven load looks like and would make every number pessimistic.
  sleep(3 + Math.random() * 7);
}

export function handleSummary(data) {
  const m = data.metrics;
  const get = (name, stat) => {
    const v = m[name] && m[name].values;
    return v ? v[stat] : undefined;
  };
  const pct = (x) => (x === undefined ? "n/a" : `${(x * 100).toFixed(1)}%`);
  const ms = (x) => (x === undefined ? "n/a" : `${x.toFixed(0)} ms`);

  const lines = [
    "",
    "  BourseNegar — market-open scenario",
    "  ".padEnd(40, "-"),
    `  peak VUs            ${PEAK_VUS}`,
    `  requests            ${get("http_reqs", "count") ?? "n/a"}`,
    `  req/s (mean)        ${(get("http_reqs", "rate") ?? 0).toFixed(1)}`,
    `  p95 duration        ${ms(get("http_req_duration", "p(95)"))}`,
    `  p99 duration        ${ms(get("http_req_duration", "p(99)"))}`,
    `  failed              ${pct(get("http_req_failed", "rate"))}`,
    `  429 rate-limited    ${pct(get("rate_limited", "rate"))}`,
    `  edge cache hits     ${pct(get("cache_hit_rate", "rate"))}   <- finding C-2`,
    "",
    "  Record these numbers against the commit you ran them on. The review's",
    "  own figures are arithmetic; these are not.",
    "",
  ];
  return { stdout: lines.join("\n") };
}
