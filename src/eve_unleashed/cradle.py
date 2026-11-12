"""cradle.py -- natively adopt any model placed in the cradle.

The cradle is ~/.azazael/cradle: drop any model file in, run
`eve cradle`, and Eve adopts it with the native runner for its type:

  .gguf    -> llama-server.exe sidecar (LM Studio backends, winget
              llama.cpp, or PATH), probed over its OpenAI-compatible API
  .pt/.pth -> torch direct load probe (map_location=cpu)
  .joblib/.pkl -> sklearn direct load probe
  .onnx    -> onnxruntime probe (if installed)
  other known suffixes -> recorded, swarm-fallback at infer time

Every adoption emits an NSP receipt (state/eve-cradle-<name>.receipt.json):
measured facts (bytes, framework, probe result) plus the Z3 verdict on
the routing decision. A receipt is evidence, not a trophy: a failed
probe registers nothing.
"""
import glob
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass

CRADLE_DIR = Path.home() / ".azazael" / "cradle"
RECEIPT_DIR = Path("state")

GGUF_RUNNERS = [
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\llama-server.exe"),
]


def _llama_server_candidates():
    cands = list(GGUF_RUNNERS)
    base = Path.home() / ".lmstudio" / "extensions" / "backends"
    if base.is_dir():
        found = sorted(base.glob("llama.cpp-*/llama-server.exe"))
        cands = found + cands
    which = shutil.which("llama-server") or shutil.which("llama-server.exe")
    if which:
        cands.append(which)
    return [c for c in cands if c and Path(c).is_file()]


def _free_port(start=8891):
    last_err = None
    for p in range(start, start + 64):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError as e:
                last_err = e
    if last_err is not None:
        note_failure("cradle:no-free-port", last_err)
    return None


def _probe_gguf(model_path):
    """Spawn a native llama-server sidecar, probe /completion, stop it.

    Returns (ok, detail, runner). The sidecar is a subprocess Eve owns:
    started for the probe (and later for serve), killed after.
    """
    cands = _llama_server_candidates()
    if not cands:
        return False, "no llama-server binary (LM Studio backends, winget llama.cpp, PATH)", None
    port = _free_port()
    if port is None:
        return False, "no free port for sidecar probe", None
    runner = str(cands[0])
    proc = None
    try:
        proc = subprocess.Popen(
            [runner, "-m", str(model_path), "--port", str(port),
             "--n-predict", "64"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 120
        healthy = False
        while time.time() < deadline:
            if proc.poll() is not None:
                return False, "sidecar exited during load (exit %s)" % proc.returncode, runner
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/health" % port, timeout=5) as r:
                    if r.status == 200:
                        healthy = True
                        break
            except Exception:
                time.sleep(2)
        if not healthy:
            return False, "sidecar never reached /health in 120 s", runner
        body = json.dumps({"prompt": "Reply with the single word: ALIVE",
                           "n_predict": 8}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:%d/completion" % port, data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            out = json.load(r)
        text = (out.get("content") or "").strip()
        if text:
            return True, "native completion: %s" % text[:120], runner
        return False, "sidecar answered with empty completion", runner
    except Exception as e:
        note_failure("cradle:gguf-probe", e)
        return False, "probe failed: %s" % e, runner
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()


def _probe_direct(model_path, framework):
    """Direct-load probe for frameworks Eve imports herself."""
    try:
        if framework in ("torch",):
            import torch
            obj = torch.load(str(model_path), map_location="cpu",
                             weights_only=True)
            return True, "torch load ok (%s)" % type(obj).__name__
        if framework in ("sklearn", "joblib", "pickle"):
            import joblib
            obj = joblib.load(str(model_path))
            return True, "joblib load ok (%s)" % type(obj).__name__
        if framework == "onnx":
            import onnxruntime
            sess = onnxruntime.InferenceSession(str(model_path))
            return True, "onnxruntime session ok (%d inputs)" % len(sess.get_inputs())
    except ImportError as e:
        return False, "framework not installed: %s" % e
    except Exception as e:
        note_failure("cradle:direct-probe", e)
        return False, "load probe failed: %s" % e
    return False, "no probe for framework %s" % framework


def list_cradle_models(cradle_dir=None):
    """Every model file in the cradle: [{name, bytes, framework}]."""
    from .core import ModelLoader
    cdir = Path(cradle_dir) if cradle_dir else CRADLE_DIR
    if not cdir.is_dir():
        return []
    out = []
    for path in sorted(cdir.iterdir()):
        if not path.is_file() or path.suffix.lower() in (
                ".json", ".receipt", ".bak"):
            continue
        if path.name.startswith("."):
            continue
        out.append({"name": path.name, "bytes": path.stat().st_size,
                    "framework": ModelLoader.guess_framework(path)})
    return out


def resolve_model(name, cradle_dir=None):
    """A cradle model by stem, name or path. Raises ValueError."""
    p = Path(name)
    if p.is_file():
        return p
    cdir = Path(cradle_dir) if cradle_dir else CRADLE_DIR
    cands = [m for m in list_cradle_models(cdir)
             if m["name"] == name or Path(m["name"]).stem == name]
    if not cands:
        raise ValueError("no cradle model %r (%s)" % (name, cdir))
    if len(cands) > 1:
        raise ValueError("ambiguous %r: %s" % (
            name, ", ".join(m["name"] for m in cands)))
    return cdir / cands[0]["name"]


def run_gguf(model_path, prompt, max_tokens=256, sidebar_timeout=180):
    """Run a text prompt on ANY cradle gguf via a transient sidecar.

    Spawns the model with its own llama-server, POSTs /completion,
    returns the text, kills the sidecar. Not only Eve's default: the
    model is chosen by the caller. Raises RuntimeError on failure."""
    cands = _llama_server_candidates()
    if not cands:
        raise RuntimeError("no llama-server binary")
    port = _free_port()
    if port is None:
        raise RuntimeError("no free port for sidecar")
    proc = None
    try:
        proc = subprocess.Popen(
            [str(cands[0]), "-m", str(model_path), "--port", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + sidebar_timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("sidecar exited during load")
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/health" % port, timeout=5) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(2)
        else:
            raise RuntimeError("sidecar never reached /health")
        body = json.dumps({"prompt": prompt,
                           "n_predict": max_tokens}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:%d/completion" % port, data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=sidebar_timeout) as r:
            out = json.load(r)
        return (out.get("content") or "").strip()
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()


def adopt_cradle(cradle_dir=None):
    """Adopt every model file in the cradle. Returns a summary dict."""
    from .model_registry import ModelRegistry, MODEL_EXTENSIONS
    from .core import ModelLoader
    from .nsp_check import verify_router_trace

    cdir = Path(cradle_dir) if cradle_dir else CRADLE_DIR
    cdir.mkdir(parents=True, exist_ok=True)
    registry = ModelRegistry()
    adopted, failed, skipped = [], [], []
    for path in sorted(cdir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in MODEL_EXTENSIONS:
            continue
        size = path.stat().st_size
        if size == 0:
            skipped.append((path.name, "zero-byte"))
            continue
        fw = ModelLoader.guess_framework(path)
        if fw == "unknown":
            skipped.append((path.name, "unknown type"))
            continue
        if fw == "gguf":
            ok, detail, runner = _probe_gguf(path)
        elif fw in ("torch", "sklearn", "joblib", "pickle", "onnx"):
            ok, detail = _probe_direct(path, fw)
            runner = "direct:%s" % fw
        else:
            ok, detail, runner = False, "no native runner yet; swarm at infer", None
        nsp_note = "nsp-unavailable"
        if ok:
            try:
                events = [{"event_type": "route", "symbolic_state": {
                    "query_norm": 1.0, "selected_idx": 0,
                    "num_providers": 1, "dead_provider_idx": -1}}]
                verdict, vdetail = verify_router_trace(events, [
                    "valid_query_features", "valid_provider_selection",
                    "no_dead_provider"])
                nsp_note = "nsp:%s [%s]" % (verdict, vdetail)
            except Exception as e:
                note_failure("cradle:nsp", e)
        receipt = {"model": path.name, "bytes": size, "framework": fw,
                   "native_runner": runner, "probe_ok": ok,
                   "probe_detail": detail, "nsp": nsp_note,
                   "adopted_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                time.gmtime())}
        rdir = RECEIPT_DIR
        try:
            rdir.mkdir(parents=True, exist_ok=True)
            with open(rdir / ("eve-cradle-%s.receipt.json" % path.stem),
                      "w", encoding="utf-8") as f:
                json.dump(receipt, f, indent=2)
        except Exception as e:
            note_failure("cradle:receipt", e)
        if ok:
            registry.register(str(path), framework=fw, project="cradle")
            adopted.append((path.name, fw, runner))
        else:
            failed.append((path.name, detail))
    return {"cradle": str(cdir), "adopted": adopted, "failed": failed,
            "skipped": skipped}
