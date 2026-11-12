"""openvino_worker.py -- the out-of-process half of the Intel accelerator recipe.

WHY THIS FILE EXISTS
--------------------
`tools/accelerator_preflight.py` measures the fact this project keeps rediscovering:
on this host the Arc GPU is reachable from Python, but **not from the production
interpreter**. py315t is 3.15.0rc3 free-threaded and its torch is `+cpu`
(`torch.xpu.is_available() is False`), and OpenVINO is not importable there at
all. The interpreter that CAN bind the Arc is `system-3.14`
(`openvino 2026.4.0` -> CPU, GPU `Intel(R) Arc(TM) 140T GPU (8GB) (iGPU)`,
NPU `Intel(R) AI Boost`).

So the accelerator is reached the way a CUDA user reaches a GPU that lives on a
different box: **across an OpenAI-compatible HTTP boundary.** That turns "find a
free-threaded 3.15 XPU wheel that does not exist" into "start a process", which is
a problem this project can actually solve. `tools/accelerator_sidecar.py` is the
proxy in front; this is the process behind it.

WHAT IT MUST NOT DO
-------------------
It must not report a device it did not bind. Every `/health` field is read back
from OpenVINO itself (`Core.available_devices`, `FULL_DEVICE_NAME`), never from a
config file or a command-line default. The CAPABILITIES.md incident -- a document
asserting `XPU available: True` while the production interpreter answered False --
is the failure mode this project does not need twice.

Token counts are reported ONLY when OpenVINO supplies real perf metrics. There is
no whitespace-splitting estimate here: a fabricated tokens/sec figure is worse than
no figure, because it silently wins arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

_PIPE: Any = None
_LOCK = threading.Lock()
_META: Dict[str, Any] = {}
_GENERATE_CALLS = 0


def load(model_dir: str, device: str) -> Dict[str, Any]:
    """Load the pipeline and record what the runtime actually bound."""
    global _PIPE
    import openvino as ov
    import openvino_genai as og

    meta: Dict[str, Any] = {
        "backend": "openvino",
        "requested_device": device,
        "model_dir": model_dir,
        "openvino": getattr(ov, "__version__", "?"),
        "openvino_genai": getattr(og, "__version__", "?"),
    }

    core = ov.Core()
    available = list(core.available_devices)
    meta["available_devices"] = available
    names = {}
    for d in available:
        try:
            names[d] = core.get_property(d, "FULL_DEVICE_NAME")
        except Exception:
            names[d] = None
    meta["device_names"] = names

    if device not in available:
        # An unbound device is a fact to report, not a value to invent.
        meta["bound"] = False
        meta["why"] = ("requested device %r is not among %s" % (device, available))
        return meta

    _PIPE = og.LLMPipeline(model_dir, device)
    meta["bound"] = True
    meta["bound_device"] = device
    meta["bound_device_name"] = names.get(device)
    return meta


def generate(prompt: str, max_tokens: int, temperature: float) -> Dict[str, Any]:
    """Run one completion. Returns text plus whatever real timings exist."""
    global _GENERATE_CALLS
    import openvino_genai as og

    if _PIPE is None:
        return {"error": "pipeline not loaded"}

    cfg = og.GenerationConfig()
    cfg.max_new_tokens = max_tokens
    try:
        cfg.temperature = float(temperature)
    except Exception:
        print("openvino_worker: bad temperature %r, keeping default" % (temperature,),
              file=sys.stderr)
        pass
        t0 = time.perf_counter()
        out = _PIPE.generate(prompt, cfg)
        elapsed = time.perf_counter() - t0
        _GENERATE_CALLS += 1

    text = out if isinstance(out, str) else getattr(out, "texts", "")
    if isinstance(text, (list, tuple)):
        text = text[0] if text else ""
    text = text if isinstance(text, str) else str(text)

    res: Dict[str, Any] = {"text": text, "elapsed_s": round(elapsed, 4)}
    # Real metrics only. Absent metrics stay absent.
    pm = getattr(out, "perf_metrics", None)
    if pm is not None:
        for key, getter in (("prompt_tokens", "get_num_input_tokens"),
                            ("generated_tokens", "get_num_generated_tokens")):
            fn = getattr(pm, getter, None)
            if callable(fn):
                try:
                    res[key] = int(fn())
                except Exception:
                    pass  # scanner:ignore -- best-effort optional metric; absent stays absent
            fn = getattr(pm, getter, None)
            if callable(fn):
                try:
                    v = fn()
                    res[key] = round(float(v.total_seconds() if hasattr(v, "total_seconds")
                                           else v), 6)
                except Exception:
                    pass  # scanner:ignore -- best-effort optional metric; absent stays absent
    return res


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "azazael-openvino-worker"

    def log_message(self, *a):  # keep the sidecar's console readable
        pass

    def _send(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in ("/health", "/v1/health", ""):
            hp = dict(_META)
            hp["generations"] = _GENERATE_CALLS
            hp["loaded"] = _PIPE is not None
            self._send(200, hp)
        elif self.path.rstrip("/") == "/v1/models":
            self._send(200, {"data": [{"id": str(_META.get("model_dir", "openvino")),
                                       "object": "model"}]})
        else:
            self._send(404, {"error": "not found", "path": self.path})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send(404, {"error": "not found", "path": self.path})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            self._send(400, {"error": "bad request: %s" % e})
            return

        messages = req.get("messages") or []
        prompt = "\n".join(
            str(m.get("content", "")) for m in messages
            if isinstance(m, dict) and m.get("role") != "system") or str(req.get("prompt", ""))
        out = generate(prompt, int(req.get("max_tokens") or 64),
                       float(req.get("temperature") or 0.0))
        if "error" in out:
            self._send(503, out)
            return

        usage = {}
        if "prompt_tokens" in out:
            usage["prompt_tokens"] = out["prompt_tokens"]
        if "generated_tokens" in out:
            usage["completion_tokens"] = out["generated_tokens"]
        self._send(200, {
            "id": "openvino-worker",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": str(_META.get("model_dir", "openvino")),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": out["text"]}}],
            "usage": usage,
            "device": _META.get("bound_device_name"),
            "timings": {k: out[k] for k in ("elapsed_s", "ttft_s", "tpot_s") if k in out},
        })


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--device", default="GPU", help="GPU, NPU, CPU or AUTO")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8190)
    args = ap.parse_args()

    try:
        meta = load(args.model_dir, args.device)
    except Exception as e:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}))
        return 2
    _META.update(meta)
    print(json.dumps({"ok": True, **{k: v for k, v in meta.items()
                                     if k in ("bound", "bound_device", "bound_device_name",
                                              "available_devices", "openvino_genai")}}))
    sys.stdout.flush()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("openvino_worker: shutting down on keyboard interrupt", file=sys.stderr)
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
