// Drive the demo in headless Chrome over CDP with no dependencies.
// Usage: node tools/_demo_check.mjs [port]
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const PORT = 9333;
const URL = "http://127.0.0.1:8123/demo/index.html";
const profile = mkdtempSync(join(tmpdir(), "vw-chrome-"));

const chrome = spawn(
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  [
    "--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
    "--no-first-run", "--no-default-browser-check", "--disable-gpu",
    "--autoplay-policy=no-user-gesture-required", "--window-size=1100,900", "about:blank",
  ],
  { stdio: "ignore" },
);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let ws, id = 0;
const pending = new Map();
const logs = [];

async function connect() {
  for (let i = 0; i < 40; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = list.find((t) => t.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch {}
    await sleep(300);
  }
  throw new Error("chrome did not expose a page target");
}

function send(method, params = {}) {
  const msg = { id: ++id, method, params };
  ws.send(JSON.stringify(msg));
  return new Promise((res, rej) => pending.set(msg.id, { res, rej }));
}

const evaluate = async (expression) => {
  const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + " " + (r.exceptionDetails.exception?.description ?? ""));
  return r.result.value;
};

try {
  const wsUrl = await connect();
  ws = new WebSocket(wsUrl);
  await new Promise((r) => (ws.onopen = r));
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? rej(new Error(m.error.message)) : res(m.result);
    } else if (m.method === "Runtime.consoleAPICalled") {
      logs.push(`[${m.params.type}] ` + m.params.args.map((a) => a.value ?? a.description ?? a.type).join(" "));
    } else if (m.method === "Runtime.exceptionThrown") {
      const d = m.params.exceptionDetails;
      const frame = d.stackTrace?.callFrames?.[0];
      logs.push(`[exception] ${d.exception?.description ?? d.text}` +
        (frame ? `  @ ${frame.functionName || "(top)"}:${frame.lineNumber + 1}` : ""));
    }
  };

  await send("Runtime.enable");
  await send("Page.enable");
  await send("Page.navigate", { url: URL });
  await sleep(3000);

  const ready = await evaluate(`(() => {
    const w = window;
    return { module: typeof w.__vw === "object" || true, title: document.title,
             wasmLoaded: !!document.querySelector("nav button") };
  })()`);
  console.log("page:", JSON.stringify(ready));
  console.log("footer:", await evaluate('document.getElementById("foot").textContent'));

  const shot = async (name) => {
    const { data } = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(name, Buffer.from(data, "base64"));
    console.log("shot:", name);
  };

  // ---- A: play, then read the aurora canvas back
  await evaluate(`document.getElementById("playA").click()`);
  await sleep(2500);
  const a = await evaluate(`(() => {
    const c = document.getElementById("aurora");
    const d = c.getContext("2d").getImageData(0,0,c.width,c.height).data;
    let lit = 0; for (let i=3;i<d.length;i+=4) if (d[i] > 8) lit++;
    const v = document.getElementById("vid");
    return { lit, t: v.currentTime, paused: v.paused, out: document.getElementById("outA").textContent };
  })()`);
  console.log("A:", JSON.stringify(a));
  await shot("/tmp/vw_a.png");

  // ---- B
  await evaluate(`document.querySelector('[data-tab="b"]').click()`);
  await evaluate(`document.getElementById("playB").click()`);
  await sleep(1200);
  const b = await evaluate(`(() => {
    const c = document.getElementById("canvasB");
    const d = c.getContext("2d").getImageData(0,0,c.width,c.height).data;
    let nonBlack = 0; for (let i=0;i<d.length;i+=4) if (d[i]+d[i+1]+d[i+2] > 12) nonBlack++;
    return { nonBlack, out: document.getElementById("outB").textContent };
  })()`);
  console.log("B:", JSON.stringify(b));
  await shot("/tmp/vw_b.png");

  // ---- C: force the WebCodecs transport and assert the readout names it. The
  // panel falling back to canvas would still paint, so only the readout proves
  // the I420 path ran.
  await evaluate(`document.querySelector('[data-tab="c"]').click()`);
  await evaluate(`(() => {
    const s = document.getElementById("pathC");
    s.value = "webcodecs"; s.dispatchEvent(new Event("change"));
  })()`);
  await evaluate(`document.getElementById("playC").click()`);
  await sleep(1800);
  const c = await evaluate(`(() => {
    const c = document.getElementById("canvasC");
    const d = c.getContext("2d").getImageData(0,0,c.width,c.height).data;
    let nonBlack = 0; for (let i=0;i<d.length;i+=4) if (d[i]+d[i+1]+d[i+2] > 12) nonBlack++;
    return { nonBlack, out: document.getElementById("outC").textContent };
  })()`);
  console.log("C:", JSON.stringify(c));
  await shot("/tmp/vw_c.png");
  const cWebCodecs = /webcodecs i420/.test(c.out);

  // ---- D, source 1: the pre-packed AVRD blob (no decoder involved)
  await evaluate(`document.querySelector('[data-tab="d"]').click()`);
  await evaluate(`document.getElementById("playD").click()`);
  await sleep(1200);
  const dBlob = await evaluate(`(() => {
    const c = document.getElementById("canvasD");
    const g = c.getContext("2d").getImageData(0,0,c.width,c.height).data;
    let nonBlack = 0; for (let i=0;i<g.length;i+=4) if (g[i]+g[i+1]+g[i+2] > 12) nonBlack++;
    return { nonBlack, out: document.getElementById("outD").textContent };
  })()`);
  console.log("D blob:", JSON.stringify(dBlob));

  // ---- D, source 2: ffmpeg.wasm decodes the mp4 in the page. This is the slow
  // one (30.9 MB core, then 6 s of AV1), so poll the readout instead of sleeping
  // a guessed duration, and fail on the panel's own error text.
  await evaluate(`document.getElementById("decodeD").click()`);
  let dOut = "";
  for (let i = 0; i < 180; i++) {
    await sleep(500);
    dOut = await evaluate('document.getElementById("outD").textContent');
    if (/^wasm decode:/.test(dOut) || /failed|not vendored/i.test(dOut)) break;
  }
  await sleep(600);
  const d = await evaluate(`(() => {
    const c = document.getElementById("canvasD");
    const g = c.getContext("2d").getImageData(0,0,c.width,c.height).data;
    let nonBlack = 0; for (let i=0;i<g.length;i+=4) if (g[i]+g[i+1]+g[i+2] > 12) nonBlack++;
    return { nonBlack, out: document.getElementById("outD").textContent };
  })()`);
  const decodedFrames = Number((/wasm decode: (\d+) frames/.exec(d.out) || [])[1] || 0);
  console.log("D ffmpeg:", JSON.stringify({ decodedFrames, nonBlack: d.nonBlack, out: d.out }));
  await shot("/tmp/vw_d.png");

  console.log("--- console ---");
  console.log(logs.length ? logs.join("\n") : "(clean: no console output, no exceptions)");

  const checks = [
    ["A aurora lit", a.lit > 200, `${a.lit} px`],
    ["B blob paints", b.nonBlack > 500, `${b.nonBlack} px`],
    ["C paints", c.nonBlack > 500, `${c.nonBlack} px`],
    ["C ran the WebCodecs I420 transport", cWebCodecs, c.out.slice(0, 60)],
    ["D blob paints", dBlob.nonBlack > 500, `${dBlob.nonBlack} px`],
    ["D ffmpeg.wasm decoded in-page", decodedFrames >= 100, `${decodedFrames} frames`],
    ["D ffmpeg.wasm output paints", d.nonBlack > 500, `${d.nonBlack} px`],
    ["no exceptions", !logs.some((l) => l.includes("exception")), `${logs.length} console lines`],
  ];
  for (const [name, ok, detail] of checks) console.log(`${ok ? "PASS" : "FAIL"}  ${name}  ${detail}`);
  const allOk = checks.every(([, ok]) => ok);
  console.log(allOk ? "DEMO OK: four panels, both decode paths" : "DEMO FAIL: see the FAIL lines");
  process.exitCode = allOk ? 0 : 1;
} catch (err) {
  console.error("DRIVER ERROR:", err.message);
  process.exitCode = 2;
} finally {
  try { ws?.close(); } catch {}
  chrome.kill();
}
