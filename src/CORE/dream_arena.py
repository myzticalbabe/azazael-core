# coding: utf-8
"""dream_arena.py — dreams-to-arena compiler: kept dreams become test worlds.

Closes the loop the DreamCritic leaves open: coherent synthetic texts are
compiled into Arena Environment descriptors (severity mix from dream
affect-tags, hardness from dream complexity), admitted to the archive iff
novel, and scheduled as the next generation's exams.

Second half (SOTA row EP238): an action-conditioned transition. The world
GENERATION path above is dream-conditioned; `predict_next` adds the missing
"given this world and this action, what happens?" over the same declared
dimensions. It is opt-in -- callers choose it, and `admit_worlds` is unchanged.
"""
import hashlib
import math
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def compile_world(dream: dict) -> dict:
    """dream: {text, tags:[...], complexity(0..1)} -> Environment config."""
    text = str(dream.get("text", ""))
    tags = list(dream.get("tags") or [])
    cx = max(0.0, min(1.0, float(dream.get("complexity", 0.5))))
    toks = text.split()
    # severity mix from affect tags; hardness from complexity + length
    neg = sum(1 for t in tags if t in ("threat", "harm", "fail", "risk"))
    pos = sum(1 for t in tags if t in ("heal", "fix", "pass", "ok"))
    tot = max(1, neg + pos)
    hard = round(max(0.05, min(0.95, 0.3 + 0.5 * cx + 0.2 * (neg / tot))), 3)
    fp = hashlib.md5(text.encode("utf-8", "replace").replace(b" ", b"")).hexdigest()[:12]
    return {"severity_mix": [round(pos / tot, 3), 0.2, round(neg / tot, 3)],
            "hard_ratio": hard,
            "era_pressure": round(0.5 + 0.5 * cx, 3),
            "n_alerts": max(10, min(1000, len(toks))),
            "fingerprint": fp,
            "source": "dream"}


def admit_worlds(arena, dreams: list[dict], genome=None) -> dict:
    """Compile dreams, populate arena with novel solvable ones.

    Returns {compiled, admitted, skipped}. Never raises.
    """
    compiled, admitted, skipped = 0, 0, 0
    try:
        from CORE.WORMS.expansion.arena import Environment
    except Exception as exc:
        note_failure("dream_arena:L41", exc)
        return {"compiled": 0, "admitted": 0, "skipped": len(dreams),
                "error": "arena-unavailable"}
    for d in dreams:
        try:
            cfg = compile_world(d)
            compiled += 1
            env = Environment(cfg["severity_mix"], cfg["hard_ratio"],
                              cfg["era_pressure"], cfg["n_alerts"],
                              seed=int(cfg["fingerprint"][:8], 16))
            if genome is None:
                skipped += 1
                continue
            got = arena.populate(env, genome, n=1)
            if got:
                admitted += 1
            else:
                skipped += 1
        except Exception as exc:
            note_failure("dream_arena:L59", exc)
            skipped += 1
    return {"compiled": compiled, "admitted": admitted, "skipped": skipped}


# ------------------------------------------------- SOTA row EP238: next-state
#
# EP238 (SepsisAgent clinical world model) was re-scored onto THIS file because the
# world substrate lives here: `compile_world` builds a descriptor and `admit_worlds`
# admits it, but nothing ever answered "given this world and this action, what
# happens next?" -- `Environment.mutate` (arena.py:105) is an RNG transition, not an
# action-conditioned one. The predictor below closes that half.
#
# Deliberately NOT wired into `rehearse_dispatch`: that path folds plan gaps into
# `predicted_failures` today and stays live, so adopting this stays a caller's choice.

#: The action vocabulary. Each name moves exactly ONE dimension that `Environment`
#: declares (`severity_mix`, `hard_ratio`, `era_pressure`, `n_alerts`,
#: arena.py:71), so a transition can be audited dimension by dimension instead of
#: attributed to a model nobody can inspect.
ACTIONS = ("hold", "escalate_severity", "harden", "soften",
           "raise_pressure", "expand")

#: Clamps mirror `Environment.__init__` (arena.py:75-79), so a successor is a legal
#: world by construction and can be handed straight back to `Environment`.
_HARD_MIN, _HARD_MAX = 0.05, 0.95
_N_ALERTS_MIN = 10


def _shift_toward_last(mix: list, mag: float) -> list:
    """Move `mag` of each slot's mass one step toward the severe end.

    `compile_world` writes `[pos, 0.2, neg]` (above), so the LAST slot is the
    severe end and escalation is a shift, not a new scale.
    """
    if len(mix) < 2 or mag <= 0.0:
        return list(mix)
    out = list(mix)
    for i in range(len(out) - 2, -1, -1):
        move = out[i] * mag
        out[i] -= move
        out[i + 1] += move
    return out


def predict_next(world: dict, action: str, magnitude: float = 0.2) -> dict:
    """The world after `action`, on the dimensions `compile_world` emits.

    `world` is either a `compile_world(...)` result or an
    `Environment.config()`. `magnitude` is the action's size in the 0..1 range.

    Grounded and inspectable, not learned: each action is a declared arithmetic
    move on one dimension, and the same `(world, action, magnitude)` always yields
    the same successor. An unknown action RAISES -- returning the input as though
    the prediction had succeeded is the failure-is-indistinguishable-from-success
    defect the project's scanner exists to catch, so it is refused loudly here.
    """
    if action not in ACTIONS:
        raise ValueError("unknown action %r; known: %s"
                         % (action, ", ".join(ACTIONS)))
    mag = max(0.0, min(1.0, float(magnitude)))
    mix = [max(0.0, float(x))
           for x in (world.get("severity_mix") or [1.0, 1.0, 1.0])]
    hard = float(world.get("hard_ratio", 0.5))
    press = max(0.0, float(world.get("era_pressure", 1.0)))
    n = int(world.get("n_alerts", 100) or 100)

    if action == "escalate_severity":
        mix = _shift_toward_last(mix, mag)
    elif action == "harden":
        hard = hard + mag
    elif action == "soften":
        hard = hard - mag
    elif action == "raise_pressure":
        press = press * (1.0 + mag)
    elif action == "expand":
        n = int(round(n * (1.0 + mag)))
    # "hold" is the identity control: it exists so a caller can measure the cost of
    # acting against not acting, instead of assuming the baseline.

    total = float(sum(mix)) or 1.0
    nxt = dict(world)
    nxt.update({"severity_mix": [round(x / total, 6) for x in mix],
                "hard_ratio": round(min(_HARD_MAX, max(_HARD_MIN, hard)), 6),
                "era_pressure": round(press, 6),
                "n_alerts": int(max(_N_ALERTS_MIN, n)),
                "source": "%s+%s" % (world.get("source", "world"), action)})
    return nxt


def simulate(world: dict, actions, magnitude: float = 0.2) -> list:
    """Roll a world forward through `actions`, returning each successor.

    This is the shape a rehearsal consumes: the consequence of a SEQUENCE, not of
    one step, so a plan's later steps can be judged on the world its earlier steps
    produce.
    """
    out = []
    cur = world
    for act in actions:
        cur = predict_next(cur, act, magnitude)
        out.append(cur)
    return out


def _descriptor(world: dict) -> list:
    """The world in `Environment.descriptor()`'s space (arena.py:83-86)."""
    mix = [float(x) for x in (world.get("severity_mix") or [1.0, 1.0, 1.0])]
    return mix + [float(world.get("hard_ratio", 0.0)),
                  math.log1p(max(0.0, float(world.get("era_pressure", 0.0))))]


def world_distance(before: dict, after: dict) -> float:
    """L1 distance in the descriptor space the arena itself compares in.

    `arena.py:83-86` fixes that space as `severity_mix + [hard_ratio,
    log1p(era_pressure)]`, so this reuses it rather than inventing a metric.
    `n_alerts` is deliberately outside it (the arena's own choice), which is why
    `expand` can move a world without moving it here.
    """
    b, a = _descriptor(before), _descriptor(after)
    return float(sum(abs(x - y) for x, y in zip(b, a)))
