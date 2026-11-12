"""Windows service + daemon wrapper for the always-on llama-server (Eve Unleashed Phase 1)."""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
import urllib.request

try:
    from CORE.failure_ledger import note_failure
except ImportError:
    def note_failure(where, exc=None):
        pass

try:
    import win32event  # pyright: ignore[reportMissingModuleSource]
    import win32service  # pyright: ignore[reportMissingModuleSource]
    import win32serviceutil  # pyright: ignore[reportMissingModuleSource]
    _WIN32_SERVICE_AVAILABLE = True
except ImportError as exc:
    note_failure("service_manager:win32service", exc)
    win32event = win32service = win32serviceutil = None
    _WIN32_SERVICE_AVAILABLE = False


#: Fallback chain (Phase 1: model hot-swapping). First live model wins;
#: a model whose GGUF is missing falls through to the next entry.
FALLBACK_CHAIN = (
    "gemma-4-e2b-it-Q8_0.gguf",
    "Qwen2.5-1.5B-instruct-Q8_0.gguf",
    "InternVL3-8B-Q8_0.gguf",
)


def _resolve_paths():
    base = Path(os.environ.get("AZAZAEL_ROOT", r"C:\Azazael\azazael"))
    llama = os.environ.get("LLAMA_SERVER_EXE") or str(base / "tools" / "llama-server.exe")
    default_model = os.environ.get("EVE_DEFAULT_MODEL") or str(
        base / "models" / FALLBACK_CHAIN[0]
    )
    port = int(os.environ.get("EVE_LLAMA_PORT", "18080"))
    return llama, default_model, port


class LlamaServerManager:
    """Supervises a llama-server subprocess with auto-restart + health probe + fallback chain."""

    def __init__(self, exe, model, port, health_timeout=30.0):
        self.exe = exe
        self.default_model = model
        self.port = port
        self.health_timeout = health_timeout
        self.model = model  # current active model
        self._proc = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.latency_samples: list[float] = []

    def start(self):
        with self._lock:
            self._stop.clear()
            self._proc = self._spawn(self.exe, self.model, self.port)
        t = threading.Thread(target=self._supervisor, name="llama-supervisor", daemon=True)
        t.start()

    def _spawn(self, exe, model, port):
        if not os.path.exists(exe):
            raise FileNotFoundError(f"llama-server binary not found: {exe}")
        args = [exe, "-m", model, "--port", str(port), "--host", "127.0.0.1", "-ngl", "0"]
        return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # scanner:ignore

    def _healthy(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=3) as r:
                return r.status == 200
        except Exception as exc:
            note_failure("service_manager:healthy", exc)
            return False

    def _supervisor(self):
        while not self._stop.is_set():
            p = None
            with self._lock:
                p = self._proc
            if p is None or p.poll() is not None:
                # crashed — restart with fallback chain
                self.model = self._pick_live_model()
                try:
                    with self._lock:
                        if self._proc is not None and self._proc.poll() is None:
                            pass
                        self._proc = self._spawn(self.exe, self.model, self.port)
                except Exception:
                    time.sleep(2)
                    continue
            # health check
            if not self._healthy():
                time.sleep(1)
            else:
                self._stop.wait(5)

    def _pick_live_model(self):
        """Return the first model whose GGUF exists on disk (fallback chain)."""
        base = Path(os.environ.get("AZAZAEL_ROOT", r"C:\Azazael\azazael")) / "models"
        for name in FALLBACK_CHAIN:
            p = base / name
            if p.exists():
                return str(p)
        return self.default_model

    def stop(self):
        self._stop.set()
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=10)
                except Exception:
                    self._proc.kill()
            self._proc = None

    def swap_model(self, new_model):
        with self._lock:
            self.model = new_model
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
                self._proc.wait(timeout=10)
            self._proc = self._spawn(self.exe, self.model, self.port)

    def record_latency(self, ms: float):
        self.latency_samples.append(ms)
        if len(self.latency_samples) > 5000:
            self.latency_samples = self.latency_samples[-5000:]

    def avg_latency_ms(self) -> float:
        if not self.latency_samples:
            return 0.0
        return sum(self.latency_samples) / len(self.latency_samples)

    def benchmark_inference(self, prompt: str, iterations: int = 5):
        """Benchmark per-token inference latency against the running server."""
        import json, time, urllib.request, urllib.error
        times = []
        for _ in range(iterations):
            payload = json.dumps({
                "prompt": prompt, "max_tokens": 32, "stream": False
            }).encode("utf-8")
            t0 = time.perf_counter()
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{self.port}/completion", data=payload,
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=self.health_timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8", "replace"))
                ms = (time.perf_counter() - t0) * 1000
                self.record_latency(ms)
                times.append(ms)
            except Exception as exc:
                try:
                    from CORE.failure_ledger import note_failure
                    note_failure("service_manager:benchmark_inference", exc)
                except Exception as exc:
                    note_failure("service_manager:benchmark", exc)
                    pass
                times.append(None)
        return times


if _WIN32_SERVICE_AVAILABLE:
    class EveLlamaService(win32serviceutil.ServiceFramework):  # pyright: ignore[reportOptionalMemberAccess]
        _svc_name_ = "EveUnleashedLlamaServer"
        _svc_display_name_ = "Eve Unleashed - llama-server"
        _svc_description_ = "Auto-starting llama-server supervised by Eve Unleashed Phase 1."

        def __init__(self, args):
            super().__init__(args)
            self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)  # pyright: ignore[reportOptionalMemberAccess]
            exe, model, port = _resolve_paths()
            self.manager = LlamaServerManager(exe, model, port)

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)  # pyright: ignore[reportOptionalMemberAccess]
            win32event.SetEvent(self.hWaitStop)  # pyright: ignore[reportOptionalMemberAccess]
            self.manager.stop()

        def SvcDoRun(self):
            self.manager.start()
            win32event.WaitForSingleObject(self.hWaitStop, win32event.INFINITE)  # pyright: ignore[reportOptionalMemberAccess]


if __name__ == "__main__":
    if _WIN32_SERVICE_AVAILABLE and len(sys.argv) >= 2 and sys.argv[1].lower() in {"install", "start", "stop", "remove"}:
        win32serviceutil.HandleCommandLine(EveLlamaService)  # pyright: ignore[reportOptionalMemberAccess]
    else:
        # daemon mode (non-service) for dev/testing
        exe, model, port = _resolve_paths()
        m = LlamaServerManager(exe, model, port)
        m.start()
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            m.stop()
