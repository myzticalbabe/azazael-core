// Render a highlight clip of the full-track aurora field.
//
//   node tools/render_highlight.mjs                     # 20 s from 0:30
//   node tools/render_highlight.mjs --start 96 --seconds 12 --out C:/tmp/x.mp4
//
// Chrome draws each frame from an explicit index (never the clock), CDP captures
// it, ffmpeg encodes. Follows the render playbook's verification: frame count,
// three distinct hashes, and the three frames read back out of the encoded file.
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, rmSync, existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import { startServer, launch } from "./cdp.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const arg = (name, dflt) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : dflt;
};
const START_S = Number(arg("start", 30));
const SECONDS = Number(arg("seconds", 20));
const FPS = 30;
const OUT = resolve(arg("out", "C:/tmp/vw_highlight.mp4"));
const WORK = resolve("C:/tmp/vw_highlight");
const FRAMES = join(WORK, "frames");
const PORT = 8132;
const FIRST = Math.round(START_S * FPS);
const N = Math.round(SECONDS * FPS);

const run = (cmd, args, opts = {}) => {
  const r = spawnSync(cmd, args, { encoding: "utf8", ...opts });
  if (r.status !== 0) throw new Error(`${cmd} ${args.join(" ")} -> ${r.status}\n${r.stderr || ""}`);
  return r.stdout;
};

if (!existsSync(join(ROOT, "demo", "assets", "spectrum.json"))) {
  console.log("spectrum.json missing — baking first");
  run("node", [join(ROOT, "tools", "bake_spectrum.mjs")], { stdio: "inherit" });
}
// A stale frame must not be able to satisfy the count check.
rmSync(FRAMES, { recursive: true, force: true });
mkdirSync(FRAMES, { recursive: true });

const server = await startServer(ROOT, PORT);
const page = await launch({ width: 960, height: 540, port: 9338, label: "vw-render" });
let failed = null;

try {
  await page.goto(`http://127.0.0.1:${PORT}/demo/aurora.html?n=${N}&start=${FIRST}`);
  await page.waitFor("window.__ready === true", 60000);
  const meta = await page.evaluate("window.__meta");
  console.log(`spectrum: ${meta.frames} frames @${meta.fps}fps, track ${meta.duration}s`);
  console.log(`rendering ${N} frames (${SECONDS}s from ${START_S}s) at 960x540…`);

  const t0 = Date.now();
  for (let i = 0; i < N; i++) {
    const painted = await page.evaluate(`window.renderFrame(${FIRST + i})`);
    if (!painted) throw new Error(`frame ${i} painted nothing`);
    await page.screenshot(join(FRAMES, `f_${String(i).padStart(5, "0")}.png`));
    if ((i + 1) % 150 === 0) {
      const rate = (i + 1) / ((Date.now() - t0) / 1000);
      console.log(`  ${i + 1}/${N}  ${rate.toFixed(1)} fps capture`);
    }
  }
  const captured = readdirSync(FRAMES).filter((f) => f.endsWith(".png"));
  if (captured.length !== N) throw new Error(`captured ${captured.length} of ${N} frames`);
  console.log(`captured ${captured.length} frames in ${((Date.now() - t0) / 1000).toFixed(1)}s`);

  run("ffmpeg", ["-y", "-loglevel", "error", "-framerate", String(FPS),
    "-i", join(FRAMES, "f_%05d.png"),
    "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-r", String(FPS), OUT]);

  // --- verification: count, three distinct hashes, then read the encoded frames
  const hash = (f) => createHash("sha256")
    .update(readFileSync(join(FRAMES, f))).digest("hex").slice(0, 16);
  const picks = [0, Math.floor(N / 2), N - 1].map((i) => `f_${String(i).padStart(5, "0")}.png`);
  const hashes = picks.map(hash);
  const distinct = new Set(hashes).size;
  const probe = run("ffprobe", ["-v", "error", "-select_streams", "v:0",
    "-show_entries", "stream=width,height,nb_frames,duration", "-of", "default=nw=1", OUT]);
  const checks = [];
  for (const [i, name] of [[0, "first"], [Math.floor(N / 2), "mid"], [N - 1, "last"]]) {
    const out = join(WORK, `_chk_${name}.png`);
    run("ffmpeg", ["-y", "-loglevel", "error", "-i", OUT,
      "-vf", `select=eq(n\\,${i})`, "-vsync", "0", "-frames:v", "1", out]);
    checks.push({ name, bytes: statSync(out).size });
  }

  console.log(`\nmp4   ${OUT}  ${(statSync(OUT).size / 1048576).toFixed(2)} MB`);
  console.log(`probe ${probe.trim().split("\n").join("  ")}`);
  console.log(`frame hashes ${picks.map((p, i) => `${p}=${hashes[i]}`).join("  ")}`);
  console.log(`distinct hashes ${distinct}/3 ${distinct === 3 ? "OK" : "FAIL (frozen frame)"}`);
  console.log(`read back ${checks.map((c) => `${c.name} ${(c.bytes / 1024).toFixed(0)} KB`).join("  ")}`);
  if (page.logs.length) console.log("console:", page.logs.join("\n"));
  if (distinct !== 3) failed = "three distinct frame hashes are required";
} catch (err) {
  failed = err.message;
} finally {
  page.close();
  server.kill();
}

if (failed) {
  console.error(`RENDER FAILED: ${failed}`);
  process.exit(1);
}
console.log(`\nok: ${N} frames, 3 distinct hashes, encoded and read back`);
