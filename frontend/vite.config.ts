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
    sourcemap: true,
    rollupOptions: {
      // One entry per island: market.html (order 08), performance.html, one
      // shared by the two scan pages (filters.html / strategies.html), the
      // screener, and the filter designer.
      input: {
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
