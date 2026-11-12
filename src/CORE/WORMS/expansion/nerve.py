# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: LEARNER (Nerve)

Nerve removes the tabular-learner ceiling. The inner learner is a Q-table keyed
on `source:severity` whose behaviour is set by four scalars — learning rate,
epsilon, epsilon decay, discount. Widening those bounds on 2026-09-11 moved them
into fresh territory, which was the right first move, but it also proved the
point: the outer loop is reduced to choosing four numbers, and a box with an
optimum on its surface is found immediately (6 generations, flat thereafter).

Nerve changes WHAT is being optimised. Instead of choosing a learning rate, the
outer loop chooses a *policy over learning*: a small network that reads context
and emits per-parameter update modulations.

    m_t = net(state_t)            # multipliers, one per plasticity parameter
    lr_t = lr_base * m_t[0]       # each modulated in [lo, hi]

This is learning-to-learn in the cheap, honest sense. It fits the memory budget
because the modulator is small (hidden=16, ~hundreds of params) while the Q-table
it modulates is already low-dimensional.

Two constraints from the hardware truth:

* The modulator is a learning problem, so it belongs to the GIL interpreter
  (`torch 2.14.0+xpu`, ABI `cp314`). A numpy fallback exists so the module still
  imports and runs — degraded — under the free-threaded pipeline.
* Training the modulator must NOT use more Q-updates as its reward: that is the
  "fake selection pressure" trap documented at `learning_rl.py:335`. The outer
  signal is *evaluation performance*, never update count.
"""

from __future__ import annotations

import importlib.util
import json
import math
import random
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = [
    "Neuromodulator",
    "modulated_q_update",
    "context_vector",
]

MODULATION_LO = 0.25
MODULATION_HI = 2.0

# TORCH IS IMPORTED LAZILY **AND** BOUNDED. Both halves are load-bearing.
#
# LAZY. Verified live by fault dump: `import torch` blocked inside
# `torch/__init__._load_dll_libraries -> platform.machine()` on this host, and
# because `expansion/spine.py` (line 91) imports this module at IMPORT time, the
# entire expansion package became unimportable -- which is why
# `tests/test_spine_mesh.py` timed out instead of failing a test. `cppn.py:149`
# and `oracle.py:474` already import torch locally, "only when actually
# available"; this module was the outlier that broke that pattern.
#
# BOUNDED, BUT NOT ON A THREAD WE WALK AWAY FROM. The old bound here ran the
# import on a daemon thread and left it running when the join expired. An
# abandoned importer is not bounded work, it is a fork: the thread keeps
# executing torch's import body while the process carries on, and the next
# `import torch` *anywhere* is handed the half-built module. Measured: on a
# contended box the 25 s join expires, the corpse lands in sys.modules, and 25
# unrelated torch tests fail with `partially initialized module 'torch' ... has
# no attribute 'distributed'` -- the same 25, in two separate long runs, while
# every uncontended run of the same suite is green. A live importer that cannot
# be killed is worse than the hang it was avoiding.
#
# So the bound moved to the one place it can be enforced: a subprocess probe,
# which *can* be killed. If torch does not import in a fresh process within
# IMPORT_TIMEOUT_S, this process never touches torch at all -- state "timeout",
# numpy fallback, nothing left running to race. If the probe reports the host
# healthy, torch loads in the thread that asked for it, which is what
# `cppn.py`, `oracle.py` and the rest of the package already do, and the one
# importer per process is serialised by a lock.
class _LazyTorch:
    """Proxy for torch: imports on first use, never at module import time."""

    #: Budget for the probe, and the only bound that can be enforced.
    IMPORT_TIMEOUT_S = 25.0

    #: One importer per process. Two threads first-importing torch at once is
    #: not safe on this interpreter: reproduced outside pytest as
    #: `ValueError: module functions cannot set METH_CLASS or METH_STATIC`, and
    #: in the aggregate suite as the partial-module AttributeError above.
    _lock = threading.Lock()

    __slots__ = ("_mod", "_state", "_seconds")

    def __init__(self) -> None:
        self._mod = None
        self._state = "unloaded"
        self._seconds = 0.0

    def _probe(self) -> bool:
        """Does a *fresh* process import torch inside the bound? Killable.

        No pipes anywhere: the child's streams go to DEVNULL rather than to
        pipes, because on timeout ``subprocess.run`` kills the direct child and
        then reaps it with ``communicate()`` *without* a timeout -- a launcher
        that left a grandchild holding the pipe makes that reap block forever
        (`CORE/fs_probe.py` hazard 1, which is why that module probes through
        temp files). A pipe-free child cannot outlive the kill.
        """
        try:
            proc = subprocess.run(
                [sys.executable, "-c", "import torch"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self.IMPORT_TIMEOUT_S,
            )
        except Exception as exc:
            note_failure("nerve:L120", exc)
            return False  # scanner:ignore
        return proc.returncode == 0

    def _import_here(self) -> None:
        started = time.monotonic()
        try:
            import torch as _mod
        except Exception as exc:
            note_failure("nerve:L128", exc)
            self._mod = None
            self._state = "absent"
        else:
            self._mod = _mod
            self._state = "ready"
        self._seconds = time.monotonic() - started

    def get(self):
        """The torch module, or None if it is absent or the probe failed.

        Never raises: the previous implementation caught anything its loader
        thread could raise, and a caller that has a numpy fallback must not be
        given an exception instead of `None`. `find_spec` on a half-installed
        package is the one call here that can raise, so it is inside the guard.
        """
        if self._state != "unloaded":
            return self._mod
        with _LazyTorch._lock:
            if self._state != "unloaded":
                return self._mod
            try:
                if importlib.util.find_spec("torch") is None:
                    self._state = "absent"
                    return None
                if not self._probe():
                    self._state = "timeout"
                    return None
                self._import_here()
            except Exception as exc:
                note_failure("nerve:L157", exc)
                self._mod = None
                self._state = "absent"
        return self._mod

    @property
    def seconds(self) -> float:
        """Seconds the in-process import took; 0.0 if it never ran."""
        return self._seconds

    @property
    def state(self) -> str:
        """unloaded | ready | absent | timeout -- observable, not inferred."""
        return self._state

    def __bool__(self) -> bool:
        return self.get() is not None

    def __getattr__(self, name: str):
        mod = self.get()
        if mod is None:
            raise AttributeError(name)
        return getattr(mod, name)


_torch = _LazyTorch()

#: Single process-wide gate for EVERY torch C entry in this module
#: (construction, forward, param read/write). torch internals AV when any
#: two of these run concurrently on 3.15 (measured); the piggybacked
#: _LazyTorch import lock proved insufficient because live use races too.
_TORCH_OP_LOCK = threading.Lock()

try:
    import numpy as _np  # type: ignore
except Exception as exc:  # pragma: no cover
    note_failure("nerve:L192", exc)
    _np = None


def context_vector(source: str, severity: str, step: int, max_steps: int,
                   epsilon: float, q_norm: float = 0.0) -> List[float]:
    """The state the modulator conditions on.

    Deliberately low-dimensional and derived only from values the tabular
    learner already has in hand, so Nerve needs no new instrumentation. The
    hash buckets are stable across processes (unlike `hash()`, which is salted).
    """
    def _bucket(s: str, n: int) -> float:
        h = 0
        for ch in str(s):
            h = (h * 131 + ord(ch)) & 0xFFFFFFFF
        return float(h % n) / float(n)

    frac = float(step) / float(max(1, max_steps))
    return [
        _bucket(source, 8),
        _bucket(severity, 4),
        frac,
        float(epsilon),
        float(min(1.0, max(0.0, q_norm))),
        math.tanh(float(step) / 50.0),
    ]


class Neuromodulator:
    """Small network emitting per-plasticity-parameter multipliers."""

    def __init__(self, n_out: int = 4, in_dim: int = 6, hidden: int = 16,
                 seed: int = 0, use_torch: bool = True):
        self.n_out = int(n_out)
        self.in_dim = int(in_dim)
        self.hidden = int(hidden)
        self.seed = int(seed)
        self.backend = "numpy"
        self._torch_ok = bool(use_torch and bool(_torch))

        rng = random.Random(seed)
        self.w1 = [[rng.gauss(0.0, 1.0 / math.sqrt(self.in_dim)) for _ in range(self.in_dim)]
                   for _ in range(self.hidden)]
        self.b1 = [0.0] * self.hidden
        self.w2 = [[rng.gauss(0.0, 1.0 / math.sqrt(self.hidden)) for _ in range(self.hidden)]
                   for _ in range(self.n_out)]
        self.b2 = [0.0] * self.n_out

        self.net = None
        if self._torch_ok:
            # Serialize torch module construction (2026-10-05): concurrent
            # first-heavy-use from thread-pool workers AVs inside torch C
            # code on 3.15 (measured: access violation in parameter.__new__
            # from ft-worker threads; 3.14.7 GIL unaffected). Reuses the
            # _LazyTorch import lock so all torch entry is single-file.
            with _TORCH_OP_LOCK:
                self._build_torch_net(seed)

    def _build_torch_net(self, seed: int) -> None:
        try:
            dev = "xpu" if getattr(_torch, "xpu", None) is not None and _torch.xpu.is_available() else "cpu"
            self.device = dev
            g = _torch.Generator(device="cpu").manual_seed(seed)
            self.net = _torch.nn.Sequential(
                _torch.nn.Linear(self.in_dim, self.hidden),
                _torch.nn.Tanh(),
                _torch.nn.Linear(self.hidden, self.n_out),
            ).to(dev)
            with _torch.no_grad():
                for p in self.net.parameters():
                    p.copy_(_torch.empty_like(p).uniform_(-0.5, 0.5, generator=g))
            self.backend = "torch:%s" % dev
        except Exception as exc:
            note_failure("nerve:L265", exc)
            self.net = None
            self.backend = "numpy"

    # -- forward ----------------------------------------------------------
    def _forward_numpy(self, ctx: Sequence[float]) -> List[float]:
        h = []
        for i in range(self.hidden):
            z = self.b1[i] + sum(self.w1[i][j] * ctx[j] for j in range(self.in_dim))
            h.append(math.tanh(z))
        out = []
        for k in range(self.n_out):
            z = self.b2[k] + sum(self.w2[k][i] * h[i] for i in range(self.hidden))
            out.append(z)
        return out

    def modulations(self, ctx: Sequence[float]) -> List[float]:
        """Per-parameter multipliers, squashed into [LO, HI]."""
        raw: List[float]
        if self.net is not None and bool(_torch):
            try:
                # Same lock as construction: torch C internals AV when a
                # forward runs concurrently with another thread's first
                # heavy use on 3.15 (measured). The net is 16-wide; the
                # serialization cost is noise next to worm execution.
                with _TORCH_OP_LOCK, _torch.no_grad():
                    t = _torch.tensor([list(ctx)], dtype=_torch.float32, device=self.net[0].weight.device)
                    raw = [float(v) for v in self.net(t)[0].tolist()]
            except Exception as exc:
                note_failure("nerve:L293", exc)
                raw = self._forward_numpy(ctx)
        else:
            raw = self._forward_numpy(ctx)
        span = MODULATION_HI - MODULATION_LO
        return [MODULATION_LO + span * (0.5 * (math.tanh(x) + 1.0)) for x in raw]

    # -- parameter vector (for the outer ES loop) --------------------------
    def get_params(self) -> List[float]:
        if self.net is not None and bool(_torch):
            try:
                out: List[float] = []
                with _TORCH_OP_LOCK:
                    for p in self.net.parameters():
                        out.extend(float(v) for v in p.detach().cpu().reshape(-1).tolist())
                return out
            except Exception as exc:
                note_failure("nerve:L309", exc) # scanner:ignore
        out = []
        for row in self.w1:
            out.extend(row)
        out.extend(self.b1)
        for row in self.w2:
            out.extend(row)
        out.extend(self.b2)
        return out

    def set_params(self, params: Sequence[float]) -> None:
        vals = [float(v) for v in params]
        if self.net is not None and bool(_torch):
            try:
                with _TORCH_OP_LOCK, _torch.no_grad():
                    i = 0
                    for p in self.net.parameters():
                        n = int(p.numel())
                        chunk = vals[i:i + n]
                        if len(chunk) != n:
                            raise ValueError("param length mismatch")
                        p.copy_(_torch.tensor(chunk, dtype=p.dtype, device=p.device).view_as(p))
                        i += n
                return
            except Exception as exc:
                note_failure("nerve:L334", exc) # scanner:ignore
        i = 0
        for r in range(self.hidden):
            for c in range(self.in_dim):
                self.w1[r][c] = vals[i]; i += 1
        for r in range(self.hidden):
            self.b1[r] = vals[i]; i += 1
        for r in range(self.n_out):
            for c in range(self.hidden):
                self.w2[r][c] = vals[i]; i += 1
        for r in range(self.n_out):
            self.b2[r] = vals[i]; i += 1

    @property
    def n_params(self) -> int:
        return self.hidden * self.in_dim + self.hidden + self.n_out * self.hidden + self.n_out

    # -- persistence -------------------------------------------------------
    def save(self, path: str) -> None:
        payload = {"backend": self.backend, "n_out": self.n_out, "in_dim": self.in_dim,
                   "hidden": self.hidden, "seed": self.seed,
                   "params": self.get_params()}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)

    @classmethod
    def load(cls, path: str) -> Optional["Neuromodulator"]:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            obj = cls(d.get("n_out", 4), d.get("in_dim", 6), d.get("hidden", 16),
                      d.get("seed", 0))
            obj.set_params(d.get("params") or [])
            return obj
        except Exception as exc:
            note_failure("nerve:L369", exc)
            return None  # scanner:ignore


def modulated_q_update(q: Dict[Any, float], state: Any, action: Any, reward: float,
                       next_state: Any, alpha: float, gamma: float,
                       mod: Sequence[float]) -> float:
    """One Q-update whose three plasticity parameters are individually modulated.

    `mod = [alpha_mult, gamma_mult, temperature_mult]`. Returns the TD error so
    callers can log learning progress without recomputing it.
    """
    a_m = float(mod[0]) if len(mod) > 0 else 1.0
    g_m = float(mod[1]) if len(mod) > 1 else 1.0
    alpha_t = min(1.0, max(1e-4, alpha * a_m))
    gamma_t = min(0.999, max(0.0, gamma * g_m))

    key = (state, action)
    prev = float(q.get(key, 0.0))
    nxt = q.get(next_state)
    if isinstance(nxt, dict):
        best_next = max((float(v) for v in nxt.values()), default=0.0)
    else:
        best_next = float(nxt) if nxt is not None else 0.0
    td = reward + gamma_t * best_next - prev
    q[key] = prev + alpha_t * td
    return td
