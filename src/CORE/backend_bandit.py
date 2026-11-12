# coding: utf-8
"""backend_bandit.py — Thompson routing over inference backends per model.

Arms: wrapper-cpu, wrapper-sycl, ollama. Seeded from bench_results.jsonl
tok/s; updated with measured rewards. The 9B tie and Eve-Ollama facts
live here as data, not doctrine.
"""
import json
import math
import random
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

BACKENDS = ("cpu", "sycl", "ollama")
BENCH = Path(r"C:\Users\clint\AppData\Local\Temp\opencode\bench_results.jsonl")


class BackendBandit:
    VERSION = "1.0.0"

    def __init__(self, seed: float = 1.0):
        self._rng = random.Random(0)
        # (model, backend) -> [successes, failures, tok_sum, n]
        self._stats: dict[tuple, list] = {}
        self._seed = float(seed)
        self._hydrate_from_bench()

    def _hydrate_from_bench(self):
        """Seed priors from measured tok/s rows (backend-compare + GO rows).

        Throughput is carried as its own annotated quantity (tok_sum, n) and
        is NEVER folded into the success count: counts and tok/s are different
        spaces and a Beta posterior cannot tell them apart (unit fusion).
        """
        try:
            lines = BENCH.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            note_failure("backend_bandit:L31", exc)
            return
        for line in lines:
            try:
                r = json.loads(line)
            except Exception as exc:
                note_failure("backend_bandit:L36", exc)
                continue
            if r.get("type") == "backend-compare":
                m = r.get("model", "?")
                for b, k in (("sycl", "sycl_tps"), ("cpu", "cpu_tps")):
                    t = float(r.get(k) or 0)
                    if t > 0:
                        s = self._stats.setdefault((m, b), [0.0, 0.0, 0.0, 0])
                        # throughput recorded as its own quantity; NOT a pseudo-success
                        s[2] += t
                        s[3] += 1
            elif r.get("status") == "GO" and r.get("tps"):
                key = (r.get("file", "?")[:30], "cpu")
                s = self._stats.setdefault(key, [0.0, 0.0, 0.0, 0])
                s[2] += float(r["tps"])
                s[3] += 1
            elif r.get("status") == "GO-VIA-OLLAMA":
                key = (r.get("file", "?")[:30], "ollama")
                s = self._stats.setdefault(key, [0.0, 0.0, 0.0, 0])
                s[0] += 1.0
                s[3] += 1

    def pick(self, model: str, path: str = "") -> str:
        """Thompson draw over backends for this model (substring match).

        Census veto (Eve-class): if path fingerprints as unloadable
        natively, ollama is forced — no exploration draw against physics.
        """
        if path:
            try:
                from CORE.gguf_census import fingerprint
                fp = fingerprint(path)
                if fp.get("magic_ok") and fp.get("loadable") is False:
                    return "ollama"
            except Exception as exc:
                note_failure("backend_bandit:L71", exc)
        cands = {}
        for (m, b), (s, f, t, n) in self._stats.items():
            if m in model or model in m:
                if b not in cands:
                    cands[b] = [0.0, 0.0, 0.0, 0]
                cands[b][0] += s + self._seed
                cands[b][1] += f + self._seed
                cands[b][2] += t
                cands[b][3] += n
        if not cands:
            return "cpu"  # doctrine default: CPU wins ties on this box
        draws = {}
        mean_tps = {}
        for b, (s, f, t, n) in cands.items():
            draws[b] = self._rng.betavariate(max(1.0, s), max(1.0, f))
            mean_tps[b] = t / max(1, n)
        # unseen backends get a small exploration draw
        for b in BACKENDS:
            if b not in draws:
                draws[b] = self._rng.betavariate(1.0, 3.0)
                mean_tps[b] = 0.0
        # throughput (tok/s) is a documented tiebreak only, never the posterior
        return max(draws, key=lambda b: (draws[b], mean_tps[b]))

    def update(self, model: str, backend: str, ok: bool, tps: float = 0.0):
        s = self._stats.setdefault((model[:30], backend), [0.0, 0.0, 0.0, 0])
        if ok:
            s[0] += 1.0
        else:
            s[1] += 1.0
        s[2] += tps
        s[3] += 1

    def table(self, model: str) -> dict:
        out = {}
        for (m, b), (s, f, t, n) in self._stats.items():
            if m in model or model in m:
                out[b] = {"w": round(s / (s + f + 1e-9), 3), "n": n,
                          "mean_tps": round(t / max(1, n), 2)}
        return out
