"""Eve's inner states as WASM tutorials: Rust tier + Go tier.

Takes a measured inner state (ablation verdicts + NSP judgment) and emits
a Rust crate that compiles to WASM: the tutorial steps live in the module,and
render_frame(step) returns a professional SVG rendering of that state.
The HTML host loads the real .wasm build; nothing here is a mock.

Two tiers, same contract (step_count / steps_json / render_frame):

- Rust tier (generate/build_wasm/run_wasm): wasm-bindgen crate via
  wasm-pack (target web for the browser host page, nodejs for headless
  verification under node).
- Go tier (generate_go/build_wasm_go/run_wasm_go): stdlib-only Go
  (syscall/js) compiled with GOOS=js GOARCH=wasm, executed under node
  (WebAssembly is built into node, no browser needed).
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

from .model_registry import note_failure

LIB_RS = r'''use wasm_bindgen::prelude::*;

const STEPS: &str = %s;

#[wasm_bindgen]
pub fn step_count() -> usize {
    steps().len()
}

#[wasm_bindgen]
pub fn steps_json() -> String {
    STEPS.to_string()
}

fn steps() -> Vec<serde_json::Value> {
    serde_json::from_str(STEPS).unwrap_or_default()
}

/// Professional rendering of one tutorial step as SVG: the provider box
/// is colored by verdict, the fallback row points at the NSP badge.
#[wasm_bindgen]
pub fn render_frame(i: usize) -> String {
    let steps = steps();
    if steps.is_empty() {
        return "<svg/>".to_string();
    }
    let s = &steps[i %% steps.len()];
    let title = s.get("title").and_then(|v| v.as_str()).unwrap_or("?");
    let verdict = s.get("verdict").and_then(|v| v.as_str()).unwrap_or("");
    let detail = s.get("detail").and_then(|v| v.as_str()).unwrap_or("");
    let color = match verdict {
        "ANSWERED" => "#1d7a3a",
        "REFUSED" => "#b35400",
        "ERROR" => "#a31f1f",
        _ => "#1f5fa3",
    };
    format!(
        "<svg xmlns='http://www.w3.org/2000/svg' width='640' height='240'>\
         <rect width='640' height='240' rx='12' fill='#0d1117'/>\
         <text x='24' y='40' font-size='20' fill='#e6edf3' \
         font-family='sans-serif'>{}</text>\
         <rect x='24' y='64' width='592' height='96' rx='8' fill='{}'/>\
         <text x='44' y='104' font-size='22' fill='#ffffff' \
         font-family='sans-serif'>{}</text>\
         <text x='44' y='134' font-size='14' fill='#f0f0f0' \
         font-family='monospace'>{}</text>\
         <text x='24' y='200' font-size='14' fill='#8b949e' \
         font-family='sans-serif'>Eve inner state {} / {} - compiled to WASM, \
         rendered natively</text></svg>",
        esc(title), color, esc(verdict), esc(detail), i + 1, steps.len()
    )
}

fn esc(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}
'''

CARGO_TOML = '''[package]
name = "%s"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["cdylib"]

[dependencies]
wasm-bindgen = "0.2"
serde_json = "1.0"
'''

INDEX_HTML = '''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>%s</title>
<style>body{background:#0d1117;color:#e6edf3;font-family:sans-serif;
max-width:700px;margin:2em auto}button{margin:.2em;padding:.5em 1em}
#stage{margin-top:1em}</style></head>
<body><h1>%s</h1><p>Eve's inner states, compiled to Rust WASM.</p>
<div id="nav"></div><div id="stage"></div>
<div id="decide"><h3>Decision blocks (drive dev via Eve serve)</h3>
<button onclick="decide('apply-best')">Apply best evolution</button>
<button onclick="decide('revert')">Revert game</button>
<span id="dout"></span></div>
<div id="therapy" style="margin-top:1.5em;padding:1em;border:2px solid #1f5fa3;border-radius:12px">
<h3>Live therapy + voice assistance</h3>
<button id="therapyBtn" style="font-size:1.3em;padding:.6em 1.4em;background:#1f5fa3;color:#fff;border:none;border-radius:10px;cursor:pointer" onclick="liveTherapy()">THERAPY</button>
<button onclick="voiceCommand()">Voice command (mic)</button>
<button onclick="stopSpeech()">Stop voice</button><br/>
<label>Mood: <select id="mood"><option>stressed</option><option>anxious</option><option>overwhelmed</option><option>sad</option><option>angry</option><option>okay</option></select></label>
<input id="tnote" placeholder="what's on your mind?" size="40"/>
<div id="tout" style="white-space:pre-wrap;margin-top:.6em;min-height:3em"></div>
<div id="vout" style="color:#8b949e;margin-top:.4em"></div>
</div>
<script>
const SERVE = 'http://127.0.0.1:8091';
function speak(t){
  try{
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(t.slice(0, 600));
    u.rate = 0.95;
    speechSynthesis.speak(u);
  }catch(e){}
}
function stopSpeech(){ try{ speechSynthesis.cancel(); }catch(e){} }
async function decide(d){
  const game = prompt('Game directory?', '');
  if(!game) return;
  const r = await fetch(SERVE + '/decide',
    {method:'POST', headers:{'Content-Type':'application/json'},
     body: JSON.stringify({decision:d, game})});
  const txt = (await r.text()).slice(0, 200);
  document.getElementById('dout').textContent = d + ': ' + txt;
  speak(d === 'revert' ? 'Game reverted.' : 'Best evolution applied.');
}
async function liveTherapy(){
  const mood = document.getElementById('mood').value;
  const note = document.getElementById('tnote').value;
  const out = document.getElementById('tout');
  out.textContent = 'Eve is here with you...';
  try{
    const r = await fetch(SERVE + '/therapy',
      {method:'POST', headers:{'Content-Type':'application/json'},
       body: JSON.stringify({mood, note})});
    const j = await r.json();
    out.textContent = j.text;
    speak(j.text);
  }catch(e){ out.textContent = 'Serve offline. Breathe in 4, hold 4, out 6. I am here.'; speak(out.textContent); }
}
function voiceCommand(){
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const out = document.getElementById('vout');
  if(!SR){ out.textContent = 'Voice recognition not supported in this browser.'; return; }
  const rec = new SR();
  rec.lang = 'en-US'; rec.interimResults = false;
  out.textContent = 'Listening... say therapy, breathe, apply best, revert, or next.';
  rec.onresult = (ev) => {
    const heard = ev.results[0][0].transcript;
    out.textContent = 'Heard: ' + heard;
    routeVoice(heard);
  };
  rec.onerror = (ev) => { out.textContent = 'Mic error: ' + ev.error; };
  rec.start();
}
async function routeVoice(heard){
  const t = heard.toLowerCase();
  if(/therap|calm|anxious|stressed|ground/.test(t)){ liveTherapy(); return; }
  if(/breath/.test(t)){ const b = 'Breathe in 4 counts, hold 4, out 6. Again. You are doing well.';
    document.getElementById('tout').textContent = b; speak(b); return; }
  if(/apply.*best/.test(t)){ decide('apply-best'); return; }
  if(/revert|undo/.test(t)){ decide('revert'); return; }
  if(/next|continue|forward/.test(t)){ if(typeof show === 'function'){ show(cur + 1); speak('Next step.'); } return; }
  speak('I heard ' + heard + '. Say therapy, breathe, apply best, revert, or next.');
}
</script>
<script type="module">
import init, { step_count, steps_json, render_frame } from './pkg/%s.js';
await init();
const steps = JSON.parse(steps_json());
const nav = document.getElementById('nav');
const stage = document.getElementById('stage');
let cur = 0;
function show(i){ cur = (i + step_count()) %% step_count();
  stage.innerHTML = render_frame(cur);
  document.title = steps[cur].title; }
steps.forEach((s, i) => { const b = document.createElement('button');
  b.textContent = (i+1); b.onclick = () => show(i); nav.appendChild(b); });
show(0);
</script></body></html>
'''


def state_to_steps(state):
    """Inner state dict -> ordered tutorial steps."""
    steps = [{"title": "Swarm overview: %d providers, %d answered"
                       % (state.get("total", 0), state.get("answered", 0)),
              "verdict": "OVERVIEW",
              "detail": state.get("summary", "")}]
    for r in state.get("rows", []):
        steps.append({"title": "Provider %s" % r.get("provider"),
                      "verdict": r.get("verdict", ""),
                      "detail": r.get("detail", "")})
    steps.append({"title": "Fallback serves with NSP judgment",
                  "verdict": "JUDGED",
                  "detail": state.get("nsp", "nsp verdict attached "
                                             "to every served answer")})
    return steps


def parse_ablation_log(path):
    """Parse a real `eve ablate` log into an inner-state dict."""
    rows, summary = [], ""
    for line in Path(path).read_text(encoding="utf-8",
                                     errors="replace").splitlines():
        line = line.strip()
        if line.startswith("[") and "]" in line:
            verdict, rest = line[1:].split("]", 1)
            provider, _, detail = rest.strip().partition(":")
            rows.append({"provider": provider.strip(),
                         "verdict": verdict.strip(),
                         "detail": detail.strip()[:100]})
        elif line.startswith("ABLATION:"):
            summary = line
    answered = sum(1 for r in rows
                   if r["provider"] != "swarm+NSP fallback"
                   and r["verdict"] == "ANSWERED")
    providers = [r for r in rows if r["provider"] != "swarm+NSP fallback"]
    return {"rows": rows, "answered": answered,
            "total": len(providers), "summary": summary}


def generate(state, out_dir, crate_name="eve_inner_state"):
    """Write crate sources + host page. Returns (out_dir, step_total)."""
    out = Path(out_dir)
    (out / "src").mkdir(parents=True, exist_ok=True)
    steps = state_to_steps(state)
    (out / "src" / "lib.rs").write_text(
        LIB_RS % json.dumps(json.dumps(steps)), encoding="utf-8")
    (out / "Cargo.toml").write_text(CARGO_TOML % crate_name,
                                    encoding="utf-8")
    title = "Eve inner states: swarm ablation tutorial"
    (out / "index.html").write_text(INDEX_HTML % (title, title, crate_name),
                                    encoding="utf-8")
    (out / "state.json").write_text(json.dumps(state, indent=2),
                                    encoding="utf-8")
    return out, len(steps)


def have_toolchain(kind):
    """Tool availability for a wasm tier: {tool: path or None}.

    rust: wasm-pack (cargo is pulled by the pack, not probed separately).
    go/node: the real binaries; the Go tier needs both (compile + run).
    """
    if kind == "rust":
        return {"wasm-pack": shutil.which("wasm-pack")}
    if kind == "go":
        return {"go": shutil.which("go")}
    if kind == "node":
        return {"node": shutil.which("node")}
    raise ValueError("wasm tiers are rust, go, node: %s" % kind)


GO_WASM_SRC = '''package main

import (
    "encoding/json"
    "fmt"
    "strings"
    "syscall/js"
)

const stepsJSON = `%s`

type step struct {
    Title   string `json:"title"`
    Verdict string `json:"verdict"`
    Detail  string `json:"detail"`
}

func steps() []step {
    var s []step
    if err := json.Unmarshal([]byte(stepsJSON), &s); err != nil {
        return nil
    }
    return s
}

func colorFor(v string) string {
    switch v {
    case "ANSWERED":
        return "#1d7a3a"
    case "REFUSED":
        return "#b35400"
    case "ERROR":
        return "#a31f1f"
    default:
        return "#1f5fa3"
    }
}

func esc(s string) string {
    r := strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;")
    return r.Replace(s)
}

func renderFrame(i int) string {
    s := steps()
    if len(s) == 0 {
        return "<svg/>"
    }
    st := s[i%%len(s)]
    return fmt.Sprintf("<svg xmlns='http://www.w3.org/2000/svg' width='640' height='240'>"+
        "<rect width='640' height='240' rx='12' fill='#0d1117'/>"+
        "<text x='24' y='40' font-size='20' fill='#e6edf3' "+
        "font-family='sans-serif'>%%s</text>"+
        "<rect x='24' y='64' width='592' height='96' rx='8' fill='%%s'/>"+
        "<text x='44' y='104' font-size='22' fill='#ffffff' "+
        "font-family='sans-serif'>%%s</text>"+
        "<text x='44' y='134' font-size='14' fill='#f0f0f0' "+
        "font-family='monospace'>%%s</text>"+
        "<text x='24' y='200' font-size='14' fill='#8b949e' "+
        "font-family='sans-serif'>Eve inner state %%d / %%d - compiled to WASM, "+
        "rendered natively</text></svg>",
        esc(st.Title), colorFor(st.Verdict), esc(st.Verdict),
        esc(st.Detail), i+1, len(s))
}

func main() {
    js.Global().Set("stepCount", js.FuncOf(func(this js.Value,
        args []js.Value) any { return len(steps()) }))
    js.Global().Set("stepsJSON", js.FuncOf(func(this js.Value,
        args []js.Value) any { return stepsJSON }))
    js.Global().Set("renderFrame", js.FuncOf(func(this js.Value,
        args []js.Value) any {
        i := 0
        if len(args) > 0 {
            i = args[0].Int()
        }
        return renderFrame(i)
    }))
    // Headless (node) path: the harness sets __eveCollect before
    // instantiation. Push the verification payload out and RETURN so
    // the Go runtime exits cleanly -- calling process.exit while the
    // runtime holds libuv handles crashes node on Windows. Browser
    // path (no collector): stay resident and serve the exports.
    if collect := js.Global().Get("__eveCollect");
        collect.Type() == js.TypeFunction {
        s := steps()
        frame := renderFrame(0)
        payload := map[string]any{
            "step_count": len(s),
            "steps_chars": len(stepsJSON),
            "frame_chars":  len(frame),
            "frame_head":   frame[:min(60, len(frame))],
        }
        b, _ := json.Marshal(payload)
        collect.Invoke(string(b))
        return
    }
    js.Global().Set("__eveReady", true)
    select {}
}

func min(a, b int) int {
    if a < b {
        return a
    }
    return b
}
'''

GO_MOD = '''module eveinnerstate

go 1.23
'''

#: Node harness for the Go module. Sets the __eveCollect callback so Go
#: main() pushes the payload out and returns; the loop then drains
#: cleanly (no process.exit under a blocked runtime -- that crashes node
#: on Windows). The payload is computed inside the WASM module.
NODE_HARNESS = '''const fs = require('fs');
const path = require('path');
const dir = __dirname;
globalThis.__eveResult = null;
globalThis.__eveCollect = (json) => { globalThis.__eveResult = json; };
require(path.join(dir, 'wasm_exec.js'));
const go = new Go();
WebAssembly.instantiate(fs.readFileSync(path.join(dir, 'main.wasm')),
    go.importObject).then((result) => {
  return go.run(result.instance);
}).then(() => {
  if (!globalThis.__eveResult) {
    console.error('WASM-NO-RESULT: module returned without payload');
    process.exit(1);
  }
  console.log(globalThis.__eveResult);
}).catch((e) => {
  console.error('WASM-FAIL: ' + (e && e.message));
  process.exit(1);
});
'''

#: Node harness for a --target nodejs pkg. That target emits CommonJS
#: (exports.*, synchronous wasm load at require time -- no init() call,
#: unlike the web target). Written into the pkg dir by run_wasm as
#: run_node.cjs with the real stem filled in (one .js per pkg).
NODE_RUST_HARNESS = '''const w = require('./%s.js');
const frame = w.render_frame(0);
console.log(JSON.stringify({
  step_count: w.step_count(),
  steps_chars: String(w.steps_json()).length,
  frame_chars: String(frame).length,
  frame_head: String(frame).slice(0, 60),
}));
'''


def _go_root():
    """GOROOT for wasm_exec.js, via the go binary (never a guess)."""
    go = shutil.which("go")
    if not go:
        raise RuntimeError("go not on PATH")
    proc = subprocess.run([go, "env", "GOROOT"], capture_output=True,
                          text=True, timeout=60)
    root = (proc.stdout or "").strip()
    if proc.returncode != 0 or not root:
        raise RuntimeError("go env GOROOT failed")
    return Path(root)


def generate_go(state, out_dir):
    """Write Go WASM sources + node harness. Returns (out_dir, steps)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    steps = state_to_steps(state)
    # Raw-string literal: backticks cannot appear inside one.
    blob = json.dumps(steps).replace("`", "'")
    (out / "main.go").write_text(GO_WASM_SRC % blob, encoding="utf-8")
    (out / "go.mod").write_text(GO_MOD, encoding="utf-8")
    (out / "run_node.js").write_text(NODE_HARNESS, encoding="utf-8")
    (out / "state.json").write_text(json.dumps(state, indent=2),
                                    encoding="utf-8")
    return out, len(steps)


def build_wasm_go(src_dir, timeout=600):
    """Compile main.go to main.wasm (GOOS=js GOARCH=wasm) + stage harness.

    Returns the dir holding main.wasm, wasm_exec.js and run_node.js.
    Raises RuntimeError with the compiler's own diagnostics on failure.
    """
    src = Path(src_dir)
    go = shutil.which("go")
    if not go:
        raise RuntimeError("go not on PATH")
    env = dict(os.environ, GOOS="js", GOARCH="wasm")
    try:
        proc = subprocess.run(
            [go, "build", "-o", "main.wasm", "."],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(src), env=env)
    except Exception as e:
        note_failure("wasm-go:build-spawn", e)
        raise RuntimeError("go build would not start: %s" % e)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout)[-2000:]
        note_failure("wasm-go:build-fail", Exception(err[:200]))
        raise RuntimeError("go build failed: %s" % err)
    try:
        root = _go_root()
        staged = None
        for cand in (root / "misc" / "wasm" / "wasm_exec.js",
                     root / "lib" / "wasm" / "wasm_exec.js"):
            if cand.is_file():
                staged = cand
                break
        if staged is None:
            raise RuntimeError("wasm_exec.js not found under %s" % root)
        (src / "wasm_exec.js").write_bytes(staged.read_bytes())
    except RuntimeError:
        raise
    except Exception as e:
        note_failure("wasm-go:stage", e)
        raise RuntimeError("could not stage wasm_exec.js: %s" % e)
    return src


def run_wasm_go(src_dir, timeout=120):
    """Execute main.wasm under node. Returns {ok, step_count, ...}.

    The module runs for real: step_count/frame come out of the compiled
    WASM through the Go bridge, not out of this file.
    """
    src = Path(src_dir)
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node not on PATH")
    for need in ("main.wasm", "wasm_exec.js", "run_node.js"):
        if not (src / need).is_file():
            raise RuntimeError("missing %s (build first)" % need)
    try:
        proc = subprocess.run(
            [node, "run_node.js"], capture_output=True, text=True,
            timeout=timeout, cwd=str(src))
    except Exception as e:
        note_failure("wasm-go:run-spawn", e)
        raise RuntimeError("node would not start: %s" % e)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout)[-2000:]
        note_failure("wasm-go:run-fail", Exception(err[:200]))
        raise RuntimeError("wasm run failed: %s" % err)
    try:
        rep = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except Exception as e:
        note_failure("wasm-go:run-parse", e)
        raise RuntimeError("wasm run printed no JSON: %s"
                           % (proc.stdout or "")[:300])
    rep["ok"] = True
    return rep


def run_wasm(pkg_dir, timeout=120):
    """Execute a --target nodejs pkg under node. Returns {ok, ...}.

    The numbers come out of the compiled Rust WASM, not out of this
    file. Raises RuntimeError naming the failure (missing module, node
    error, unparseable output).
    """
    pkg = Path(pkg_dir)
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node not on PATH")
    mods = sorted(p for p in pkg.glob("*.js")
                  if not p.name.endswith(".d.ts")
                  and p.name not in ("run_node.mjs", "run_node.cjs"))
    if len(mods) != 1:
        raise RuntimeError("expected one module in %s, found %d"
                           % (pkg, len(mods)))
    stem = mods[0].stem
    for need in (stem + ".js", stem + "_bg.wasm"):
        if not (pkg / need).is_file():
            raise RuntimeError("missing %s (build --target nodejs first)"
                               % need)
    (pkg / "run_node.cjs").write_text(NODE_RUST_HARNESS % stem,
                                       encoding="utf-8")
    try:
        proc = subprocess.run(
            [node, "run_node.cjs"], capture_output=True, text=True,
            timeout=timeout, cwd=str(pkg))
    except Exception as e:
        note_failure("wasm:run-spawn", e)
        raise RuntimeError("node would not start: %s" % e)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout)[-2000:]
        note_failure("wasm:run-fail", Exception(err[:200]))
        raise RuntimeError("wasm run failed: %s" % err)
    try:
        rep = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except Exception as e:
        note_failure("wasm:run-parse", e)
        raise RuntimeError("wasm run printed no JSON: %s"
                           % (proc.stdout or "")[:300])
    rep["ok"] = True
    return rep


def build_wasm(crate_dir, timeout=600, target="web"):
    """Compile the crate to WASM with wasm-pack. Returns pkg dir or raises.

    target web (default) feeds the HTML host page; nodejs loads the
    module under node via require (no fetch), which is how headless
    verification runs.
    """
    if target not in ("web", "nodejs"):
        raise ValueError("wasm target is web or nodejs: %s" % target)
    try:
        proc = subprocess.run(
            ["wasm-pack", "build", "--target", target, str(crate_dir)],
            capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        note_failure("wasm:build-spawn", e)
        raise RuntimeError("wasm-pack would not start: %s" % e)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout)[-2000:]
        note_failure("wasm:build-fail",
                     Exception(err[:200]))
        raise RuntimeError("wasm-pack failed: %s" % err)
    return Path(crate_dir) / "pkg"
