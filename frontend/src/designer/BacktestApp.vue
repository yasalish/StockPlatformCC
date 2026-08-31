<script setup lang="ts">
/**
 * BacktestApp.vue — /filter-backtest, «این فیلتر در گذشته چه می‌گفت».
 *
 * The third page of the designer, after the canvas and the result table, and
 * the one that answers whether the other two were worth building. The graph
 * arrives exactly as it does on the result page — from the saved filter named
 * in the URL, or from the draft the canvas left in localStorage — so the three
 * pages are one workflow and no graph is ever serialised twice.
 *
 * WHY THE «مازاد» COLUMN IS THE LAST ROW OF THE TABLE
 *
 * Every screener backtest anyone has ever shown a user leads with the average
 * return, and in a market that rose 113 % over the tested window every filter
 * ever written shows a handsome one. The number that carries information is the
 * difference against buying the whole market on the same days for the same
 * holding period, so «بازار» sits directly above «مازاد» and «مازاد» is the row
 * the eye stops on.
 *
 * WHY THE CAVEATS ARE ON THE PAGE RATHER THAN IN THE DOCS
 *
 * A backtest is a number that looks like a fact. The three things that would
 * make it not one — survivorship, unfillable queue entries, and a sample too
 * small to mean anything — are printed next to the result, because a caveat in
 * a help page is a caveat nobody reads before risking money on the number.
 */
import { computed, onMounted, ref } from "vue";
import GraphCanvas from "./GraphCanvas.vue";
import EquityChart from "./EquityChart.vue";
import { loadDraft, designerUrl, resultUrl } from "./draft";
import type { Catalog, Graph } from "./graph";
import { fa } from "../format";
import { handledInRow, openDetail } from "../nav";

const props = defineProps<{
  catalog: Catalog;
  detailBaseStock: string;
  detailBaseEtf: string;
  filterId: number | null;
}>();

interface Horizon {
  n: number;
  avg: number;
  median: number;
  win: number;
  best: number;
  worst: number;
  stdev: number;
  bench: number;
  excess: number;
  benched: number;
}

interface Trade {
  ticker: string;
  name: string;
  date: string;
  entry: number;
  exit_date: string | null;
  [k: string]: string | number | null;
}

interface Report {
  as_of: string;
  kind: "stock" | "etf";
  sessions: number;
  scanned: number;
  signals: number;
  clipped: boolean;
  horizons: number[];
  hold: number;
  cost: number;
  stats: Record<string, Horizon>;
  curve: number[];
  bench_curve: number[];
  dates: string[];
  drawdown: number;
  bench_drawdown: number;
  exposure: number;
  trades: Trade[];
  skipped: { lock: number; halt: number; bad: number };
  errors: number;
  from: string;
  to: string;
  server_ms: number;
}

const specs = computed(() => new Map(props.catalog.nodes.map((n) => [n.type, n])));
const colors = computed(() => new Map(props.catalog.categories.map((c) => [c.key, c.color])));

const graph = ref<Graph>({ nodes: [], edges: [] });
const title = ref("");
const currentId = ref<number | null>(props.filterId);
const kind = ref<"stock" | "etf">(props.catalog.kind);
const group = ref(props.catalog.group ?? "");
const subgroup = ref(props.catalog.subgroup ?? "");

const sessions = ref(250);
const cost = ref(1.2);
const hold = ref(22);
const fill = ref(true);
const repeat = ref(false);

const busy = ref(false);
const error = ref("");
const report = ref<Report | null>(null);
const showGraph = ref(false);

const SESSION_CHOICES = [
  { v: 120, l: "۶ ماه" },
  { v: 250, l: "۱ سال" },
  { v: 500, l: "۲ سال" },
  { v: 1000, l: "۴ سال" },
];

const detailBase = computed(() =>
  kind.value === "etf" ? props.detailBaseEtf : props.detailBaseStock,
);

/** Trades are shown against the horizon the curve follows, so the table and the
 *  picture cannot disagree about which holding period is being discussed. */
const holdKey = computed(() => `r${report.value?.hold ?? hold.value}`);

/** A sample this small is not a result, and the page says so rather than
 *  printing five significant figures over thirty trades. */
const THIN = 30;
const thin = computed(() => !!report.value && report.value.signals < THIN);

async function run() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    const res = await fetch("/api/designer/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        graph: graph.value,
        kind: kind.value,
        group: group.value || null,
        subgroup: subgroup.value || null,
        sessions: sessions.value,
        cost: cost.value,
        hold: hold.value,
        fill: fill.value,
        repeat: repeat.value,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data as { error?: string }).error || `HTTP ${res.status}`);
    report.value = data as Report;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "بک‌تست اجرا نشد.";
    report.value = null;
  } finally {
    busy.value = false;
  }
}

function pct(v: number | undefined, digits = 2): string {
  if (v === undefined || v === null || !Number.isFinite(v)) return "—";
  const s = (Math.round(v * 10 ** digits) / 10 ** digits).toFixed(digits);
  return `${fa(s)}٪`;
}

function signClass(v: number | undefined): string {
  if (v === undefined || !Number.isFinite(v) || Math.abs(v) < 0.005) return "";
  return v > 0 ? "is-up" : "is-dn";
}

function money(v: number): string {
  return fa(Math.round(v).toLocaleString("en-US"));
}

function openRow(ev: MouseEvent, t: Trade) {
  if (handledInRow(ev)) return;
  openDetail(`${detailBase.value}${t.ticker}`);
}

onMounted(async () => {
  if (currentId.value) {
    try {
      const res = await fetch(`/api/designer/filters/${currentId.value}`, {
        headers: { Accept: "application/json" },
      });
      if (res.ok) {
        const row = await res.json();
        graph.value = row.graph;
        title.value = row.name ?? "";
        kind.value = row.kind === "etf" ? "etf" : "stock";
      }
    } catch {
      /* falls through to the draft below */
    }
  }
  if (!graph.value.nodes.length) {
    const d = loadDraft();
    if (d) {
      graph.value = d.graph;
      title.value = d.name || "";
      currentId.value = d.id;
      kind.value = d.kind;
      if (!group.value) group.value = d.group;
      if (!subgroup.value) subgroup.value = d.subgroup;
    }
  }
  if (graph.value.nodes.length) void run();
  else error.value = "گرافی برای بک‌تست پیدا نشد. اول در «طراحی فیلتر» یک فیلتر بسازید.";
});
</script>

<template>
  <div class="dz-bt">
    <!-- ── controls ─────────────────────────────────────────────────── -->
    <section class="panel dz-bt-controls">
      <div class="dz-bt-ctl-row">
        <label class="dz-field">
          <span>بازهٔ تست</span>
          <select v-model.number="sessions" :disabled="busy">
            <option v-for="s in SESSION_CHOICES" :key="s.v" :value="s.v">{{ s.l }}</option>
          </select>
        </label>

        <label class="dz-field">
          <span>دورهٔ نگهداری</span>
          <select v-model.number="hold" :disabled="busy">
            <option :value="1">۱ کندل</option>
            <option :value="5">۵ کندل</option>
            <option :value="10">۱۰ کندل</option>
            <option :value="22">۲۲ کندل</option>
          </select>
        </label>

        <label class="dz-field">
          <span>کارمزد رفت‌وبرگشت ٪</span>
          <input v-model.number="cost" type="number" min="0" max="10" step="0.1" :disabled="busy" />
        </label>

        <label class="dz-bt-check" title="ورودهایی که در صف خرید یا نماد متوقف قابل انجام نبوده‌اند کنار گذاشته می‌شوند">
          <input v-model="fill" type="checkbox" :disabled="busy" />
          <span>فقط ورودهای قابل انجام</span>
        </label>

        <label class="dz-bt-check" title="اگر روشن باشد، هر کندلی که شرط در آن برقرار است یک سیگنال جداگانه شمرده می‌شود">
          <input v-model="repeat" type="checkbox" :disabled="busy" />
          <span>هر کندل یک سیگنال</span>
        </label>

        <button type="button" class="btn btn-primary" :disabled="busy" @click="run">
          {{ busy ? "در حال محاسبه…" : "اجرای بک‌تست" }}
        </button>
      </div>

      <div class="dz-bt-links muted small">
        <a :href="designerUrl(currentId)">بازگشت به طراحی</a>
        ·
        <a :href="resultUrl({ id: currentId, kind, group, subgroup })">نتیجهٔ امروز</a>
        <template v-if="graph.nodes.length">
          ·
          <button type="button" class="dz-linkish" @click="showGraph = !showGraph">
            {{ showGraph ? "بستن نمودار گراف" : "نمایش گراف" }}
          </button>
        </template>
      </div>
    </section>

    <p v-if="error" class="dz-msg is-error">{{ error }}</p>

    <section v-if="showGraph && graph.nodes.length" class="panel dz-bt-graph">
      <GraphCanvas
        :graph="graph"
        :specs="specs"
        :colors="colors"
        :categories="catalog.categories"
        :selected="[]"
        :explain="null"
        readonly
      />
    </section>

    <template v-if="report && report.signals">
      <!-- ── headline ───────────────────────────────────────────────── -->
      <section class="panel dz-bt-head">
        <h2 class="dz-bt-title">
          {{ title || "فیلتر بدون نام" }}
          <span class="muted small">
            {{ fa(report.from) }} تا {{ fa(report.to) }}
          </span>
        </h2>
        <div class="dz-bt-tiles">
          <div class="dz-bt-tile">
            <b>{{ fa(report.signals.toLocaleString("en-US")) }}</b>
            <span>سیگنال</span>
          </div>
          <div class="dz-bt-tile">
            <b>{{ fa(report.scanned) }}</b>
            <span>نماد بررسی‌شده</span>
          </div>
          <div class="dz-bt-tile" :class="signClass(report.stats[String(report.hold)]?.excess)">
            <b>{{ pct(report.stats[String(report.hold)]?.excess) }}</b>
            <span>مازاد بر بازار ({{ fa(report.hold) }} کندل)</span>
          </div>
          <div class="dz-bt-tile">
            <b>{{ pct(report.stats[String(report.hold)]?.win, 1) }}</b>
            <span>نرخ برد</span>
          </div>
          <div class="dz-bt-tile is-dn">
            <b>{{ pct(report.drawdown, 1) }}</b>
            <span>بیشینهٔ افت</span>
          </div>
          <div class="dz-bt-tile">
            <b>{{ pct(report.exposure, 0) }}</b>
            <span>زمان در بازار</span>
          </div>
        </div>
      </section>

      <!-- ── the horizon table ──────────────────────────────────────── -->
      <section class="panel dz-bt-stats">
        <div class="panel-head"><h2>بازده پس از سیگنال</h2></div>
        <div class="table-wrap">
          <table class="grid dz-bt-table">
            <thead>
              <tr>
                <th scope="col">&nbsp;</th>
                <th v-for="h in report.horizons" :key="h" scope="col">
                  {{ fa(h) }} کندل بعد
                </th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th scope="row">میانگین</th>
                <td v-for="h in report.horizons" :key="h" :class="signClass(report.stats[String(h)].avg)">
                  {{ pct(report.stats[String(h)].avg) }}
                </td>
              </tr>
              <tr>
                <th scope="row">میانه</th>
                <td v-for="h in report.horizons" :key="h" :class="signClass(report.stats[String(h)].median)">
                  {{ pct(report.stats[String(h)].median) }}
                </td>
              </tr>
              <tr>
                <th scope="row">نرخ برد</th>
                <td v-for="h in report.horizons" :key="h">
                  {{ pct(report.stats[String(h)].win, 1) }}
                </td>
              </tr>
              <tr class="dz-bt-sep">
                <th scope="row">بازار در همان روزها</th>
                <td v-for="h in report.horizons" :key="h" :class="signClass(report.stats[String(h)].bench)">
                  {{ pct(report.stats[String(h)].bench) }}
                </td>
              </tr>
              <tr class="dz-bt-key-row">
                <th scope="row">مازاد بر بازار</th>
                <td v-for="h in report.horizons" :key="h" :class="signClass(report.stats[String(h)].excess)">
                  <b>{{ pct(report.stats[String(h)].excess) }}</b>
                </td>
              </tr>
              <tr>
                <th scope="row">بهترین / بدترین</th>
                <td v-for="h in report.horizons" :key="h" class="dz-bt-minmax">
                  <span class="is-up">{{ pct(report.stats[String(h)].best, 0) }}</span>
                  <span class="is-dn">{{ pct(report.stats[String(h)].worst, 0) }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="muted small note">
          ورود با قیمت <b>باز شدن کندل بعد از سیگنال</b> و خروج با قیمت پایانی، پس از کسر
          {{ pct(report.cost, 1) }} کارمزد. «بازار» یعنی خرید همهٔ نمادهای همان روز با همان
          دورهٔ نگهداری و همان کارمزد — پس «مازاد» فقط انتخاب نماد را می‌سنجد.
        </p>
      </section>

      <!-- ── equity ─────────────────────────────────────────────────── -->
      <section class="panel dz-bt-equity">
        <div class="panel-head">
          <h2>منحنی سرمایه</h2>
          <span class="muted small">
            سبد هم‌وزن از سیگنال‌های باز، هر کدام {{ fa(report.hold) }} کندل نگهداری
          </span>
        </div>
        <EquityChart :curve="report.curve" :bench="report.bench_curve" :dates="report.dates" />
        <p class="muted small note">
          بیشینهٔ افت فیلتر {{ pct(report.drawdown, 1) }} در برابر
          {{ pct(report.bench_drawdown, 1) }} بازار. فیلتر تنها
          {{ pct(report.exposure, 0) }} روزها موقعیت باز داشته و بقیهٔ روزها نقد بوده است.
        </p>
      </section>

      <!-- ── caveats ────────────────────────────────────────────────── -->
      <section class="panel dz-bt-caveats">
        <div class="panel-head"><h2>آنچه این عدد نمی‌گوید</h2></div>
        <ul class="dz-bt-cav">
          <li v-if="thin" class="is-warn">
            فقط {{ fa(report.signals) }} سیگنال در کل بازه — این نمونه برای نتیجه‌گیری کوچک
            است. بازهٔ بلندتری انتخاب کنید یا شرط را کمی آزادتر بگیرید.
          </li>
          <li v-if="report.skipped.lock">
            {{ fa(report.skipped.lock.toLocaleString("en-US")) }} ورود کنار گذاشته شد چون
            کندل بعد در <b>صف خرید</b> باز شده بود و خرید در آن قیمت ممکن نبود.
          </li>
          <li v-if="report.skipped.halt">
            {{ fa(report.skipped.halt.toLocaleString("en-US")) }} ورود کنار گذاشته شد چون
            نماد در آن روز <b>معامله نشده</b> بود.
          </li>
          <li v-if="report.skipped.bad">
            {{ fa(report.skipped.bad.toLocaleString("en-US")) }} سیگنال کنار گذاشته شد چون
            سری قیمت تعدیل‌شدهٔ آن نماد در آن بازه گسسته است.
          </li>
          <li>
            نمادهایی که امروز در بازار نیستند (حذف‌شده یا متوقف دائم) در این بک‌تست هم
            نیستند؛ پس نتیجه کمی <b>خوش‌بینانه‌تر</b> از واقعیت است.
          </li>
          <li v-if="report.clipped">
            گراف تاریخچهٔ بیشتری از سقف موجود لازم داشت، بنابراین بازه کوتاه‌تر از
            درخواست شماست.
          </li>
          <li v-if="report.errors">
            {{ fa(report.errors) }} نماد به خطا خورد و در نتیجه نیامد.
          </li>
        </ul>
      </section>

      <!-- ── trades ─────────────────────────────────────────────────── -->
      <section class="panel dz-bt-trades">
        <div class="panel-head">
          <h2>آخرین معاملات</h2>
          <span class="muted small">
            {{ fa(report.trades.length) }} از {{ fa(report.signals.toLocaleString("en-US")) }}
          </span>
        </div>
        <div class="table-wrap">
          <table class="grid">
            <thead>
              <tr>
                <th scope="col">نماد</th>
                <th scope="col">نام</th>
                <th scope="col">تاریخ ورود</th>
                <th scope="col">قیمت ورود</th>
                <th v-for="h in report.horizons" :key="h" scope="col">+{{ fa(h) }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(t, i) in report.trades" :key="i" class="is-clickable"
                  @click="openRow($event, t)">
                <td class="ticker">{{ t.ticker }}</td>
                <td class="name">{{ t.name }}</td>
                <td>{{ fa(t.date) }}</td>
                <td class="num">{{ money(t.entry) }}</td>
                <td v-for="h in report.horizons" :key="h" class="num"
                    :class="[signClass(t['r' + h] as number), 'r' + h === holdKey ? 'is-hold' : '']">
                  {{ pct(t["r" + h] as number, 1) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>

    <section v-else-if="report" class="panel">
      <p class="muted note">
        این فیلتر در بازهٔ انتخاب‌شده هیچ سیگنالی نداشت
        ({{ fa(report.scanned) }} نماد، {{ fa(report.from) }} تا {{ fa(report.to) }}).
        بازهٔ بلندتری بگیرید یا شرط را آزادتر کنید.
      </p>
    </section>

    <p v-else-if="busy" class="muted note">در حال بازپخش فیلتر روی تاریخچه…</p>
  </div>
</template>
