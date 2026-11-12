"""live_morph.py -- speak an instruction, watch the document change.

A morph you run blind is a batch job; a morph you watch is an
instrument. This module serves a live window (stdlib HTTP only):
the document on the left, an instruction box with a voice button
in the middle, and the instruction log on the right. Every spoken
or typed instruction parses to one operation, applies to the live
text, and lands in the log with its before/after line count -- so
people see their instructions create, live.

Voice is the browser's own Web Speech API (the same hook the WASM
tutorials already use); nothing is recorded or uploaded, the
recognition result lands in the same box typing would. The server
never leaves the machine: it binds loopback, the document never
crosses the network, and undo is a stack, not a promise.

Instructions understood (case-insensitive):
- replace <a> with <b> | change <a> to <b>
- uppercase | lowercase [in <heading>]
- make steps | tutorial  (doc_tutor conversion, shown as steps)
- outline               (headings listed into the log)
- mutate                (one seeded mutation, logged as such)
- undo | revert          (pop the stack)
Anything else is logged as UNUNDERSTOOD, never applied.
"""
import html as _html
import json as _json
import re as _re
import threading as _threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass

_REPLACE_RE = _re.compile(r"^\s*(?:replace|change)\s+(.+?)\s+(?:with|to)\s+(.+?)\s*$",
                          _re.I)
_CASE_RE = _re.compile(r"^\s*(upper|lower)\s*case\s*(?:in\s+(.+?))?\s*$", _re.I)
_UNDO_RE = _re.compile(r"^\s*(undo|revert|go back)\s*$", _re.I)
_STEPS_RE = _re.compile(r"^\s*(make steps|tutorial|steps)\s*$", _re.I)
_OUTLINE_RE = _re.compile(r"^\s*(outline|headings|show outline)\s*$", _re.I)
_MUTATE_RE = _re.compile(r"^\s*(mutate|remix|vary)\s*$", _re.I)


def parse_instruction(text):
    """Instruction words -> operation dict. Raises ValueError."""
    if not text or not text.strip():
        raise ValueError("empty instruction: say or type what to change")
    s = text.strip()
    m = _REPLACE_RE.match(s)
    if m:
        old, new = m.group(1).strip(), m.group(2).strip()
        if not old or not new:
            raise ValueError("replace needs both sides: replace X with Y")
        return {"op": "replace", "old": old, "new": new, "raw": s}
    m = _CASE_RE.match(s)
    if m:
        return {"op": "case",
                "upper": m.group(1).lower() == "upper",
                "section": (m.group(2) or "").strip() or None, "raw": s}
    if _UNDO_RE.match(s):
        return {"op": "undo", "raw": s}
    if _STEPS_RE.match(s):
        return {"op": "steps", "raw": s}
    if _OUTLINE_RE.match(s):
        return {"op": "outline", "raw": s}
    if _MUTATE_RE.match(s):
        return {"op": "mutate", "raw": s}
    raise ValueError("not understood: %s -- try 'replace X with Y', "
                     "'uppercase', 'make steps', 'outline', 'mutate', "
                     "'undo'" % s[:80])


def _section_span(lines, heading):
    """Line span of a markdown/upper heading, or the whole document."""
    if not heading:
        return 0, len(lines)
    want = heading.strip().lower()
    start, level = None, None
    for i, line in enumerate(lines):
        s = line.strip()
        head = (s.lstrip("# ").strip().lower() if s.startswith("#")
                else s.lower() if s.isupper() and len(s) < 70 else None)
        if head is None:
            continue
        if start is None and head == want:
            start = i
            level = len(s) - len(s.lstrip("#")) if s.startswith("#") else 2
            continue
        if start is not None:
            this = len(s) - len(s.lstrip("#")) if s.startswith("#") else 2
            if this <= level:
                return start, i
    return (start or 0), len(lines)


def apply_op(text, op):
    """Apply one parsed operation. Returns (new_text, report)."""
    lines = text.splitlines()
    kind = op["op"]
    if kind == "replace":
        new = text.replace(op["old"], op["new"])
        n = text.count(op["old"])
        return new, "replaced %d occurrence(s) of %r" % (n, op["old"][:40])
    if kind == "case":
        lo, hi = _section_span(lines, op.get("section"))
        for i in range(lo, hi):
            lines[i] = lines[i].upper() if op["upper"] else lines[i].lower()
        where = "section %r" % op["section"] if op.get("section") else "all"
        return "\n".join(lines), "cased %d line(s) %s" % (hi - lo, where)
    if kind == "steps":
        from .doc_tutor import tutorial as _tut
        tut = _tut(text)
        out = ["# Steps (from your words)", ""]
        for n, st in enumerate(tut["steps"][:20], 1):
            out.append("%d. %s" % (n, st["action"]))
        return "\n".join(out), "%d step(s) extracted" % min(
            len(tut["steps"]), 20)
    if kind == "outline":
        from .doc_tutor import outline as _out
        heads = _out(text)
        return text, "outline: %s" % (" | ".join(
            h["title"] for h in heads[:10]) or "(no headings)")
    if kind == "mutate":
        import random as _random
        rng = _random.Random(len(text))
        idx = [i for i, ln in enumerate(lines) if len(ln.split()) >= 4]
        if not idx:
            return text, "mutate: no long line to vary"
        i = rng.choice(idx)
        words = lines[i].split()
        j = rng.randrange(len(words))
        words[j] = words[j][::-1]
        lines[i] = " ".join(words)
        return "\n".join(lines), "mutated line %d (one word reversed)" % (i + 1)
    raise ValueError("cannot apply: %s" % kind)


class LiveState:
    """The document, its history stack, and the instruction log."""

    def __init__(self, text, name="document"):
        self.name = name
        self.text = text
        self.history = []
        self.log = []

    def instruct(self, raw):
        """Parse, apply, log. Always returns a report dict."""
        try:
            op = parse_instruction(raw)
        except ValueError as e:
            entry = {"instruction": raw, "result": "UNUNDERSTOOD",
                     "detail": str(e)[:160]}
            self.log.append(entry)
            return {"ok": False, "text": self.text, "entry": entry,
                    "log": self.log}
        if op["op"] == "undo":
            if not self.history:
                entry = {"instruction": raw, "result": "NOTHING_TO_UNDO",
                         "detail": "the stack is empty"}
                self.log.append(entry)
                return {"ok": False, "text": self.text, "entry": entry,
                        "log": self.log}
            self.text = self.history.pop()
            entry = {"instruction": raw, "result": "UNDONE",
                     "detail": "one step back, %d left" % len(self.history)}
            self.log.append(entry)
            return {"ok": True, "text": self.text, "entry": entry,
                    "log": self.log}
        try:
            new_text, detail = apply_op(self.text, op)
        except Exception as e:
            note_failure("live_morph:apply", e)
            entry = {"instruction": raw, "result": "FAILED",
                     "detail": str(e)[:160]}
            self.log.append(entry)
            return {"ok": False, "text": self.text, "entry": entry,
                    "log": self.log}
        self.history.append(self.text)
        self.text = new_text
        entry = {"instruction": raw, "result": "APPLIED", "detail": detail,
                 "before_lines": len(self.history[-1].splitlines()),
                 "after_lines": len(new_text.splitlines())}
        self.log.append(entry)
        return {"ok": True, "text": self.text, "entry": entry,
                "log": self.log}


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Eve live morph</title><style>
body{font-family:Georgia,serif;margin:0;display:grid;
 grid-template-columns:1fr 320px 280px;height:100vh}
#doc{padding:24px;overflow:auto;border-right:2px solid #333;white-space:pre-wrap}
#mid{padding:16px;border-right:2px solid #333;overflow:auto}
#logp{padding:16px;overflow:auto;background:#f6f1e7}
button{font-size:16px;padding:8px 12px;margin:4px 0}
input{width:100%;font-size:15px;padding:8px}
.entry{border-bottom:1px solid #999;padding:6px 0;font-size:13px}
</style></head><body>
<pre id="doc"></pre>
<div id="mid"><h2>Instruction</h2>
<input id="box" placeholder="say or type: replace X with Y">
<button onclick="send()">Apply</button>
<button id="mic" onclick="listen()">&#127908; Speak</button>
<p id="hint">replace &middot; uppercase &middot; make steps &middot;
outline &middot; mutate &middot; undo</p></div>
<div id="logp"><h2>Log</h2><div id="log"></div></div>
<script>
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
function paint(t, log){
 document.getElementById('doc').textContent = t;
 document.getElementById('log').innerHTML = log.slice().reverse().map(e =>
  '<div class="entry"><b>'+e.result+'</b> '+e.instruction+
  '<br>'+(e.detail||'')+'</div>').join('');
}
async function pull(){
 const r = await fetch('/api/doc'); const j = await r.json(); paint(j.text, j.log);
}
async function send(){
 const v = document.getElementById('box').value;
 const r = await fetch('/api/instruct',{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({text:v})});
 const j = await r.json(); paint(j.text, j.log);
 document.getElementById('box').value = '';
}
function listen(){
 if(!SR){ alert('this browser has no speech recognition; type instead'); return; }
 const r = new SR(); r.lang = 'en-US';
 r.onresult = ev => { document.getElementById('box').value =
  ev.results[0][0].transcript; send(); };
 r.start();
}
pull();
</script></body></html>
"""


def make_app(state):
    """Build the request handler class bound to one LiveState."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype="application/json"):
            raw = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/":
                self._send(PAGE, "text/html; charset=utf-8")
            elif self.path == "/api/doc":
                self._send(_json.dumps({"name": state.name,
                                        "text": state.text,
                                        "log": state.log}))
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path != "/api/instruct":
                self.send_error(404)
                return
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = _json.loads(self.rfile.read(n) or b"{}")
            except Exception as e:
                note_failure("live_morph:payload", e)
                payload = {}
            res = state.instruct(payload.get("text", ""))
            self._send(_json.dumps({"ok": res["ok"], "text": res["text"],
                                    "entry": res["entry"], "log": res["log"]}))
    return Handler


def serve(state, port=0):
    """Serve the live window on loopback. Returns (server, url)."""
    server = ThreadingHTTPServer(("127.0.0.1", port), make_app(state))
    url = "http://127.0.0.1:%d/" % server.server_address[1]
    t = _threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, url
