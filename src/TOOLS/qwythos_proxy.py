# coding: utf-8
"""Qwythos XML tool-call translation proxy (stdlib only).

Qwythos emits Mythos-style tool calls::

    <tool_call>
    <function=listdir>
    <parameter=path>
    .
    </parameter>
    </function>
    </tool_call>

Ollama cannot parse that format, so ``tool_calls`` comes back null and no
agent framework can drive the model. This proxy sits in front of Ollama's
OpenAI-compatible endpoint, extracts the XML blocks deterministically, and
returns real OpenAI ``tool_calls`` (values JSON-decoded when possible, raw
strings otherwise). Anything without XML passes through untouched, so the
proxy is safe as a general Ollama front door.

Endpoints:
    GET  /health                  -> {"status": "ok"}
    GET  /v1/models               -> passthrough to Ollama
    POST /v1/chat/completions     -> translate (streaming forced off;
                                     agents don't need token streaming)

    Model alias: ``qwythos-9b-xml`` (or any name in MODEL_MAP) is rewritten
    to the real Ollama tag before forwarding.

Run: ``python qwythos_proxy.py [port]``  (default 11435)
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

OLLAMA = "http://127.0.0.1:11434"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 11435

MODEL_MAP = {
    "qwythos-9b-xml": "qwythos-9b-lenient",
}

TOOL_BLOCK = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
FUNC_BLOCK = re.compile(
    r"<function\s*=\s*(?P<name>[^>\s]+)\s*>\s*(?P<body>.*?)</function>",
    re.DOTALL,
)
PARAM_BLOCK = re.compile(
    r"<parameter\s*=\s*(?P<key>[^>\s]+)\s*>\s*(?P<value>.*?)</parameter>",
    re.DOTALL,
)


def _coerce(value: str) -> object:
    text = value.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        note_failure("qwythos_proxy:L66", exc)
        return text


def extract_tool_calls(content: str) -> tuple[str, list[dict]]:
    """Split Mythos XML blocks out of ``content``.

    Returns ``(cleaned_content, tool_calls)`` where each call is an OpenAI
    ``{"id", "type": "function", "function": {"name", "arguments"}}`` dict
    with arguments as a JSON string.
    """
    calls: list[dict] = []

    def _one(match: re.Match) -> str:
        body = match.group(1)
        for func in FUNC_BLOCK.finditer(body):
            name = func.group("name").strip()
            args = {m.group("key").strip(): _coerce(m.group("value")) for m in PARAM_BLOCK.finditer(func.group("body"))}
            calls.append(
                {
                    "id": f"call_{len(calls):04d}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            )
        return ""

    cleaned = TOOL_BLOCK.sub(_one, content).strip()
    return cleaned, calls


def _forward(path: str, body: bytes | None, method: str = "POST") -> tuple[int, bytes]:
    req = urllib.request.Request(
        OLLAMA + path, data=body, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        note_failure("qwythos_proxy:L105", exc)
        return exc.code, exc.read()


class Handler(BaseHTTPRequestHandler):
    server_version = "QwythosProxy/1.0"

    def log_message(self, *args: object) -> None:
        pass

    def _send(self, code: int, payload: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, b'{"status":"ok"}')
            return
        code, data = _forward(self.path, None, "GET")
        self._send(code, data)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, ValueError) as exc:
            note_failure("qwythos_proxy:L134", exc)
            self._send(400, b'{"error":"invalid JSON body"}')
            return
        model = req.get("model", "")
        req["model"] = MODEL_MAP.get(model, model)
        req["stream"] = False
        # Qwythos only emits tool XML reliably at low temperature; above
        # ~0.3 it answers in prose instead of calling tools, which starves
        # agentic loops. Cap temperature whenever tools are in play.
        if req.get("tools"):
            try:
                if float(req.get("temperature", 1.0)) > 0.2:
                    req["temperature"] = 0.2
            except (TypeError, ValueError) as exc:
                note_failure("qwythos_proxy:L147", exc)
                req["temperature"] = 0.2
            opts = req.get("options")
            if isinstance(opts, dict) and "temperature" in opts:
                try:
                    if float(opts["temperature"]) > 0.2:
                        opts["temperature"] = 0.2
                except (TypeError, ValueError) as exc:
                    note_failure("qwythos_proxy:L154", exc)
                    opts["temperature"] = 0.2
        code, data = _forward(
            "/v1/chat/completions", json.dumps(req).encode("utf-8"), "POST"
        )
        if code != 200:
            self._send(code, data)
            return
        try:
            resp = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, ValueError) as exc:
            note_failure("qwythos_proxy:L164", exc)
            self._send(code, data)
            return
        choices = resp.get("choices") or []
        if choices and isinstance(choices[0].get("message"), dict):
            msg = choices[0]["message"]
            content = msg.get("content") or ""
            cleaned, calls = extract_tool_calls(content)
            if calls:
                msg["content"] = cleaned or None
                msg["tool_calls"] = calls
                choices[0]["finish_reason"] = "tool_calls"
        self._send(200, json.dumps(resp).encode("utf-8"))


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"qwythos-proxy on 127.0.0.1:{PORT} -> {OLLAMA}", flush=True)
    server.serve_forever()
