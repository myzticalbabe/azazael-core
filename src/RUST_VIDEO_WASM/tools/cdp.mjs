// Shared CDP plumbing for the bake and render tools. No dependencies: Node 24
// ships a global WebSocket, and the DevTools protocol is just JSON over it.
//
// Playbook rules encoded here (C:\Users\clint\YGGDRASIL_RENDER_PLAYBOOK_2026-10-30.md):
//   - one browser instance per run, its own --user-data-dir, because the profile
//     directory is a hard lock and a shared one yields exit 0 with no file;
//   - absolute output paths, because Chrome resolves a relative path against its
//     own CWD and writes the frames somewhere else.
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

export const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function startServer(root, port) {
  const proc = spawn("python", ["-m", "http.server", String(port), "--bind", "127.0.0.1"],
    { cwd: root, stdio: "ignore" });
  for (let i = 0; i < 80; i++) {
    try {
      if ((await fetch(`http://127.0.0.1:${port}/demo/index.html`, { method: "HEAD" })).ok) return proc;
    } catch {}
    await sleep(250);
  }
  proc.kill();
  throw new Error(`static server never answered on 127.0.0.1:${port}`);
}

export async function launch({ width = 960, height = 540, port = 9333, label = "vw" } = {}) {
  const profile = mkdtempSync(join(tmpdir(), `${label}-`));
  const proc = spawn(CHROME, [
    "--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--hide-scrollbars",
    "--autoplay-policy=no-user-gesture-required", "about:blank",
  ], { stdio: "ignore" });

  let wsUrl;
  for (let i = 0; i < 80 && !wsUrl; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      wsUrl = list.find((t) => t.type === "page")?.webSocketDebuggerUrl;
    } catch {}
    if (!wsUrl) await sleep(250);
  }
  if (!wsUrl) { proc.kill(); throw new Error("chrome never exposed a debugging target"); }

  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error("ws failed")); });

  let id = 0;
  const pending = new Map();
  const logs = [];
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.rej(new Error(m.error.message)) : p.res(m.result);
    } else if (m.method === "Runtime.exceptionThrown") {
      logs.push(`[exception] ${m.params.exceptionDetails.exception?.description?.split("\n")[0]}`);
    } else if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
      logs.push(`[console.error] ${m.params.args.map((a) => a.value ?? a.description).join(" ")}`);
    }
  };
  const send = (method, params = {}) => {
    const msg = { id: ++id, method, params };
    ws.send(JSON.stringify(msg));
    return new Promise((res, rej) => pending.set(msg.id, { res, rej }));
  };

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride",
    { width, height, deviceScaleFactor: 1, mobile: false });

  return {
    send, logs,
    async evaluate(expression) {
      const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) {
        throw new Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text);
      }
      return r.result?.value;
    },
    async goto(url) {
      await send("Page.navigate", { url });
      for (let i = 0; i < 200; i++) {
        if (await this.evaluate("document.readyState === 'complete'")) return;
        await sleep(50);
      }
      throw new Error(`page never reached readyState complete: ${url}`);
    },
    async waitFor(expr, timeoutMs = 120000) {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        try { if (await this.evaluate(expr)) return; } catch {}
        await sleep(100);
      }
      throw new Error(`timed out waiting for ${expr}`);
    },
    async screenshot(path) {
      const { data } = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
      writeFileSync(resolve(path), Buffer.from(data, "base64"));
    },
    close() { try { ws.close(); } catch {} proc.kill(); },
  };
}
