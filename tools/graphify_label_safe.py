#!/usr/bin/env python3
"""graphify_label_safe.py — preflight LLM backends before `graphify label`.

Why this exists (2026-10-22, third lane): `graphify label` picks its backend by
key *presence* (detect_backend: gemini -> kimi -> claude -> openai -> deepseek
-> azure -> bedrock -> ollama) with no credit/auth preflight. An OpenAI key
with $0 balance burns all 26 batches into 429s before you learn anything. This
wrapper probes each backend with a 1-token chat call in the same priority
order, runs `graphify label` on the first backend that proves it can complete
a billed call, and aborts cleanly (exit 2, files untouched) when none works —
leaving the deterministic hub labels from `graphify update` intact.

Usage:
  python tools/graphify_label_safe.py [PATH] [--backend NAME] [--model MODEL]
      [--missing-only] [--batch-size N] [--max-concurrency N]
      [--probe-only] [--list-backends] [--dry-run] [--timeout SECS]

  PATH defaults to the repo root (this file's grandparent). Extra label flags
  (--missing-only etc.) pass through to `graphify label`.

Exit codes: 0 label ran (its exit preserved); 1 usage error;
2 no working backend (nothing written).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parent.parent  # tools/ -> repo root
GRAPHIFY_BIN = REPO / "scripts" / "graphify_bin.sh"

# (backend, key env vars, default base url, default model, needs_endpoint)
BACKENDS = (
    ("gemini", ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
     "https://generativelanguage.googleapis.com/v1beta/openai/",
     "gemini-3-flash-preview"),
    ("kimi", ("KIMI_API_KEY",),
     "https://api.moonshot.ai/v1", "kimi-k2-0711-preview"),
    ("claude", ("ANTHROPIC_API_KEY",),
     "https://api.anthropic.com", "claude-sonnet-4-6"),
    ("openai", ("OPENAI_API_KEY",),
     "https://api.openai.com/v1", "gpt-4o-mini"),
    ("deepseek", ("DEEPSEEK_API_KEY",),
     "https://api.deepseek.com/v1", "deepseek-chat"),
    ("ollama", (),
     "http://localhost:11434/v1", None),  # model resolved from /api/tags
)

CHAT_PATH = "/chat/completions"


def _env_base(backend: str, default: str) -> str:
    prefix = {"gemini": "GEMINI", "kimi": "KIMI", "claude": "ANTHROPIC",
              "openai": "OPENAI", "deepseek": "DEEPSEEK"}.get(backend)
    if prefix:
        override = os.environ.get(f"{prefix}_BASE_URL")
        if override:
            return override.rstrip("/")
    if backend == "ollama":
        return (os.environ.get("OLLAMA_BASE_URL")
                or _ollama_host() or default).rstrip("/")
    return default.rstrip("/")


def _ollama_host() -> str | None:
    host = (os.environ.get("OLLAMA_HOST") or "").strip()
    if not host:
        return None
    if "://" not in host:
        host = "http://" + host
    if host.startswith("http://:") or host.startswith("https://:"):
        host = host.replace("://:", "://localhost:", 1)
    elif host.count(":") == 1:  # bare hostname, no port
        host += ":11434"
    return host + "/v1"


def _key_for(backend: str) -> str | None:
    for var in next(b[1] for b in BACKENDS if b[0] == backend):
        val = os.environ.get(var)
        if val:
            return val
    return None


def _post(base: str, key: str | None, payload: dict,
          timeout: float) -> tuple[int, str]:
    req = urllib.request.Request(
        base + CHAT_PATH,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {key}"} if key else {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(2000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:  # noqa: F821 (imported lazily below)
        return e.code, e.read(2000).decode("utf-8", "replace")[:500]
    except Exception as e:  # connection refused, timeout, DNS, ...
        return -1, f"{type(e).__name__}: {e}"[:500]


import urllib.error  # noqa: E402  (kept late so _post reads cleanly above)


def _ollama_models(base: str, timeout: float) -> list[str]:
    """Models the local Ollama server actually has pulled (no spend)."""
    plain = base[:-len("/v1")] if base.endswith("/v1") else base
    try:
        with urllib.request.urlopen(
                plain + "/api/tags", timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def probe(backend: str, model: str | None, timeout: float,
          verbose: bool = True) -> tuple[bool, str]:
    """One 1-token chat call. True only if a billed call completed (200)."""
    base = _env_base(backend, next(b[2] for b in BACKENDS if b[0] == backend))
    key = _key_for(backend)
    if backend != "ollama" and not key:
        return False, "no key"
    if backend == "claude":
        return False, ("anthropic messages API differs from OpenAI chat schema; "
                        "probe skipped (key presence only)")
    candidates = [model] if model else []
    if backend == "ollama" and not model:
        pulled = _ollama_models(base, timeout)
        if not pulled:
            return False, "server unreachable or no models pulled"
        want = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b")
        ordered = ([want] if want in pulled else []) + \
            [m for m in pulled if m != want and "embed" not in m.lower()]
        if not ordered:
            return False, f"only embedding models pulled: {pulled}"
        candidates = ordered
    if not candidates:
        candidates = [model or next(b[3] for b in BACKENDS if b[0] == backend)]
    last = ""
    for cand in candidates:
        status, body = _post(base, key, {"model": cand, "messages": [
            {"role": "user", "content": "ok"}], "max_tokens": 1,
            "temperature": 0}, timeout)
        if status == 200:
            return True, f"{cand} ok"
        last = f"{cand}: HTTP {status} {body[:120]}"
        if verbose:
            print(f"  probe {backend}/{cand} -> HTTP {status}", file=sys.stderr)
        if status == 404 and backend == "ollama":
            continue  # model not pulled; try next pulled model
        if status in (401, 403):
            return False, f"auth rejected ({last})"
        if status == 429:
            return False, f"quota exhausted ({last})"
        if status == -1:
            return False, f"unreachable ({last})"
        return False, last
    return False, last or "no candidate model"


def pick_backend(explicit: str | None, model: str | None, timeout: float,
                 probe_only: bool = False) -> tuple[str | None, str, str]:
    """Return (backend, model, note). Backend None => nothing works."""
    order = [explicit] if explicit else [b[0] for b in BACKENDS]
    for backend in order:
        if backend not in [b[0] for b in BACKENDS]:
            return None, "", f"unknown backend '{backend}'"
        if backend != "ollama" and not _key_for(backend):
            print(f"- {backend}: skipped (no key)", file=sys.stderr)
            continue
        ok, note = probe(backend, model, timeout)
        print(f"- {backend}: {'OK ' + note if ok else 'FAIL ' + note}",
              file=sys.stderr)
        if ok:
            return backend, (model or ""), note
    return None, "", "no working backend"


def main(argv: list[str]) -> int:
    path = str(REPO)
    rest: list[str] = []
    backend = model = None
    timeout = 60.0
    probe_only = dry_run = list_only = False
    batch = conc = None
    missing_only = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--backend" and i + 1 < len(argv):
            backend, i = argv[i + 1], i + 2
        elif a.startswith("--backend="):
            backend, i = a.split("=", 1)[1], i + 1
        elif a == "--model" and i + 1 < len(argv):
            model, i = argv[i + 1], i + 2
        elif a.startswith("--model="):
            model, i = a.split("=", 1)[1], i + 1
        elif a == "--timeout" and i + 1 < len(argv):
            timeout, i = float(argv[i + 1]), i + 2
        elif a == "--probe-only":
            probe_only, i = True, i + 1
        elif a == "--dry-run":
            dry_run, i = True, i + 1
        elif a == "--list-backends":
            list_only, i = True, i + 1
        elif a == "--missing-only":
            missing_only, i = True, i + 1
        elif a.startswith("--batch-size"):
            batch, i = a, i + 1
        elif a.startswith("--max-concurrency"):
            conc, i = a, i + 1
        elif a.startswith("-"):
            print(f"unknown flag {a}", file=sys.stderr)
            return 1
        else:
            path, i = a, i + 1
    if list_only:
        for b, keys, _base, _model in BACKENDS:
            print(f"{b}: key={'yes' if _key_for(b) else 'no'}"
                  + ("" if b != "ollama" else " (keyless, local)"))
        print("probe order: " + " -> ".join(b[0] for b in BACKENDS))
        return 0
    chosen, _m, note = pick_backend(backend, model, timeout)
    if not chosen:
        print(f"ABORT: {note}. graphify-out labels left untouched (hub names "
              f"stand). Restore billing or pull a local model, then retry.",
              file=sys.stderr)
        return 2
    # POSIX form: Git Bash strips backslashes in absolute Windows paths
    # (bash C:\x -> C:x, exit 127). Forward slashes survive everywhere here.
    cmd = ["bash", GRAPHIFY_BIN.as_posix(), "label", path,
           f"--backend={chosen}"]
    if model:
        cmd.append(f"--model={model}")
    if missing_only:
        cmd.append("--missing-only")
    if batch:
        cmd.append(batch)
    if conc:
        cmd.append(conc)
    if probe_only or dry_run:
        print(f"backend={chosen} ({note})")
        print("would run:", " ".join(cmd))
        return 0
    print(f"backend={chosen} ({note}); running: {' '.join(cmd)}",
          file=sys.stderr)
    # timeout= keeps the staged anti-pattern gate green (subprocess_no_timeout);
    # 3600s covers a full 26-batch serial ollama relabel with headroom.
    try:
        return subprocess.call(cmd, timeout=3600)
    except OSError as exc:
        print(f"ABORT: cannot launch graphify ({exc}); labels untouched.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
