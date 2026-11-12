#!/usr/bin/env node
/**
 * capture_yggdrasil.js — fixed-frame-rate capture for the YGGDRASIL artefacts.
 *
 * The two clips shipped at 30 fps and 10 fps because CLEAVE could only be
 * screen-grabbed in real time (it exposed no draw hook) while SHARD_01 was
 * stepped at 100 ms. This driver removes the difference: it sets
 * `window.__capture = true` before any page script runs, then calls
 * `window.__draw(t)` once per frame at a fixed rate and screenshots the result.
 * Duration and frame rate are then a property of the timeline, not of the
 * machine's load.
 *
 * Usage:
 *   node tools/capture_yggdrasil.js <in.html> <out.mp4> [fps] [<in2> <out2> [fps]]
 *
 * Output: one PNG sequence per job in a temp dir next to the mp4, encoded with
 * libx264 at fixed CFR, then the PNGs are removed. Prints the mp4's probed
 * frame count, fps and duration at the end, so the report is measured, not
 * asserted. For a page that exposes `window.__labels()` it also prints the
 * label geometry measured by the drawing browser.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawnSync } = require('child_process');
const { createRequire } = require('module');

// puppeteer is not a repo dependency; it lives in whichever node_modules the
// host installed it into. Resolve it from the usual roots instead of assuming
// the repo has one (a require that throws on every other host is not a tool).
function loadPuppeteer() {
  const roots = [
    process.env.PUPPETEER_ROOT,
    process.cwd(),
    path.resolve(__dirname, '..'),
    path.resolve(__dirname, '../..'),
    path.join(os.homedir(), 'node_modules'),
    path.join(process.env.APPDATA || '', 'npm/node_modules'),
  ].filter(Boolean);
  const tried = [];
  for (const root of roots) {
    try {
      const req = createRequire(path.join(root, '__resolve__.js'));
      return req('puppeteer');
    } catch (e) { tried.push(root); }
  }
  throw new Error('puppeteer not found. Set PUPPETEER_ROOT to the dir containing ' +
    'node_modules/puppeteer. Tried: ' + tried.join(', '));
}
const puppeteer = loadPuppeteer();

function log(...a) { console.log(...a); }

function ffprobe(mp4) {
  const r = spawnSync('ffprobe', ['-v', 'error', '-select_streams', 'v:0',
    '-count_frames', '-show_entries',
    'stream=nb_read_frames,r_frame_rate,width,height:format=duration,size',
    '-of', 'default=noprint_wrappers=1', mp4], { encoding: 'utf8', shell: true });
  return (r.stdout || '').trim();
}

async function capture(browser, html, mp4, fps) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ygg_'));
  const page = await browser.newPage();
  await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 1 });
  await page.evaluateOnNewDocument(() => { window.__capture = true; });
  const url = 'file:///' + path.resolve(html).replace(/\\/g, '/');
  await page.goto(url, { waitUntil: 'load' });
  await page.evaluate(() => (document.fonts ? document.fonts.ready : null));
  await page.evaluate(() => {
    window.__done = false;
    const f = document.getElementById('fade');
    if (f) f.style.transition = 'none', f.style.opacity = 0;
  });

  const total = await page.evaluate(
    () => (typeof window.__TOTAL === 'number' ? window.__TOTAL
      : (typeof window.__duration === 'number' ? window.__duration : null)));
  if (!total) throw new Error(html + ': no window.__TOTAL/__duration — cannot fix the frame count');

  const frames = Math.round(total / 1000 * fps);
  log(`  timeline ${total} ms -> ${frames} frames at ${fps} fps`);

  // Prefer the virtual-clock stepper: it pins the page's clock and resolves
  // after two painted frames, so the screenshot is deterministic and the
  // compositor is awake. Fall back to a bare __draw + two rAFs.
  const stepper = await page.evaluate(() =>
    typeof window.__step === 'function' ? 'step'
      : (typeof window.__draw === 'function' ? 'draw' : 'none'));
  if (stepper === 'none') throw new Error(html + ': no window.__step or window.__draw');
  const step = (t) => stepper === 'step'
    ? page.evaluate((ms) => window.__step(ms), t)
    : page.evaluate((ms) => new Promise((r) => { window.__draw(ms);
        requestAnimationFrame(() => requestAnimationFrame(() => r(ms))); }), t);
  log(`  stepper: window.__${stepper}`);

  const hasLabels = await page.evaluate(() => typeof window.__labels === 'function');
  if (hasLabels) {
    await step(total - 500);
    const geo = await page.evaluate(() => window.__labels());
    log(`  label geometry: ${geo.overlaps} overlapping pairs of ${geo.pairs}`);
    const xs = geo.boxes.map(b => b.x), ys = geo.boxes.map(b => b.y);
    log(`  label extent: x ${Math.min(...xs).toFixed(0)}..${Math.max(...xs).toFixed(0)}`
      + `, y ${Math.min(...ys).toFixed(0)}..${Math.max(...ys).toFixed(0)}`);
    for (const b of geo.boxes) log(`    ${b.name.padEnd(14)} (${b.x}, ${b.y})`);
  }

  const t0 = Date.now();
  for (let i = 0; i < frames; i++) {
    const t = i * 1000 / fps;
    await step(t);
    await page.screenshot({
      path: path.join(tmp, String(i).padStart(5, '0') + '.png'),
      clip: { x: 0, y: 0, width: 1920, height: 1080 },
    });
    if (i % 150 === 0) log(`    frame ${i}/${frames}  ${((Date.now() - t0) / 1000).toFixed(0)}s`);
  }
  const drawS = (Date.now() - t0) / 1000;

  const enc = spawnSync('ffmpeg', ['-y', '-loglevel', 'error',
    '-framerate', String(fps), '-i', path.join(tmp, '%05d.png'),
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '19', '-pix_fmt', 'yuv420p',
    '-movflags', '+faststart', path.resolve(mp4)], { encoding: 'utf8', shell: true });
  if (enc.status !== 0) {
    log('  ffmpeg FAILED: ' + (enc.stderr || '').slice(0, 500));
    return { ok: false, tmp, frames, drawS };
  }
  fs.rmSync(tmp, { recursive: true, force: true });
  log(`  captured ${frames} frames in ${drawS.toFixed(0)}s -> ${mp4}`);
  log('  probe: ' + ffprobe(mp4).replace(/\n/g, ' | '));
  return { ok: true, frames, drawS };
}

(async () => {
  const argv = process.argv.slice(2);
  if (argv.length < 2) {
    console.error('usage: node capture_yggdrasil.js <in.html> <out.mp4> [fps] [<in2> <out2> [fps]]');
    process.exit(2);
  }
  const jobs = [];
  for (let i = 0; i < argv.length;) {
    const html = argv[i++];
    let fps = 30;
    if (/^\d+$/.test(argv[i] || '')) fps = parseInt(argv[i++], 10);
    const out = argv[i++];
    if (!out) { console.error('missing out.mp4 for ' + html); process.exit(2); }
    jobs.push({ html, out, fps });
  }

  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--font-render-hinting=none',
      '--disable-lcd-text', '--force-color-profile=srgb'],
  });
  let bad = 0;
  try {
    for (const j of jobs) {
      log(`\n[job] ${path.basename(j.html)} -> ${path.basename(j.out)} @ ${j.fps} fps`);
      const r = await capture(browser, j.html, j.out, j.fps);
      if (!r.ok) bad++;
    }
  } finally {
    await browser.close();
  }
  process.exit(bad ? 1 : 0);
})();
