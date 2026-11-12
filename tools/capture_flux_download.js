#!/usr/bin/env node
/**
 * capture_flux_download.js — a live, visible download progress card.
 *
 * WHY THIS EXISTS. The user said "I CAN NOT SEE IT": the download stats were
 * going only into my tool output, which is not a place a person can watch.
 * A download that takes 1.5 hours and reports into a void feels broken, and the
 * instinct to check on it every thirty seconds is the problem. So the progress
 * becomes an ARTEFACT -- a file on disk that updates itself -- rather than
 * something I have to narrate.
 *
 * HOUSE STACK: Puppeteer, not PIL. SVG/HTML authored in the page, screenshot
 * captured. 2x deviceScaleFactor, so a 900px card renders at 1800px.
 *
 * IT RE-RENDERS EVERY TICK. Same process loops: sample the files, rewrite the
 * HTML, re-screenshot. Open the PNG any time; each refresh is current.
 */
const fs = require("fs");
const path = require("path");
const puppeteer = require("puppeteer");

const BASE =
  "C:\\Users\\clint\\AppData\\Local\\Programs\\AI Playground\\resources\\models\\ComfyUI";
const OUTDIR = "C:\\Users\\clint\\azazael\\dashboard";
const HTML = path.join(OUTDIR, "flux_download.html");
const PNG = path.join(OUTDIR, "flux_download.png");
const LOG = path.join(OUTDIR, "flux_download_state.json");

const COMPONENTS = [
  { dir: "diffusion_models", file: "flux1-schnell-fp8-e4m3fn.safetensors", size: 11891329784, label: "UNet", note: "FLUX.1-schnell FP8 e4m3fn" },
  { dir: "text_encoders", file: "t5xxl_fp8_e4m3fn_scaled.safetensors", size: 5157348688, label: "Text encoder", note: "T5-XXL FP8 scaled" },
  { dir: "clip", file: "clip_l.safetensors", size: 246144152, label: "CLIP-L", note: "246 MB" },
  { dir: "vae", file: "flux-vae-bf16.safetensors", size: 167664710, label: "VAE", note: "flux-vae bf16" },
];

const GB = 1073741824;
const pad = (n, w = 2) => String(n).padStart(w, "0");
const hhmmss = (s) => {
  if (s < 0 || !isFinite(s)) return "--:--:--";
  s = Math.round(s);
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
};

function sample() {
  let total = 0, want = 0, complete = 0;
  const parts = COMPONENTS.map((c) => {
    const p = path.join(BASE, c.dir, c.file);
    let got = 0;
    try { got = fs.statSync(p).size; } catch (e) { got = 0; }
    const pct = Math.min(100, (100 * got) / c.size);
    if (got >= c.size) complete++;
    total += got; want += c.size;
    return { ...c, got, pct, done: got >= c.size, present: got > 0 };
  });
  return { parts, total, want, complete, pct: (100 * total) / want };
}

function rateBetween(a, b) {
  const dt = (b.t - a.t) / 1000;
  if (dt <= 0) return { inst: 0, avg: 0 };
  const inst = (b.total - a.total) / GB / dt * 1024; // MB/s
  const avg = b.total / GB / (b.t / 1000) * 1024;
  return { inst, avg };
}

function buildHtml(s, rates, startedAt) {
  const eta = rates.inst > 50 ? (s.want - s.total) / GB * 1024 / rates.inst : -1;
  const elapsed = (Date.now() - startedAt) / 1000;
  const overallPct = s.pct;

  const rows = s.parts
    .map((p) => {
      const state = p.done ? "DONE" : p.present ? "downloading" : "queued";
      const bar = p.done
        ? 100
        : Math.max(0.4, p.pct);
      return `
      <div class="row ${state}">
        <div class="rl">
          <span class="lbl">${p.label}</span>
          <span class="note">${p.note}</span>
        </div>
        <div class="track"><div class="fill" style="width:${bar}%"></div></div>
        <div class="rn">
          <span class="pct">${p.pct.toFixed(1)}%</span>
          <span class="sz">${(p.got / GB).toFixed(2)} / ${(p.size / GB).toFixed(2)} GB</span>
        </div>
      </div>`;
    })
    .join("");

  return `<!doctype html><html><head><meta charset="utf-8"><style>
  @font-face{font-family:mono;src:local("Cascadia Mono"),local("Consolas")}
  *{box-sizing:border-box;margin:0;padding:0}
  body{width:900px;background:#0d1117;color:#e6edf3;
       font-family:"Cascadia Mono",Consolas,monospace;padding:34px 38px}
  h1{font-size:23px;font-weight:700;letter-spacing:.4px}
  h1 span{color:#58a6ff}
  .sub{color:#8b949e;font-size:12.5px;margin-top:7px;letter-spacing:.3px}
  .hero{margin:26px 0 8px;display:flex;align-items:baseline;gap:14px}
  .pct{font-size:64px;font-weight:700;color:#58a6ff;line-height:1}
  .of{font-size:15px;color:#8b949e}
  .trackO{height:16px;background:#161b22;border-radius:8px;overflow:hidden;
          border:1px solid #30363d;margin:14px 0 22px}
  .fillO{height:100%;background:linear-gradient(90deg,#1f6feb,#58a6ff);
         border-radius:8px;transition:width .8s ease}
  .grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}
  .cell{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 14px}
  .k{font-size:10.5px;color:#8b949e;letter-spacing:1.1px;text-transform:uppercase}
  .v{font-size:20px;font-weight:700;margin-top:5px}
  .v.blue{color:#58a6ff}.v.green{color:#3fb950}.v.amber{color:#d29922}
  .row{display:grid;grid-template-columns:190px 1fr 168px;align-items:center;
       gap:14px;padding:11px 0;border-top:1px solid #21262d}
  .rl{display:flex;flex-direction:column;gap:2px}
  .lbl{font-size:13.5px;font-weight:600}
  .note{font-size:10.5px;color:#6e7681}
  .track{height:9px;background:#161b22;border-radius:5px;overflow:hidden;
         border:1px solid #30363d}
  .fill{height:100%;background:#3fb950;border-radius:5px;transition:width .8s ease}
  .downloading .fill{background:linear-gradient(90deg,#1f6feb,#58a6ff)}
  .queued .fill{background:#30363d}
  .rn{display:flex;flex-direction:column;align-items:flex-end;gap:2px}
  .rn .pct{font-size:13.5px;font-weight:700}
  .rn .sz{font-size:10.5px;color:#6e7681}
  .done .pct{color:#3fb950}
  .foot{margin-top:22px;padding-top:14px;border-top:1px solid #21262d;
        font-size:11px;color:#6e7681;line-height:1.7}
  .dot{display:inline-block;width:7px;height:7px;border-radius:50%;
       background:#3fb950;margin-right:7px;
       animation:p 1.6s ease-in-out infinite}
  @keyframes p{0%,100%{opacity:1}50%{opacity:.25}}
  </style></head><body>
  <h1>FLUX<span>.1-schnell</span> FP8 &mdash; local image stack</h1>
  <div class="sub">Intel Arc 140T &middot; 7.80 GB XPU &middot; headless ComfyUI &middot; 4 steps &middot; 1024&times;1360</div>

  <div class="hero">
    <div class="pct">${overallPct.toFixed(1)}%</div>
    <div class="of">${(s.total / GB).toFixed(2)} of ${(s.want / GB).toFixed(2)} GB &middot; ${s.complete}/4 files</div>
  </div>
  <div class="trackO"><div class="fillO" style="width:${overallPct.toFixed(2)}%"></div></div>

  <div class="grid">
    <div class="cell"><div class="k">Speed now</div><div class="v blue">${rates.inst.toFixed(1)}<span style="font-size:11px;color:#8b949e"> MB/s</span></div></div>
    <div class="cell"><div class="k">Average</div><div class="v">${rates.avg.toFixed(1)}<span style="font-size:11px;color:#8b949e"> MB/s</span></div></div>
    <div class="cell"><div class="k">Remaining</div><div class="v amber">${hhmmss(eta)}</div></div>
    <div class="cell"><div class="k">Elapsed</div><div class="v">${hhmmss(elapsed)}</div></div>
  </div>

  ${rows}

  <div class="foot">
    <span class="dot"></span>live &mdash; this card re-renders every tick; open
    <b>azazael/dashboard/flux_download.png</b><br>
    Link is ~3 MB/s, so expect ~1.5 h for 16.26 GB. curl runs with <b>-C -</b>,
    so a dropped connection resumes instead of restarting.<br>
    UNet is 11.89 GB on a 7.80 GB card, so ComfyUI will offload to system RAM &mdash;
    free RAM, not VRAM, is the binding constraint. Restart before the run.
  </div>
  </body></html>`;
}

(async () => {
  const tickMs = Number(process.env.TICK_MS || 20000);
  const startedAt = fs.existsSync(LOG) ? JSON.parse(fs.readFileSync(LOG, "utf8")).startedAt : Date.now();
  fs.mkdirSync(OUTDIR, { recursive: true });
  fs.writeFileSync(LOG, JSON.stringify({ startedAt }));

  const browser = await puppeteer.launch({ headless: "new", args: ["--no-sandbox"] });
  const page = await browser.newPage();
  await page.setViewport({ width: 900, height: 620, deviceScaleFactor: 2 });

  let prev = { ...sample(), t: Date.now() };
  console.log("rendering", PNG, "every", tickMs, "ms -- Ctrl-C to stop");

  for (;;) {
    const s = sample();
    const cur = { ...s, t: Date.now() };
    const rates = rateBetween(prev, cur);
    const html = buildHtml(s, rates, startedAt);
    fs.writeFileSync(HTML, html, "utf8");
    await page.setContent(html, { waitUntil: "load" });
    await page.screenshot({ path: PNG, type: "png" });
    const eta = rates.inst > 50 ? (s.want - s.total) / GB * 1024 / rates.inst : -1;
    console.log(
      `${new Date().toLocaleTimeString()}  ${s.pct.toFixed(2)}%  ` +
      `${(s.total / GB).toFixed(2)}/${(s.want / GB).toFixed(2)}GB  ` +
      `${rates.inst.toFixed(1)} MB/s  ETA ${hhmmss(eta)}  ${s.complete}/4`
    );
    prev = cur;
    if (s.complete >= 4) {
      console.log("ALL COMPONENTS COMPLETE");
      break;
    }
    await new Promise((r) => setTimeout(r, tickMs));
  }
  await browser.close();
})();
