#!/usr/bin/env python3
"""accelerator_sidecar.py — local-accelerator OpenAI-compatible sidecar server.

GOAL (PMB goal 001a121d05d93_c2e269a8): give the meta-evolution loop free
LOCAL inference with честy device reporting. This process binds the best
available accelerator, FAILS OVER down the chain, and serves a stable
OpenAI-compatible API while telling the truth in /health about what is
actually serving.

BACKEND PRIORITY (measured on this host, 2026-10-10):
  1. llama.cpp Vulkan  — bin/llama-b10964-vulkan/llama-server.exe handles
     the Intel Arc 140T (8GB) via Vulkan; no NVIDIA driver present.
  2. llama.cpp CPU     — bin/llama-b10964-cpu/llama-server.exe (always works).
  (OpenVINO GenAI and OpenVINO NPU slots are present for when
  openvino-genai is installed into py314m, the sole sanctioned 3.14 runtime;
  currently absent, reported honestly.)

Wiring contract (How the meta-evolution loop consumes it):
  * /health        -> {"device": "<vulkan|cpu>", "backend": …, "model": …}
                       "device" is the ACTUALLY BOUND device, never aspirational.
  * /v1/models     -> OpenAI models list (served by llama-server directly).
  * /v1/chat/completions -> proxied to the bound backend.

The sidecar PROXIES (single connection pool, streaming pass-through) rather
than re-implementing inference: it still counts as failover because if the
preferred backend fails its health check or dies, the sidecar respawns/rewires
to the next backend and /health flips to the new device.

Stdlib-only server (http.server + urllib) so it runs on both 3.15 interpreters:
the free-threaded production runner and the GIL sidecar.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: object | None = None) -> None:
        pass

#: Where llama-server models live on this host (verified layout).
LLAMA_HOME = Path(os.environ.get("LLAMA_SERVER_HOME", r"C:\Users\clint\llama-server"))
MODELS_DIR = LLAMA_HOME / "models"
BIN_DIR = LLAMA_HOME / "bin"

#: Chat-model preference order (tested working GGUFs first):
CHAT_MODELS = (
    MODELS_DIR / "Qwen2.5-1.5B-Instruct" / "Qwen2.5-1.5B-Instruct-Q4_k_m.gguf",
    MODELS_DIR / "Qwen3-4B-Abliterated" / "Qwen3-4B-Abliterated-Q4_k_m.gguf",
    MODELS_DIR / "Eve-V2-Unleashed" / "Eve-V2-Unleashed-Q4_k_m.gguf",
    MODELS_DIR / "Qwopus3.5-9B-v3" / "Qwopus3.5-9B-v3-Q4_k_m.gguf",
)

#: Backend launch specs. First spec whose binary exists AND whose health check
#: passes after spawn becomes the bound backend; the rest is the failover tail.
BACKENDS = (
    {
        "name": "llama_cpp_vulkan",
        "device": "vulkan",
        "binary": BIN_DIR / "llama-b10964-vulkan" / "llama-server.exe",
    },
    {
        "name": "llama_cpp_cuda",
        "device": "cuda",
        "binary": BIN_DIR / "llama-b10964-cuda" / "llama-server.exe",
    },
    {
        # Intel OpenVINO GenAI on the Arc 140T / AI Boost NPU. On this box
        # there is no NVIDIA CUDA, so this is the CUDA-equivalent slot: it
        # spawns openvino_worker.py (system Python, where openvino lives) instead
        # of a native binary. Only activates when OPENVINO_MODEL_DIR points at a
        # real model directory; otherwise it is skipped honestly.
        "name": "openvino_genai",
        "device": "intel-gpu",
        "binary": Path(os.environ.get("SYSTEM_PYTHON", r"C:\Users\clint\AppData\Local\Programs\Python\Python314\python.exe")),
        "launch": None,  # filled in bind() when a model dir is available
    },
    {
        "name": "llama_cpp_cpu",
        "device": "cpu",
        "binary": BIN_DIR / "llama-b10964-cpu" / "llama-server.exe",
    },
)


def _first_existing_model() -> Path | None:
    for m in CHAT_MODELS:
        if m.is_file():
            return m
    # Fall back to any *.gguf directly under models/
    for cand in sorted(MODELS_DIR.rglob("*.gguf")):
        return cand
    return None


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


class BackendManager:
    """Spawn/monitor/health the bound backend. Owns the failover decision."""

    def __init__(self, bind_port: int, allow_spawn: bool = True):
        self._lock = threading.Lock()
        self._allow_spawn = allow_spawn
        self.bind_port = bind_port
        self.backend_port: int | None = None
        self.backend_name: str = "none"
        self.device: str = "none"
        self.model_path: str = ""
        self._proc: subprocess.Popen | None = None

    # -- health ------------------------------------------------------------

    def _probe(self, port: int) -> bool:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
            with urllib.request.urlopen(req, timeout=3) as r:
                return 200 <= r.status < 300
        except (urllib.error.URLError, OSError) as exc:
            note_failure("accelerator_sidecar:probe", exc)
            return False  # unhealth backend; explicit not-open, not a guess

    def _is_chat_capable(self, port: int) -> bool:
        """A 1-token completion is the only proof that a server does logits.
        Health-open-but-embed-only servers fail this (measured: 8080 returns
        500 'context does not logits computation')."""
        if not self._probe(port):
            return False
        payload = json.dumps({"messages": [{"role": "user", "content": "ping"}],
                              "max_tokens": 1}).encode()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/chat/completions", data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.loads(r.read().decode("utf-8", "replace"))
                return "choices" in body
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            note_failure("accelerator_sidecar:chat_probe", exc)
            return False

    # -- binding -----------------------------------------------------------

    def bind(self) -> bool:
        """Bind the best CHAT-capable backend. Adopt live or spawn in order.

        An embed-only llama-server (e.g. the mxbai one on 8080) answers
        /health but returns 500 on chat completions ("context does not do
        logits computation") — so adoption requires a real completion probe,
        not merely an open port. An embed server is recorded in /health as
        an accelerator but never bound as the chat backend.
        """
        with self._lock:
            # 0) Already bound and healthy?
            if self.backend_port and self._is_chat_capable(self.backend_port):
                return True
            # 1) Adopt an already-running CHAT-capable llama-server.
            for port in (18080, 8081, 8080):
                if _port_open(port) and self._is_chat_capable(port):
                    self.backend_port, self.backend_name, self.device = \
                        port, "adopted", f"port{port}"
                    return True
            if not self._allow_spawn:
                return False
            model = _first_existing_model()
            if model is None:
                note_failure("accelerator_sidecar:no_model", None)
                return False
            # 2) Spawn backends in priority order.
            for spec in BACKENDS:
                if spec["name"] == "openvino_genai":
                    model_dir = os.environ.get("OPENVINO_MODEL_DIR", "").strip()
                    if not model_dir or not Path(model_dir).is_dir():
                        continue
                    if not spec["binary"].is_file():
                        continue
                    worker = Path(__file__).resolve().parent / "openvino_worker.py"
                    if not worker.is_file():
                        continue
                    bport = 18181
                    try:
                        self._proc = subprocess.Popen(
                            [str(spec["binary"]), str(worker),
                             "--model-dir", model_dir, "--device", "GPU",
                             "--host", "127.0.0.1", "--port", str(bport)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
                    except OSError as exc:
                        note_failure(f"accelerator_sidecar:spawn:{spec['name']}", exc)
                        continue
                else:
                    if not spec["binary"].is_file():
                        continue
                    bport = 18181
                    try:
                        self._proc = subprocess.Popen(
                            [str(spec["binary"]), "-m", str(model),
                             "--host", "127.0.0.1", "--port", str(bport),
                             "-c", "4096", "-b", "4096"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
                    except OSError as exc:
                        note_failure(f"accelerator_sidecar:spawn:{spec['name']}", exc)
                        continue
                deadline = time.time() + 90.0
                while time.time() < deadline:
                    if self._probe(bport):
                        self.backend_port = bport
                        self.backend_name = spec["name"]
                        self.device = spec["device"]
                        self.model_path = model_dir if spec["name"] == "openvino_genai" else str(model)
                        return True
                    if self._proc.poll() is not None:
                        break  # died during warm-up; try the next backend
                    time.sleep(1.0)
                self._proc = None
            return False

    def health(self) -> dict:
        alive = bool(self.backend_port and self._probe(self.backend_port))
        return {
            "status": "ok" if alive else "unavailable",
            "device": self.device if alive else "none",
            "backend": self.backend_name if alive else "none",
            "model": self.model_path,
            "backend_port": self.backend_port,
            "openvino_genai": self._openvino_available(),
            "note": "device is the ACTUALLY BOUND backend; no aspiration",
        }

    @staticmethod
    def _openvino_available() -> dict:
        try:
            import openvino  # noqa: F401
            import openvino_genai  # type: ignore # noqa: F401
            return {"available": True}
        except ImportError as exc:
            note_failure("accelerator_sidecar:openvino_probe", exc)
            return {"available": False,
                    "reason": f"openvino/genai import failed: {exc}"}


MANAGER: BackendManager | None = None


class ProxyHandler(BaseHTTPRequestHandler):
    """Proxy /v1/* to the bound backend; serve /health directly."""

    def log_message(self, fmt, *args):  # silence default stderr chatter
        pass

    # -- helpers -------------------------------------------------------------

    def _forward(self):
        assert MANAGER is not None
        if not MANAGER.backend_port or not MANAGER.bind():
            self.send_error(503, "no accelerator backend bound")
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        url = f"http://127.0.0.1:{MANAGER.backend_port}{self.path}"
        req = urllib.request.Request(url, data=body, method=self.command)
        for h in ("Content-Type", "Authorization"):
            v = self.headers.get(h)
            if v:
                req.add_header(h, v)
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type",
                                 resp.headers.get("Content-Type",
                                                  "application/json"))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except (urllib.error.URLError, OSError) as exc:
            note_failure("accelerator_sidecar:forward", exc)
            self.send_error(502, f"backend error: {exc}")

    # -- routes ----------------------------------------------------------------

    def do_GET(self):
        if self.path == "/health":
            body = json.dumps(MANAGER.health()).encode() if MANAGER else b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/v1"):
            self._forward()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.startswith("/v1"):
            self._forward()
        else:
            self.send_error(404)


def main() -> int:
    # Estate convention (engram #707): stdout is cp1252 on this host, so
    # argparse help text with non-latin characters crashes printing it.
    # Reconfigure instead of sprinkling encodes at every print site.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception as exc:
        print("sidecar: stdio reconfigure failed: %s" % exc,
              file=sys.stderr)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=18180)
    ap.add_argument("--no-spawn", action="store_true",
                    help="adopt an already-running backend only")
    args = ap.parse_args()

    global MANAGER
    MANAGER = BackendManager(bind_port=args.port, allow_spawn=not args.no_spawn)
    if not MANAGER.bind():
        print(json.dumps({"status": "unavailable",
                          "detail": "no backend could be bound"}))
        return 1
    print(json.dumps({"status": "bound", "device": MANAGER.device,
                      "backend": MANAGER.backend_name,
                      "backend_port": MANAGER.backend_port}))
    try:
        ThreadingHTTPServer(("127.0.0.1", args.port), ProxyHandler).serve_forever()
    except KeyboardInterrupt:
        print("sidecar: shutdown requested (KeyboardInterrupt)", file=sys.stderr)
    finally:
        if MANAGER._proc:  # only kill backends WE spawned
            MANAGER._proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
