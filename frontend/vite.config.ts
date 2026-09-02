import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { resolve } from "node:path";

// Build straight into static/dist/, with FIXED filenames rather than hashed
// ones. Cache-busting is already solved on this project: the Jinja templates
// call asset_version(path), which stamps ?v=<file mtime> onto every asset, and
// nginx serves /static/ with a one-year immutable policy (order 00/05). A
// content hash in the filename would add a second, competing mechanism and
// leave orphaned bundles in static/dist on every build.
export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: resolve(__dirname, "../static/dist"),
    emptyOutDir: true,
    // The island is loaded with a plain <script type="module"> from a Jinja
    // template, so no manifest or HTML entry is involved.
    manifest: false,
    // Modest target: the audience is in Iran and older Android browsers are
    // common. es2019 covers optional chaining's absence gracefully via
    // transpilation while keeping the bundle small.
    target: "es2019",
    // H-5. `sourcemap: true` was unconditional, so static/dist/ shipped 11 .map
    // files — about 1.1 MB of the 1.3 MB directory — under nginx's one-year
    // immutable policy. That published the complete TypeScript source, the
    // filter-designer graph model included, to anyone who asked for it.
    //
    // It costs ordinary users nothing (browsers fetch maps only with devtools
    // open), so this is source disclosure rather than a performance problem —
    // but it is disclosure for no benefit.
    //
    // Keyed on --watch, which is exactly the distinction that matters here:
    // `npm run dev` is `vite build --watch` and wants maps, `npm run build` is
    // the one-shot build that produces the image and must not ship them.
    //
    // NOT `process.env.NODE_ENV !== "production"`, which is the obvious fix and
    // is wrong for this project: Vite sets NODE_ENV=production for EVERY
    // `vite build`, watching or not, so that condition silently strips maps
    // from development too — the one place they are actually used. Verified by
    // running both scripts and counting the .map files.
    //
    // If readable production stack traces are wanted later: generate them,
    // upload to Sentry at build time, and delete them from static/dist before
    // the image is built. Generating them is fine; serving them is not.
    sourcemap: process.argv.includes("--watch") || process.argv.includes("-w"),
    rollupOptions: {
      // One entry per island: market.html (order 08), performance.html, one
      // shared by the two scan pages (filters.html / strategies.html), the
      // screener, and the filter designer.
      input: {
        // M-2. Publishes format.ts on window.BN_FORMAT so static/js/app.js
        // stops carrying its own two copies of the same logic. Loaded on every
        // page by base.html, ahead of app.js. See the file's header for why a
        // global rather than an ES import.
        "legacy-format": resolve(__dirname, "src/legacy-format.ts"),
        market: resolve(__dirname, "src/market.ts"),
        perf: resolve(__dirname, "src/perf.ts"),
        scan: resolve(__dirname, "src/scan.ts"),
        screener: resolve(__dirname, "src/screener.ts"),
        // «طراحی فیلتر» — the node-graph filter designer (filter_engine.py),
        // and the results page «اجرا» navigates to. Two entries rather than one
        // because they share only the graph model and the canvas: the editor
        // carries the palette, the inspector and undo, none of which the results
        // page has any use for.
        designer: resolve(__dirname, "src/designer.ts"),
        designer_result: resolve(__dirname, "src/designer_result.ts"),
        // …and the backtest page, which replays the same graph over history.
        // A third entry rather than a tab on the results page: it shares only
        // the graph model and the read-only canvas, and loading its chart and
        // report tables into the page people hit on every «اجرا» would make the
        // common path pay for the occasional one.
        designer_backtest: resolve(__dirname, "src/designer_backtest.ts"),
      },
      output: {
        // Entry names stay FIXED because the templates stamp them with
        // ?v=<mtime> via asset_version(). Shared chunks (Vue, TanStack) get a
        // content hash instead: nothing stamps a chunk URL, and /static/ is
        // served immutable for a year, so a fixed chunk name would be served
        // stale after a rebuild.
        entryFileNames: "[name].js",
        chunkFileNames: "chunk-[name]-[hash].js",
        assetFileNames: "[name].[ext]",
      },
    },
  },
  define: {
    // Drops Vue's dev-only warning machinery from the production bundle.
    __VUE_OPTIONS_API__: "false",
    __VUE_PROD_DEVTOOLS__: "false",
    __VUE_PROD_HYDRATION_MISMATCH_DETAILS__: "false",
  },
});
