// Functional + timing check for the azazael wasm package.
//
// The board row says "fix wasm-bindgen to export cosine_similarity_simd". Measured
// 2026-11-01: the export already exists in lib.rs AND in the built binary. What was
// never checked is whether the exported name means anything -- the `simd` cargo
// feature is `simd = []` (a no-op) and there is no `.cargo/config.toml`, so LLVM is
// not told `target-feature=+simd128` and the "simd" function is scalar code with a
// vector name. So this script measures the two things a name cannot: does it return
// the right number, and does the build with +simd128 return the same number faster.
//
// Usage: node bench.mjs [path-to-pkg]
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const pkgDir = process.argv[2] || "pkg";
const mod = await import(pathToFileURL(resolve(pkgDir, "azazael_wasm.js")).href);
// The glue exports an async initialiser and the raw functions call through `wasm`,
// which is undefined until it resolves -- a caller that imports and calls gets
// "Cannot read properties of undefined (reading 'cosine_similarity_simd')" (measured
// 2026-11-01: that is exactly how this script failed on its first run). BOTH the
// old and the new build must be initialised, so the init stays here whatever the
// target is.
if (typeof mod.default === "function") {
  // The `--target web` glue resolves its argument with fetch(), so a file:// URL
  // throws an undici error from inside wasm-bindgen. Compile the module here and
  // hand the glue a WebAssembly.Module: no fetch, no path resolution, and the
  // init path is the same code either way.
  const { readFile } = await import("node:fs/promises");
  const bytes = await readFile(resolve(pkgDir, "azazael_wasm_bg.wasm"));
  await mod.default(await WebAssembly.compile(bytes));
}
const { cosine_similarity_simd, cosine_similarity_wasm } = mod;

// A deterministic vector pair whose cosine we can compute in JS exactly enough to
// catch a broken kernel: one-hot overlap plus noise, so the answer is not 0 or 1.
const DIM = 1536;
const a = new Float32Array(DIM);
const b = new Float32Array(DIM);
let seed = 12345;
const rnd = () => {
  seed = (seed * 1103515245 + 12345) & 0x7fffffff;
  return seed / 0x7fffffff - 0.5;
};
for (let i = 0; i < DIM; i++) {
  a[i] = i % 7 === 0 ? 1 + rnd() : rnd() * 0.3;
  b[i] = i % 7 === 0 ? 1 + rnd() : rnd() * 0.3;
}

const ref = (() => {
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < DIM; i++) { dot += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; }
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
})();

const gotSimd = cosine_similarity_simd(a, b);
const gotScalar = cosine_similarity_wasm(a, b);
const dev = Math.abs(gotSimd - ref);

console.log(`dims=${DIM} ref=${ref.toFixed(7)} simd_fn=${gotSimd.toFixed(7)} ` +
            `scalar_fn=${gotScalar.toFixed(7)} max_dev=${dev.toExponential(2)}`);
if (dev > 1e-5) {
  console.error("FAIL: cosine_similarity_simd disagrees with the JS reference");
  process.exit(1);
}

// Timing. Same call, same data, order alternated so a warm cache cannot favour one.
const ITERS = 20000;
function bench(fn, label) {
  for (let i = 0; i < 2000; i++) fn(a, b);            // warm
  const t0 = process.hrtime.bigint();
  let sink = 0;
  for (let i = 0; i < ITERS; i++) sink += fn(a, b);
  const t1 = process.hrtime.bigint();
  const us = Number(t1 - t0) / 1000 / ITERS;
  return { label, us, sink };
}
const r1 = bench(cosine_similarity_simd, "simd_fn");
const r2 = bench(cosine_similarity_wasm, "scalar_fn");
const r3 = bench(cosine_similarity_simd, "simd_fn#2");
console.log(`${r1.label}: ${r1.us.toFixed(3)} us/call`);
console.log(`${r2.label}: ${r2.us.toFixed(3)} us/call`);
console.log(`${r3.label}: ${r3.us.toFixed(3)} us/call`);
console.log(`RESULT ok dev=${dev.toExponential(2)} simd_us=${r1.us.toFixed(3)} ` +
            `scalar_us=${r2.us.toFixed(3)} ratio=${(r2.us / r1.us).toFixed(2)}`);
