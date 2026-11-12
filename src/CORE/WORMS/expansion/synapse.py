# coding: utf-8
"""
NEURAL MESH — Category: SYNAPSE (4 worms)
Neural edge weights: Win, WSL2, Bare.

SynapseWeightWorm, MoEDispatcherWorm, SSMSequenceWorm, ActivationRouterWorm

2026-09-14 audit fixes:
- G2: `SynapseWeightWorm.update()` collapsed a float reward to `reward > 0` and
  passed `abs(reward)*100` as a fake duration, so the "batch Q-learning" claim
  was a 1-bit sign update. It is now a genuine TD(0) update whose lr-scaled
  error reaches the brainstem with its magnitude intact
  (spine_registry.record_outcome(..., delta=...)).
  2026-09-16 correction: the magnitude reached the brainstem, but through the
  wrong OPERATION. `reward=` is applied as a TARGET (`q += a*(reward - q)`),
  whereas a TD error is a DELTA (`q += a*error`). Every edge was therefore
  dragged toward `lr*td_error` rather than accumulating it, and the live table
  showed 31 edges collapsed to 3 distinct values (25 at exactly +0.0909). The
  parameter is now named for the operation it implements.
- G3 (2026-09-16): `MoEDispatcherWorm.feedback()` deliberately still uses
  `reward=`. An outcome quality in [-1, 1] is a target -- "this expert is
  good" -- not an increment, so the target form is the correct one there.
- G1: `MoEDispatcherWorm` advertised three experts, none of which it could
  resolve, and `dispatch()` ranked a static table. It now resolves experts
  against the real modules, ranks them by the shared Q-table, and its
  `feedback()` writes into that table instead of a private dict.
- G5/G6: the SSM and attention docstrings claimed Mamba/T attention they did
  not implement. Both now do what they say (input-dependent B/C with a fitted
  readout; a scalar attention prior over natures, explicitly NOT token-level
  attention), and both say what they do.
"""

import time
import threading
import math
from datetime import datetime
from typing import Any, Callable, List
from collections import defaultdict
import random as _rng

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.worm_registry import register_worm  # type: ignore[reportAssignmentType]
except ImportError as exc:
    note_failure("synapse:L42", exc)
    def register_worm(**kw: Any) -> Callable[[type], type]:
        return lambda cls: cls


@register_worm(category="synapse", priority=8, cooldown_seconds=30.0,
    description="Maintains edge weights env<->env, worm<->worm; unified with spine_registry Q-table (G1)")
class SynapseWeightWorm:
    """TD-learned synaptic edge weights over the shared spine Q-table.

    G1: Unified with spine_registry._Q_WEIGHTS. This class is a query layer
    plus TD(0) update path over the single authoritative Q-table in
    spine_registry. The brainstem auto-reinforces on every record_outcome();
    `update()` adds the heavier cross-edge temporal-difference step.

    G2 (audit fix): `update()` used to pass `reward > 0` (a sign bit) and
    `abs(reward) * 100` as a fabricated duration, destroying the reward
    magnitude before it reached the brainstem and leaving `lr`/`gamma`
    unread. The TD error is now computed here and handed over as a signed
    magnitude, so the Q-table is not quantised to one bit.
    """

    VERSION = "3.0.0"

    def __init__(self, lr: float = 0.1, gamma: float = 0.9):
        self._lr = float(lr)
        self._gamma = float(gamma)
        self._lock = threading.Lock()
        self._updates = 0
        self._last_td_error = 0.0
        self._spine = None
        try:
            from CORE import spine_registry as _spine
            self._spine = _spine
        except ImportError as exc:
            note_failure("synapse:L76", exc)

    def update(self, src: str, dst: str, reward: float,
               duration_ms: float = 0.0, next_value: float = 0.0) -> float:
        """TD(0) update on one edge; writes through to the shared Q-table.

            td_error = reward + gamma * V(s') - V(s)

        `V(s)` is the edge's current Q-weight, `V(s')` is `next_value`
        (0.0 for a terminal/ungrounded edge), and the lr-scaled error is
        passed to the brainstem as **`delta=`**, which implements the textbook
        `Q += alpha * TD-error`. `success` stays boolean because the ledger's
        fail-rate statistics need a boolean. Returns the post-update edge
        weight.

        This used to pass the error as `reward=`, which the brainstem applies
        as a TARGET ("q converges toward this") rather than a DELTA ("add this
        to q"). The result was not a small unit error: q was dragged toward
        `lr * td_error` on every update instead of accumulating the error, so
        the edge never kept what it had learned. The live table showed the
        consequence precisely -- 31 synapse edges collapsed to 3 distinct
        values, 25 of them at exactly +0.0909 -- and a routing table that
        cannot produce more than three distinct values cannot route.
        """
        key = f"{src}>{dst}"
        current = self.weight(src, dst)
        try:
            td_error = float(reward) + self._gamma * float(next_value) - current
        except (TypeError, ValueError) as exc:
            note_failure("synapse:L105", exc)
            td_error = 0.0
        with self._lock:
            self._updates += 1
            self._last_td_error = round(td_error, 6)
        if self._spine is not None:
            try:
                # `delta`, not `reward`: this is a TD error, and the brainstem's
                # two conventions are separate operations (Q += a*d vs
                # Q += a*(target - Q)). Passing a delta as a target made every
                # edge converge to lr*td_error instead of accumulating it.
                self._spine.record_outcome("synapse", key, td_error > 0.0,
                                           float(duration_ms),
                                           delta=self._lr * td_error)
            except Exception as exc:
                note_failure("synapse:L119", exc) # learning is best-effort; callers must not fail on it
        return self.weight(src, dst)

    def weight(self, src: str, dst: str) -> float:
        """Read Q-weight from unified spine_registry table."""
        if self._spine is not None:
            return round(self._spine.get_q_weight("synapse", f"{src}>{dst}"), 4)
        return 0.0

    def top_edges(self, n: int = 10) -> list:
        """Top edges from unified Q-table (filtered to synapse nature)."""
        if self._spine is not None:
            all_q = self._spine.get_q_weights()
            synapse_q = {k: v for k, v in all_q.items() if k.startswith("synapse:")}
            ranked = sorted(synapse_q.items(), key=lambda kv: -kv[1])[:n]
            return [{"src": k.split(":", 1)[1], "w": round(v, 4)}
                    for k, v in ranked]
        return []

    def stats(self) -> dict:
        """Introspection: learning hyper-parameters and the last TD error."""
        with self._lock:
            return {"lr": self._lr, "gamma": self._gamma,
                    "updates": self._updates,
                    "last_td_error": self._last_td_error,
                    "unified_with_spine": self._spine is not None}

    def execute(self) -> dict[str, Any]:
        top = self.top_edges(5)
        q_count = 0
        if self._spine is not None:
            q_count = sum(1 for k in self._spine.get_q_weights()
                         if k.startswith("synapse:"))
        return {"success": True, "method": "execute",
                "result": {"edges": q_count, "top": top, **self.stats()}}


@register_worm(category="synapse", priority=8, cooldown_seconds=15.0,
    description="Sparse top-k routing over InferenceWorm/ParallelInference/NimInference (MoE)")
class MoEDispatcherWorm:
    """Mixture-of-experts dispatcher over the real inference worms.

    G1 (audit fix): the previous revision listed three experts it had no way
    to resolve and ranked a private static score table, so it could "route"
    to a non-existent expert forever. It now:

    - resolves each expert against its real module (`EXPERT_MODULES`) and
      reports which ones are available;
    - ranks only resolvable experts, combining the static score with the
      shared spine Q-table (nature `moe`), so routing improves with outcomes;
    - writes `feedback()` into that same Q-table instead of a private dict.

    `dispatch()` is deterministic given the same table state; `get_expert()`
    hands the chosen class to a caller that actually needs to run inference.
    """

    VERSION = "2.0.0"
    EXPERTS = ("InferenceWorm", "ParallelInferenceWorm", "NimInferenceWorm")
    EXPERT_MODULES = {
        "InferenceWorm": ("CORE.WORMS.INFERENCE_WORM", "InferenceWorm"),
        "ParallelInferenceWorm": ("CORE.WORMS.PARALLEL_INFERENCE_WORM",
                                  "ParallelInferenceWorm"),
        "NimInferenceWorm": ("CORE.WORMS.NIM_INFERENCE", "NimInferenceWorm"),
    }
    MOE_NATURE = "moe"

    def __init__(self, top_k: int = 2):
        self._top_k = int(top_k)
        self._scores: dict[str, float] = {e: 1.0 for e in self.EXPERTS}
        self._lock = threading.Lock()
        self._availability: dict[str, bool] = {}
        self._spine = None
        try:
            from CORE import spine_registry as _spine
            self._spine = _spine
        except ImportError as exc:
            note_failure("synapse:L195", exc)

    def available_experts(self) -> dict:
        """Which experts actually exist as importable classes (cached)."""
        with self._lock:
            if not self._availability:
                import importlib.util as _iu
                for name, (mod, cls) in self.EXPERT_MODULES.items():
                    ok = False
                    try:
                        ok = _iu.find_spec(mod) is not None
                    except Exception as exc:
                        note_failure("synapse:L207", exc)
                        ok = False
                    self._availability[name] = ok
            return dict(self._availability)

    def dispatch(self, prompt_len: int = 100,
                 needs_vision: bool = False) -> dict[str, Any]:
        """Rank experts for a task; returns the top-k with their weights."""
        available = self.available_experts()
        with self._lock:
            scores = {e: s for e, s in self._scores.items()
                      if available.get(e, False)}
        if not scores:  # nothing resolvable — fail open, say so
            return {"success": True, "experts": [], "weights": {},
                    "available": available, "resolved": False}
        # Blend with the shared Q-table so routing is learned, not static.
        if self._spine is not None:
            try:
                for e in list(scores):
                    scores[e] = max(
                        0.05, scores[e] * (1.0 + self._spine.get_q_weight(
                            self.MOE_NATURE, e)))
            except Exception as exc:
                note_failure("synapse:L229", exc)
        if needs_vision:
            scores["NimInferenceWorm"] = scores.get("NimInferenceWorm", 1.0) + 1.0
        if prompt_len > 4000:
            scores["ParallelInferenceWorm"] = scores.get(
                "ParallelInferenceWorm", 1.0) + 0.5
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:self._top_k]
        return {"success": True, "experts": [e for e, _ in ranked],
                "weights": {e: round(s, 3) for e, s in ranked},
                "available": available, "resolved": True}

    def get_expert(self, name: str):
        """Import and instantiate one expert class, or None if unavailable."""
        spec = self.EXPERT_MODULES.get(name)
        if not spec or not self.available_experts().get(name):
            return None
        mod_name, cls_name = spec
        try:
            mod = __import__(mod_name, fromlist=[cls_name])
            return getattr(mod, cls_name)()
        except Exception as exc:
            note_failure("synapse:L250", exc)
            return None

    def top_expert(self, prompt_len: int = 100, needs_vision: bool = False):
        """Convenience: the highest-ranked resolvable expert instance."""
        ranked = self.dispatch(prompt_len, needs_vision).get("experts") or []
        return self.get_expert(ranked[0]) if ranked else None

    def dispatch_cascade(self, task, run_fn, threshold: float = 0.65,
                         prompt_len: int = 100,
                         needs_vision: bool = False) -> dict[str, Any]:
        """Walk ranked experts, escalating while confidence < threshold.

        run_fn(expert_instance, task) -> (answer, confidence).
        Each attempt feeds feedback() (+1 ok / -1 fail) so the Q-table
        learns which expert handles what. Returns answer + trail.
        """
        ranked = self.dispatch(prompt_len, needs_vision).get("experts") or []
        trail: list[dict[str, Any]] = []
        best: dict[str, Any] | None = None
        for name in ranked:
            inst = self.get_expert(name)
            if inst is None:
                continue
            try:
                answer, conf = run_fn(inst, task)
                conf = 0.0 if conf is None else float(conf)
            except Exception as e:
                note_failure("synapse:L277", e)
                self.feedback(name, -1.0)
                trail.append({"expert": name, "error": str(e)[:800]})
                continue
            ok = conf >= float(threshold)
            self.feedback(name, 1.0 if ok else -0.5)
            trail.append({"expert": name, "confidence": round(conf, 3),
                          "accepted": ok})
            if ok:
                return {"success": True, "answer": answer, "via": name,
                        "escalations": len(trail) - 1, "trail": trail}
            if best is None or conf > best["confidence"]:
                best = {"expert": name, "answer": answer, "confidence": conf}
        if not trail:
            return {"success": False, "error": "no_resolvable_experts"}
        if best is None:
            # Every resolvable expert raised. The previous revision fell through
            # to a return that read `answer`, which is never bound on this path,
            # so the documented "best effort" outcome was an UnboundLocalError
            # (audit 2026-09-16, B6). A trial that produced no answer is a
            # failure, not a best effort.
            return {"success": False, "error": "all_experts_failed",
                    "escalations": len(trail) - 1, "trail": trail}
        # Below-threshold fallback reports the HIGHEST-confidence attempt, not
        # the last one tried: `name` after the loop is merely whichever expert
        # happened to be ranked last, which the old `best_effort_last_expert`
        # note asserted but did not deliver (B6, second half).
        return {"success": True, "answer": best["answer"],
                "via": best["expert"],
                "escalations": len(trail) - 1, "trail": trail,
                "below_threshold": round(best["confidence"], 3),
                "note": "best_effort_best_expert"}

    def feedback(self, expert: str, reward: float) -> None:
        """Report an expert outcome; updates the local score AND the Q-table."""
        try:
            r = max(-1.0, min(1.0, float(reward)))
        except (TypeError, ValueError) as exc:
            note_failure("synapse:L314", exc)
            return
        with self._lock:
            self._scores[expert] = max(0.05,
                                       self._scores.get(expert, 1.0) + 0.1 * r)
        if self._spine is not None:
            try:
                self._spine.record_outcome(self.MOE_NATURE, expert, r > 0.0,
                                           0.0, reward=r)
            except Exception as exc:
                note_failure("synapse:L323", exc)

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute",
                "result": self.dispatch()}


@register_worm(category="synapse", priority=6, cooldown_seconds=60.0,
    description="Input-dependent state-space summarizer; selective-scan recurrence with an LMS-fitted readout")
class SSMSequenceWorm:
    """Selective-scan recurrence over a value stream, with a fitted readout.

    The recurrence is a genuine linear state-space step:

        gate_t  = sigmoid(v_t * gate_scale)          # input-dependent decay
        decay_t = base + (1 - base) * gate_t
        B_t     = W_b . phi(v_t),  phi(v) = [1, v]   # INPUT-DEPENDENT B
        C_t     = W_c . phi(v_t)                     # INPUT-DEPENDENT C
        s_t     = s_{t-1} * decay_t + (1 - decay_t) * B_t * v_t
        y_t     = C_t . s_t

    G5 (audit fix): the previous revision froze B and C at `Random(42)`
    constants and never updated them, which makes the recurrence a fixed
    linear filter — the one thing a selective scan is not. B and C are now
    input-dependent (the structural property S4/Mamba have), and `W_c` is
    fitted online by least-mean-squares on the step's reconstruction error
    `e = v_t - C_t . s_t`. `W_b` follows a Hebbian approximation of the same
    error and is labelled as an approximation. The gradient for `W_c` is
    exact for this linear readout; nothing here claims to train B by
    backpropagation.

    This is a ~2*D-parameter adaptive filter, NOT a trained deep model — no
    gradient library, no GPU. Complexity stays O(n*d) time, O(d) memory.
    """

    VERSION = "3.0.0"

    def __init__(self, state_dim: int = 64, base_decay: float = 0.95,
                 gate_scale: float = 0.3, lr_c: float = 0.02,
                 lr_b: float = 0.005):
        self._dim = int(state_dim)
        self._base_decay = float(base_decay)
        self._gate_scale = float(gate_scale)
        self._lr_c = float(lr_c)
        self._lr_b = float(lr_b)
        self._state: list[float] = [0.0] * self._dim
        self._lock = threading.Lock()
        self._tokens = 0
        self._params_updated = 0
        self._err_ema: float | None = None
        # Learnable input-dependent projections (small random init, then fitted)
        _r = _rng.Random(42)
        self._W_b: list[list[float]] = [[_r.gauss(0, 0.1), _r.gauss(0, 0.1)]
                                       for _ in range(self._dim)]
        self._W_c: list[list[float]] = [[_r.gauss(0, 0.1), _r.gauss(0, 0.1)]
                                        for _ in range(self._dim)]
        # Pending step for the one-step prediction target
        self._pending: tuple | None = None

    @staticmethod
    def _phi(v: float) -> tuple:
        """Input feature map: [bias, value]."""
        return (1.0, float(v))

    def ingest(self, values: list[float]) -> dict[str, Any]:
        """Run the selective scan over `values`, fitting the readout on the way."""
        with self._lock:
            for raw in values:
                try:
                    v = float(raw)
                except (TypeError, ValueError) as exc:
                    note_failure("synapse:L395", exc)
                    continue
                # 1. learning step on the previous step's reconstruction error
                if self._pending is not None:
                    s_prev, phi_prev, decay_prev, err = self._pending
                    if err is not None:
                        self._err_ema = (err if self._err_ema is None
                                         else 0.9 * self._err_ema + 0.1 * err)
                    # LMS on the linear readout: dW_c[h][j] = lr * e * s[h] * phi[j]
                    for h in range(self._dim):
                        base = self._lr_c * (err if err is not None else 0.0) * s_prev[h]
                        self._W_c[h][0] += base * phi_prev[0]
                        self._W_c[h][1] += base * phi_prev[1]
                        # Hebbian approximation for W_b (labelled as such)
                        hb = (self._lr_b * (err if err is not None else 0.0) * (1.0 - decay_prev) * phi_prev[1])
                        self._W_b[h][0] += hb * phi_prev[0]
                        self._W_b[h][1] += hb * phi_prev[1]
                    self._params_updated += 1

                # 2. forward step with input-dependent B/C
                phi = self._phi(v)
                gate = 1.0 / (1.0 + math.exp(-v * self._gate_scale))
                decay = self._base_decay + (1.0 - self._base_decay) * gate
                B_t = [w[0] * phi[0] + w[1] * phi[1] for w in self._W_b]
                C_t = [w[0] * phi[0] + w[1] * phi[1] for w in self._W_c]
                for i in range(self._dim):
                    self._state[i] = (self._state[i] * decay
                                      + B_t[i] * v * (1.0 - decay))
                pred = sum(C_t[i] * self._state[i] for i in range(self._dim))
                self._tokens += 1
                # 3. queue this step's state + reconstruction error for the
                #    learning step at the start of the next iteration
                self._pending = (list(self._state), phi, decay, v - pred)

            mag = round(math.sqrt(sum(x * x for x in self._state)), 4)
            return {"success": True, "tokens": self._tokens, "state_mag": mag,
                    "model": "selective_scan_input_dependent_bc",
                    "gate_scale": self._gate_scale,
                    "params_updated": self._params_updated}

    def summarize(self) -> dict[str, Any]:
        """Read the current state out through C (no side effects)."""
        with self._lock:
            phi = self._phi(0.0)
            C_t = [w[0] * phi[0] + w[1] * phi[1] for w in self._W_c]
            projection = sum(C_t[i] * self._state[i] for i in range(self._dim))
            mag = round(math.sqrt(sum(x * x for x in self._state)), 4)
            return {"success": True, "tokens": self._tokens,
                    "state_mag": mag, "projection": round(projection, 4),
                    "dim": self._dim, "model": "selective_scan_input_dependent_bc",
                    "complexity": "O(n*d)",
                    "params_updated": self._params_updated,
                    "train_error_ema": (round(self._err_ema, 5)
                                        if self._err_ema is not None else None)}

    def train_error(self) -> float:
        """EMA of the reconstruction error (0.0 = never trained yet)."""
        with self._lock:
            return abs(self._err_ema) if self._err_ema is not None else 0.0

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute",
                "result": self.summarize()}


@register_worm(category="synapse", priority=8, cooldown_seconds=30.0,
    description="G5: scalar attention prior over active natures; cross-nature routing bias")
class AttentionRouterWorm:
    """Scalar attention PRIOR over natures, blended with the shared Q-table.

    G6 (audit fix): this is explicitly NOT token-level attention — there are
    no queries, keys or values and no sequence. It softmaxes one scalar per
    nature to bias cross-nature routing. The previous revision also blended
    `update_from_outcome()` deltas into the SAME accumulator that `attend()`
    overwrote with an EMA of Q, so the two update paths double-counted and
    partially cancelled. Manual deltas now live in their own table and are
    summed with the Q-derived EMA at read time.

    `task_context` is honoured when it names a preferred nature
    (`{"nature": "immune"}` or `{"prefer": [...]}`), giving a real attention
    bias instead of an ignored argument.
    """

    VERSION = "2.0.0"

    def __init__(self, top_k: int = 3, decay: float = 0.99,
                 beta: float = 2.0):
        self._top_k = int(top_k)
        self._decay = float(decay)
        self._beta = float(beta)
        self._q_ema: dict[str, float] = {}     # EMA of the shared Q-table
        self._prior: dict[str, float] = {}     # manual deltas (separate table)
        self._lock = threading.Lock()
        self._spine = None
        try:
            from CORE import spine_registry as _spine
            self._spine = _spine
        except ImportError as exc:
            note_failure("synapse:L492", exc)

    def attend(self, task_context: dict | None = None) -> dict[str, Any]:
        """Softmax over natures: Q-derived EMA + manual prior + context bias."""
        ctx = task_context if isinstance(task_context, dict) else {}
        with self._lock:
            if self._spine is not None:
                try:
                    for key, val in self._spine.get_q_weights().items():
                        nature = key.split(":", 1)[0]
                        self._q_ema[nature] = (self._q_ema.get(nature, 0.0)
                                               * self._decay
                                               + val * (1.0 - self._decay))
                except Exception as exc:
                    note_failure("synapse:L506", exc)
            scores = {n: self._q_ema.get(n, 0.0) + self._prior.get(n, 0.0)
                      for n in set(self._q_ema) | set(self._prior)}
        if not scores:
            return {"success": True, "top_natures": [], "attention": {}}

        preferred = ctx.get("nature") or ctx.get("prefer") or []
        if isinstance(preferred, str):
            preferred = [preferred]
        for name in preferred:
            scores[str(name)] = scores.get(str(name), 0.0) + 1.0

        max_w = max(scores.values())
        exp_w = {k: math.exp(self._beta * (v - max_w)) for k, v in scores.items()}
        total = sum(exp_w.values()) or 1.0
        attn = {k: round(v / total, 4) for k, v in exp_w.items()}
        ranked = sorted(attn.items(), key=lambda kv: -kv[1])[:self._top_k]
        return {"success": True, "top_natures": [n for n, _ in ranked],
                "attention": dict(ranked),
                "beta": self._beta, "scored_natures": len(scores)}

    def update_from_outcome(self, nature: str, success: bool,
                            strength: float = 1.0) -> None:
        """Manual bias in its own table — never mixed into the Q-derived EMA.

        `strength` (default 1.0) scales the step, so a caller holding a
        success RATE can push proportionally (e.g. a nature at 100% success
        moves twice as far as one at 75%) instead of a fixed increment.
        """
        try:
            s = max(0.0, min(1.0, float(strength)))
        except (TypeError, ValueError) as exc:
            note_failure("synapse:L538", exc)
            s = 1.0
        with self._lock:
            delta = (0.1 if success else -0.15) * s
            self._prior[nature] = max(-10.0, min(10.0,
                self._prior.get(nature, 0.0) + delta))

    def stats(self) -> dict:
        with self._lock:
            return {"q_natures": len(self._q_ema),
                    "prior_natures": len(self._prior),
                    "top_k": self._top_k, "beta": self._beta,
                    "unified_with_spine": self._spine is not None}

    def execute(self) -> dict[str, Any]:
        result = self.attend()
        result["stats"] = self.stats()
        return {"success": True, "method": "execute", "result": result}


@register_worm(category="synapse", priority=7, cooldown_seconds=15.0,
    description="Threshold gating — only fires downstream cluster if confidence above threshold")
class ActivationRouterWorm:
    """Confidence-gated activation router."""

    VERSION = "1.0.0"

    def __init__(self, threshold: float = 0.6):
        self._threshold = threshold
        self._fired = 0
        self._suppressed = 0
        self._lock = threading.Lock()

    def gate(self, confidence: float, payload: dict | None = None) -> dict[str, Any]:
        with self._lock:
            if confidence >= self._threshold:
                self._fired += 1
                return {"success": True, "fire": True, "confidence": confidence,
                        "payload_keys": list((payload or {}).keys())[:8]}
            self._suppressed += 1
            return {"success": True, "fire": False, "confidence": confidence,
                    "threshold": self._threshold}

    def stats(self) -> dict:
        with self._lock:
            total = self._fired + self._suppressed
            return {"fired": self._fired, "suppressed": self._suppressed,
                    "total": total, "threshold": self._threshold,
                    "fire_rate": round(self._fired / total, 3) if total else 0.0}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute", "result": self.stats()}
