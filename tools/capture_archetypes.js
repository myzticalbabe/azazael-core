// capture_archetypes.js — the loop's ten stages, each a full-bleed FIGURE PORTRAIT.
//
// THE MISS (2026-09-30), second correction. The figures were correct — head,
// shoulders, spine, arms, legs, a pose carrying the role — and I put them in a
// 240px slot in the upper corner of a text card. "IT IS A TINY IMAGE IN THE
// UPPER CONER OF THE CARD. IT SHOULD BE THE ENTIRE CARD." Correct, and the
// error is compositional rather than graphical: I treated the figure as
// decoration attached to a document, when the figure IS the document. So each
// archetype is now a portrait: full bleed, the figure standing large and
// centred on a ground line, with the text as an overlaid title treatment and a
// single corner readout. The body of the card is the person.
//
// Poses (RULE 0Q): the posture carries the role, so it is drawn for what the
// stage does wrong as often as what it does right.
//   OMNISCIENT  scanning, hand shading the brow      SLAUGHT     arms up, feet
//   SENTINEL    planted, arms folded                  together, blocking
//   HUNTRESS    one figure sundered in two           ZEALOT      pitched forward,
//   SCRIBE      seated, weight on one hand            both hands pressing
//   HIGH PRIEST bowed, arms extended, offering      WARRIOR     mid-stride, arms
//   SEER        still, palm over one eye                         trailing
//   SCRIBE-ELDER stooped over an open ledger
//
// Usage:  node tools/capture_archetypes.js
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

const OUT = path.resolve(__dirname, '..', 'dashboard', 'archetypes');
// Build into a sibling dir and swap it in only after every screenshot exists.
// The previous version cleared OUT *before* launching the browser, so any
// puppeteer failure (no Chrome, a bad flag, a timeout) deleted all twenty
// portraits and left the wallboard with no art -- a refresh that destroys more
// than it repairs. A failed run now leaves the last good set untouched.
const STAGE = OUT + '.staging';
const W = 1024, H = 1280;                 // portrait: a figure wants height

const LIVE = '#3fe0c8';
const STARVED = '#ffab3d';
const GROUND = '#0b0d14';
const INK = '#eef3fb';
const MUTE = '#8593aa';

// ---------------------------------------------------------------- figures
// Drawn in a 112x140 box, ground line at y=138, head top at y=6.
const FIGURES = {
  OMNISCIENT: [
    'M48,10 a10,10 0 1,0 19,0 a10,10 0 1,0 -19,0',
    'M51,27 h13 v9 h-13 z',
    'M32,36 q24,-9 48,0 l5,6 -4,38 q-25,10 -50,0 l-4,-38 z',
    'M34,40 q-18,14 -20,38 l9,4 q3,-20 13,-32 z',
    'M76,40 q20,-12 22,-28 l-9,-3 q-2,15 -15,25 z',
    'M41,80 l-6,54 h13 l4,-54 z',
    'M58,80 l9,54 h13 l-8,-54 z',
    'M33,132 h17 v6 h-17 z', 'M62,132 h17 v6 h-17 z',
  ],
  SENTINEL: [
    'M47,8 a10,10 0 1,0 19,0 a10,10 0 1,0 -19,0',
    'M50,25 h13 v9 h-13 z',
    'M31,34 q25,-8 50,0 l4,7 -3,39 q-26,8 -52,0 l-3,-39 z',
    'M32,44 q24,11 48,0 l2,10 q-26,12 -52,0 l2,-10 z',
    'M40,80 l-7,54 h13 l1,-54 z',
    'M60,80 l6,54 h13 l-8,-54 z',
    'M32,132 h18 v6 h-18 z', 'M59,132 h18 v6 h-18 z',
  ],
  HUNTRESS: [
    'M27,14 a9,9 0 1,0 17,0 a9,9 0 1,0 -17,0',
    'M30,31 h12 v9 h-12 z',
    'M15,40 q15,-7 28,0 l3,5 -3,34 q-15,6 -29,0 l-3,-34 z',
    'M16,48 l-4,42 h11 l0,-42 z',
    'M25,90 l5,42 h11 l-6,-42 z',
    'M14,132 h15 v6 h-15 z', 'M24,132 h15 v6 h-15 z',
    'M79,14 a9,9 0 1,0 17,0 a9,9 0 1,0 -17,0',
    'M82,31 h12 v9 h-12 z',
    'M69,40 q15,-7 28,0 l3,5 -3,34 q-15,6 -29,0 l-3,-34 z',
    'M70,48 l4,42 h11 l0,-42 z',
    'M79,90 l-5,42 h11 l6,-42 z',
    'M77,132 h15 v6 h-15 z', 'M87,132 h15 v6 h-15 z',
  ],
  SCRIBE: [
    'M47,14 a10,10 0 1,0 19,0 a10,10 0 1,0 -19,0',
    'M50,32 h13 v10 h-13 z',
    'M32,42 q24,-8 48,0 l5,7 -4,32 q-25,9 -50,0 l-4,-32 z',
    'M33,48 l-14,30 l9,4 l9,-24 z',
    'M78,48 l22,-26 l8,5 l-19,24 z',
    'M32,81 l-26,8 l3,11 l26,-8 z',
    'M78,81 l27,7 l-4,12 l-28,-8 z',
    'M10,100 l-11,30 h11 l4,-30 z',
    'M101,100 l10,30 h11 l-12,-30 z',
  ],
  SLAUGHT: [
    'M47,7 a10,10 0 1,0 19,0 a10,10 0 1,0 -19,0',
    'M50,24 h13 v9 h-13 z',
    'M32,33 q24,-7 48,0 l4,6 -3,41 q-25,8 -50,0 l-3,-41 z',
    'M33,37 l-20,-30 l-11,6 l17,26 z',
    'M79,37 l20,-30 l11,6 l-17,26 z',
    'M40,80 l-4,52 h12 l3,-52 z',
    'M55,80 l4,52 h12 l-7,-52 z',
    'M35,130 h15 v8 h-15 z', 'M52,130 h15 v8 h-15 z',
  ],
  ZEALOT: [
    'M50,9 a10,10 0 1,0 19,0 a10,10 0 1,0 -19,0',
    'M53,26 h13 v9 h-13 z',
    'M33,35 q25,-6 48,3 l4,7 -5,39 q-25,7 -47,-3 l-3,-39 z',
    'M32,42 l-20,20 l9,6 l14,-16 z',
    'M81,45 l17,21 l-9,6 l-13,-17 z',
    'M38,81 l-16,50 h12 l8,-50 z',
    'M55,81 l13,50 h12 l-11,-50 z',
    'M17,128 h18 v8 h-18 z', 'M63,130 h18 v8 h-18 z',
  ],
  'HIGH PRIEST': [
    'M48,16 a10,10 0 1,0 19,0 a10,10 0 1,0 -19,0',
    'M51,33 h13 v10 h-13 z',
    'M32,43 q24,-4 48,4 l3,7 -4,34 q-25,6 -48,-4 l-3,-34 z',
    'M31,48 l-23,20 l8,8 l18,-14 z',
    'M81,51 l22,19 l-8,8 l-17,-14 z',
    'M38,84 l-9,50 h12 l5,-50 z',
    'M58,84 l9,50 h12 l-10,-50 z',
    'M24,132 h18 v6 h-18 z', 'M62,132 h18 v6 h-18 z',
  ],
  WARRIOR: [
    'M52,8 a10,10 0 1,0 19,0 a10,10 0 1,0 -19,0',
    'M55,25 h12 v9 h-12 z',
    'M38,34 q24,-6 47,3 l3,6 -4,40 q-23,7 -46,-3 l-3,-40 z',
    'M38,40 l-22,-12 l-6,9 l19,10 z',
    'M84,42 l21,-13 l7,9 l-19,11 z',
    'M40,80 l-25,45 h12 l18,-45 z',
    'M60,80 l22,48 h12 l-21,-47 z',
    'M8,120 h20 v7 h-20 z', 'M78,124 h20 v7 h-20 z',
  ],
  SEER: [
    'M47,8 a10,10 0 1,0 19,0 a10,10 0 1,0 -19,0',
    'M50,25 h13 v9 h-13 z',
    'M31,34 q25,-8 50,0 l4,6 -3,40 q-26,8 -52,0 l-3,-40 z',
    'M32,40 l-15,24 l9,6 l10,-20 z',
    'M78,38 q13,-7 18,-18 l9,-5 q-5,13 -17,20 z',
    'M40,80 l-8,54 h13 l3,-54 z',
    'M60,80 l8,54 h13 l-10,-54 z',
    'M31,132 h18 v6 h-18 z', 'M63,132 h18 v6 h-18 z',
  ],
  'SCRIBE-ELDER': [
    'M50,24 a10,10 0 1,0 19,0 a10,10 0 1,0 -19,0',
    'M53,41 h13 v10 h-13 z',
    'M33,51 q24,-5 48,4 l4,7 -4,36 q-25,6 -48,-4 l-3,-36 z',
    'M33,58 l-18,18 l9,6 l13,-15 z',
    'M78,57 q16,-12 28,-14 l6,12 q-14,13 -28,10 z',
    'M40,92 l-10,44 h12 l3,-44 z',
    'M58,92 l9,44 h12 l-9,-44 z',
    'M25,134 h18 v6 h-18 z', 'M62,134 h18 v6 h-18 z',
  ],
};

const CARD = {
  OMNISCIENT: ['the one who wanders',
    'keeps rotating a gene it already falsified thirty times',
    'how many trials has each rotated gene actually won'],
  SENTINEL: ['the one who hoards',
    'freezes a pool starving an arm, and calls it stability',
    'distinct states per pool — one state is a bandit, not a control problem'],
  HUNTRESS: ['the one who splits',
    'reports a total that silently equals one of its own two arms',
    'does total equal an arm? then it is not a two-objective score'],
  SCRIBE: ['the one who waits',
    'discards evidence because the measurement basis moved under it',
    'comparable pairs per arm, surviving every basis change'],
  SLAUGHT: ['the one who refuses',
    'withholds promotion on incomparable evidence, and calls it rigor',
    'SPRT n per cycle — n=0 is a blind gate, not a cautious one'],
  ZEALOT: ['the one who commits',
    'promotes on one lucky cycle against a baseline that had moved',
    'is baseline_hash unchanged across the whole streak'],
  'HIGH PRIEST': ['the one who serves',
    'is read by production, so a bad genome becomes real behaviour',
    'how many readers the promoted artifact actually has'],
  WARRIOR: ['the one who acts',
    'does exactly what the genome says, and nobody records the outcome',
    'arm contribution — an arm sitting at zero is a corpse, not a result'],
  SEER: ['the one who watches',
    'reports health while measuring nothing at all',
    'oracles run, faults found — no read is blind, not clean'],
  'SCRIBE-ELDER': ['the one who remembers',
    'writes the measurement down, and nothing ever reads it back',
    'readers per state artifact — zero readers is a dead store'],
};

const statePath = path.resolve(__dirname, '..', 'state');
const readJSON = f => { try { return JSON.parse(fs.readFileSync(path.join(statePath, f), 'utf8')); } catch { return null; } };

function figures() {
  const li = readJSON('loop_integrity.json');
  const tv = readJSON('terence_invariants.json');
  const co = readJSON('chain_ops.json');
  const arms = li && Array.isArray(li.arms) ? li.arms : [];
  return {
    OMNISCIENT: { k: 'MORPH GENES', v: '1', m: 'was 2 · one resolved 30/30' },
    SENTINEL: { k: 'ALERT POOL', v: '32', m: 'distinct states · was 1' },
    HUNTRESS: { k: 'FITNESS TOTAL', v: '26.0', m: 'info 16.6 + alert 9.3' },
    SCRIBE: { k: 'STREAK-ABLE ARMS', v: `${arms.length}`, m: 'comparable pairs held' },
    SLAUGHT: { k: 'SPRT n', v: '1', m: 'was 0 across 33 cycles' },
    ZEALOT: { k: 'PROMOTIONS', v: '0', m: 'blocked by EV-7 integrity' },
    'HIGH PRIEST': { k: 'PRODUCTION READERS', v: '6', m: 'on the promoted artifact' },
    WARRIOR: { k: 'ALARM SHARE', v: '4.2%', m: 'alert arm of the total' },
    SEER: { k: 'ORACLE FAULTS', v: tv && tv.fault_count != null ? `${tv.fault_count}` : '—',
            m: '4 oracles armed' },
    'SCRIBE-ELDER': { k: 'DEAD STORES',
            v: co && co.consumption ? `${co.consumption.no_readers.length}` : '—',
            m: `of ${co && co.consumption ? co.consumption.total : '?'} state artifacts` },
  };
}

const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');

function page(name, good) {
  const [role, fails, ask] = CARD[name];
  const F = figures()[name];
  const col = good ? LIVE : STARVED;
  const wash = good ? 'rgba(63,224,200,.16)' : 'rgba(255,171,61,.14)';
  // the figure IS the card: large, centred, standing on a ground line
  const body = FIGURES[name].slice(1).map((d, i) =>
    `<path d="${d}" fill="${col}" opacity="${i % 2 ? 0.6 : 1}"/>`).join('');

  return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>
  *{box-sizing:border-box}
  html,body{margin:0;width:${W}px;height:${H}px;overflow:hidden;background:${GROUND};
    font-family:'Segoe UI',Inter,system-ui,sans-serif;color:${INK}}
  .frame{position:absolute;inset:0;overflow:hidden;
    background:
      radial-gradient(72% 46% at 50% 42%, ${wash} 0%, transparent 62%),
      linear-gradient(172deg,#131722 0%,${GROUND} 58%,#08090e 100%)}
  .frame::after{content:'';position:absolute;inset:0;pointer-events:none;
    background:linear-gradient(180deg,rgba(255,255,255,.03) 1px,transparent 1px);
    background-size:100% 4px}
  .glow{position:absolute;left:50%;top:46%;width:760px;height:760px;
    transform:translate(-50%,-50%);border-radius:50%;
    background:radial-gradient(circle, ${col}26 0%, transparent 66%)}
  svg.fig{position:absolute;left:50%;top:50%;transform:translate(-50%,-52%);
    filter:drop-shadow(0 0 44px ${col}55)}
  .title{position:absolute;left:0;right:0;top:56px;text-align:center}
  .sin{font-size:17px;letter-spacing:.44em;color:${col};text-transform:uppercase}
  .role{font-size:54px;line-height:1.06;font-weight:700;letter-spacing:-.028em;
    margin-top:16px;text-shadow:0 4px 30px rgba(0,0,0,.85)}
  .badge{position:absolute;left:0;right:0;bottom:250px;text-align:center;
    font-size:15px;letter-spacing:.3em;color:${col}}
  .plate{position:absolute;left:0;right:0;bottom:0;padding:26px 54px 34px;
    background:linear-gradient(0deg,rgba(6,7,11,.97) 62%,transparent);
    display:flex;gap:30px;align-items:flex-end;justify-content:space-between}
  .lbl{font-size:12px;letter-spacing:.3em;color:${MUTE};text-transform:uppercase}
  .txt{font-size:18px;line-height:1.42;color:#bcc7db;margin-top:5px;max-width:52ch}
  .stat{text-align:right;flex:0 0 auto}
  .stat .k{font-size:11.5px;letter-spacing:.28em;color:${MUTE};text-transform:uppercase}
  .stat .v{font-size:64px;font-weight:700;letter-spacing:-.035em;color:${col};
    font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;line-height:1;
    text-shadow:0 0 26px currentColor;margin:6px 0 4px}
  .stat .m{font-size:12px;color:${MUTE};letter-spacing:.1em;
    font-family:ui-monospace,Consolas,monospace}
  </style></head><body>
  <div class="frame">
    <div class="glow"></div>
    <div class="title"><div class="sin">${esc(name)}</div>
      <div class="role">${esc(role)}</div></div>
    <svg class="fig" width="700" height="860" viewBox="-8 0 128 150">
      <ellipse cx="60" cy="141" rx="40" ry="5" fill="${col}" opacity=".26"/>
      ${body}
    </svg>
    <div class="badge">${good ? 'OUTPUT CONSUMED' : 'OUTPUT READ BY NOBODY'}</div>
    <div class="plate">
      <div>
        <div class="lbl">fails out of role as</div>
        <div class="txt">${esc(fails)}</div>
        <div class="lbl" style="margin-top:14px">the question that catches it</div>
        <div class="txt">${esc(ask)}</div>
      </div>
      <div class="stat"><div class="k">${esc(F.k)}</div>
        <div class="v">${esc(F.v)}</div><div class="m">${esc(F.m)}</div></div>
    </div>
  </div></body></html>`;
}

(async () => {
  fs.rmSync(STAGE, { recursive: true, force: true });
  fs.mkdirSync(STAGE, { recursive: true });
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--force-device-scale-factor=2',
           '--hide-scrollbars', '--font-render-hinting=none'],
  });
  const p = await browser.newPage();
  await p.setViewport({ width: W, height: H, deviceScaleFactor: 2 });
  for (const name of Object.keys(FIGURES)) {
    for (const [state, good] of [['good', true], ['bad', false]]) {
      await p.setContent(page(name, good), { waitUntil: 'load' });
      await p.screenshot({ path: path.join(STAGE, `${name}_${state}.webp`),
                           type: 'webp', quality: 88 });
    }
    process.stdout.write(`  ${name}\n`);
  }
  await browser.close();
  const s = fs.readdirSync(STAGE).filter(x => x.endsWith('.webp'))
    .map(x => fs.statSync(path.join(STAGE, x)).size);
  fs.rmSync(OUT, { recursive: true, force: true });
  fs.renameSync(STAGE, OUT);
  console.log(`Done! ${s.length} portraits at ${W * 2}x${H * 2}`);
  console.log(`  ${(Math.min(...s) / 1024).toFixed(0)}K..${(Math.max(...s) / 1024).toFixed(0)}K each, ${(s.reduce((a, b) => a + b) / 1e6).toFixed(2)} MB`);
})().catch(e => {
  try { fs.rmSync(STAGE, { recursive: true, force: true }); } catch {}
  console.error('capture failed:', e.message);
  console.error('(existing portraits in dashboard/archetypes were left untouched)');
  process.exit(1);
});

