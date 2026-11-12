// Bake the full 218 s track to per-frame band energies.
//
//   node tools/bake_spectrum.mjs
//
// Writes demo/assets/spectrum.json: { rate, duration, fps, bands, frames:[{b,r}] }.
// 30 fps x 218 s = ~6500 frames of 32 bands. Uses the Rust DFT in the page, not
// a JS re-implementation, so the JSON and the renderer share one definition.
import { writeFileSync, mkdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import { startServer, launch } from "./cdp.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUT = join(ROOT, "demo", "assets", "spectrum.json");
const PORT = 8131;

mkdirSync(dirname(OUT), { recursive: true });
const server = await startServer(ROOT, PORT);
const page = await launch({ port: 9337, label: "vw-bake" });

try {
  await page.goto(`http://127.0.0.1:${PORT}/demo/bake.html`);
  await page.waitFor("window.__ready === true", 30000);
  console.log("decoding audio in-page…");
  const t0 = Date.now();
  const spec = await page.evaluate("window.__bake()");
  if (!spec?.frames?.length) throw new Error("bake returned no frames");

  writeFileSync(OUT, JSON.stringify(spec));
  const mb = statSync(OUT).size / 1048576;
  const peaks = spec.frames.map((f) => f.r);
  console.log(`frames ${spec.frames.length} @${spec.fps}fps  ${spec.bands} bands  ` +
    `win ${spec.win}  rate ${spec.rate}  duration ${spec.duration}s`);
  console.log(`rms  min ${Math.min(...peaks).toFixed(4)}  max ${Math.max(...peaks).toFixed(4)}`);
  console.log(`wrote ${OUT} (${mb.toFixed(2)} MB) in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  if (page.logs.length) console.log("console:", page.logs.join("\n"));
} finally {
  page.close();
  server.kill();
}
