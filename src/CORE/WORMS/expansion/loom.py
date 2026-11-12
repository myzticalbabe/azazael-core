# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: DIVERSITY (Loom)

Loom removes the one-scalar diversity problem. The QD archive today is scored by
*coverage* — how many of the 16 grid cells hold an elite. Coverage cannot see the
failure that actually happened on 2026-09-11: 42 of 50 cycles produced flat
trajectories and ended in the same corner of the box. Those cells were occupied;
the archive had simply collapsed into a single blob with the illusion of spread.

Loom measures the SHAPE of the archive rather than its count. The intended tool
is persistent homology over the descriptor point cloud; when a TDA dependency is
unavailable (no TDA package is installed on this machine) Loom falls back to the
cheap spectral proxy the plan specifies. numpy itself imports under the 3.15t
production runner (verified 2026-10-19) and the proxy needs only stdlib:

    intrinsic dimension  = participation ratio of covariance eigenvalues
                           ID = (sum L)^2 / sum L^2

    eigenvalue decay     = top-k mass / total mass

`ID` is a direct measure of collapse: a blob collapses toward ID -> 1 no matter
how many points it contains, whereas a genuinely spread archive has ID several
times 1. When ID falls below a homeostatic setpoint, novelty pressure ENGAGES —
a drive, not a fixed coefficient. That distinction matters: a fixed novelty
weight is swept by the search (the winner is always the weight that looks best
on the current pool); a setpoint is a control loop that responds to the archive's
measured state.

Everything here is stdlib-only by design, so it runs identically under both
interpreters. A numpy fast path is used when numpy is importable (GIL
interpreter only).
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = [
    "intrinsic_dimension",
    "participation_ratio",
    "jacobi_eigenvalues",
    "spectral_summary",
    "NoveltyDrive",
]

try:  # Optional: numpy ships a 3.15 build, but a stripped host may lack it.
    import numpy as _np  # type: ignore
except Exception as exc:  # pragma: no cover - stdlib-only fallback below
    note_failure("loom:L51", exc)
    _np = None


# --------------------------------------------------------------------------
# Spectral core (stdlib-only)
# --------------------------------------------------------------------------

def jacobi_eigenvalues(a: Sequence[Sequence[float]], iters: int = 60,
                       tol: float = 1e-10) -> List[float]:
    """Eigenvalues of a small symmetric matrix, in descending order.

    Classic cyclic Jacobi rotations — O(n^3) per sweep but n here is the
    descriptor width (single digits to low tens), so it is effectively free and
    avoids a linear-algebra dependency the free-threaded build cannot load.
    """
    n = len(a)
    if n == 0:
        return []
    if n == 1:
        return [float(a[0][0])]
    m = [[float(a[i][j]) for j in range(n)] for i in range(n)]
    # Symmetrise defensively: callers may hand us a nearly-symmetric product.
    for i in range(n):
        for j in range(i + 1, n):
            v = 0.5 * (m[i][j] + m[j][i])
            m[i][j] = m[j][i] = v

    for _ in range(max(1, iters)):
        off = math.sqrt(sum(m[i][j] ** 2 for i in range(n) for j in range(i + 1, n)))
        if off <= tol:
            break
        for p in range(n - 1):
            for q in range(p + 1, n):
                if abs(m[p][q]) <= tol:
                    continue
                theta = 0.5 * math.atan2(2.0 * m[p][q], m[q][q] - m[p][p] + 1e-300)
                c, s = math.cos(theta), math.sin(theta)
                for k in range(n):
                    mkp, mkq = m[k][p], m[k][q]
                    m[k][p] = c * mkp - s * mkq
                    m[k][q] = s * mkp + c * mkq
                for k in range(n):
                    mpk, mqk = m[p][k], m[q][k]
                    m[p][k] = c * mpk - s * mqk
                    m[q][k] = s * mpk + c * mqk
    return sorted((m[i][i] for i in range(n)), reverse=True)


def participation_ratio(eigs: Sequence[float]) -> float:
    """ID = (sum L)^2 / sum L^2 — the effective number of populated axes."""
    vals = [max(0.0, float(x)) for x in eigs]
    s1 = sum(vals)
    s2 = sum(x * x for x in vals)
    if s1 <= 0.0 or s2 <= 0.0:
        return 0.0
    return (s1 * s1) / s2


def intrinsic_dimension(points: Sequence[Sequence[float]],
                        already_symmetric: bool = False) -> float:
    """Participation ratio of the descriptor covariance spectrum."""
    if not points or len(points) < 2:
        return 0.0
    if _np is not None:
        try:
            x = _np.asarray(points, dtype=float)
            x = x - x.mean(axis=0, keepdims=True)
            if x.shape[0] <= x.shape[1]:
                cov = (x.T @ x) / float(x.shape[0] - 1)
            else:
                cov = (x.T @ x) / float(x.shape[0] - 1)
            eigs = _np.linalg.eigvalsh(cov)[::-1]
            return float(participation_ratio([float(v) for v in eigs]))
        except Exception as exc:
            note_failure("loom:L125", exc)
    return participation_ratio(jacobi_eigenvalues(_covariance(points)))


def _covariance(points: Sequence[Sequence[float]]) -> List[List[float]]:
    n = len(points)
    d = len(points[0])
    mean = [sum(p[i] for p in points) / n for i in range(d)]
    cov = [[0.0] * d for _ in range(d)]
    denom = float(max(1, n - 1))
    for p in points:
        row = [p[i] - mean[i] for i in range(d)]
        for i in range(d):
            ri = row[i]
            if ri == 0.0:
                continue
            ci = cov[i]
            for j in range(i, d):
                ci[j] += ri * row[j]
    for i in range(d):
        for j in range(i, d):
            v = cov[i][j] / denom
            cov[i][j] = v
            cov[j][i] = v
    return cov


def spectral_summary(points: Sequence[Sequence[float]], top_k: int = 3) -> Dict[str, Any]:
    """Full shape summary: ID, decay, top mass, and the eigenvalues themselves."""
    if not points or len(points) < 2:
        return {"id": 0.0, "n": len(points), "eigenvalues": [], "decay": 0.0,
                "top_mass": 0.0, "anisotropy": 0.0}

    # Must be bound BEFORE the numpy branch. Under the free-threaded
    # interpreter `_np is None`, the branch below never executes, and the
    # `if not eigs` fallback would then read an unbound local.
    eigs: List[float] = []
    used_numpy = False
    if _np is not None:
        try:
            x = _np.asarray(points, dtype=float)
            x = x - x.mean(axis=0, keepdims=True)
            cov = (x.T @ x) / float(max(1, x.shape[0] - 1))
            eigs = [float(v) for v in _np.linalg.eigvalsh(cov)[::-1]]
            used_numpy = True
        except Exception as exc:
            note_failure("loom:L171", exc)
            eigs = []
    if not eigs:
        eigs = jacobi_eigenvalues(_covariance(points))

    pos = [max(0.0, v) for v in eigs]
    total = sum(pos) or 1.0
    top = pos[:max(1, top_k)]
    k = max(1, top_k)
    # eigenvalue decay: successive ratios of the largest eigenvalues
    if len(pos) >= 2 and pos[0] > 0:
        decay = sum(1.0 for i in range(1, k) if i < len(pos) and pos[i] < pos[i - 1]) / float(k - 1 or 1)
    else:
        decay = 0.0
    return {
        "id": participation_ratio(pos),
        "n": len(points),
        "eigenvalues": pos,
        "decay": decay,
        "top_mass": sum(top) / total,
        "anisotropy": (pos[0] / pos[1]) if len(pos) > 1 and pos[1] > 0 else float("inf"),
        "numpy": used_numpy,
    }


# --------------------------------------------------------------------------
# The drive
# --------------------------------------------------------------------------

class NoveltyDrive:
    """A homeostatic control loop over archive shape.

    `pressure` is the output: 0 when the archive is healthy, rising toward 1 as
    intrinsic dimension collapses below the setpoint. Callers scale the novelty
    term of their offspring selection by it. Because the setpoint is stated in
    intrinsic-dimension units, pressure is comparable across cycles even as the
    descriptor width changes — which a fixed novelty coefficient is not.

    Also tracks the longest run of collapsed readings. A sustained collapse is
    the signature of E3 (elite freeze) and is reported explicitly rather than
    left for a human to notice in the data.
    """

    def __init__(self, setpoint: float = 3.0, gain: float = 1.5,
                 ema_alpha: float = 0.3, collapse_floor: float = 1.5,
                 path: Optional[str] = None):
        self.setpoint = float(setpoint)
        self.gain = float(gain)
        self.ema_alpha = float(min(1.0, max(0.0, ema_alpha)))
        self.collapse_floor = float(collapse_floor)
        self.path = path
        self.ema_id: Optional[float] = None
        self.pressure = 0.0
        self.collapse_run = 0
        self.history: List[dict] = []

    # -- measurement ------------------------------------------------------
    def update(self, points: Sequence[Sequence[float]]) -> Dict[str, Any]:
        summary = spectral_summary(points)
        id_val = float(summary["id"])

        if self.ema_id is None:
            self.ema_id = id_val
        else:
            a = self.ema_alpha
            self.ema_id = (1.0 - a) * self.ema_id + a * id_val

        deficit = max(0.0, self.setpoint - self.ema_id) / max(1e-9, self.setpoint)
        self.pressure = max(0.0, min(1.0, deficit * self.gain))

        if id_val < self.collapse_floor:
            self.collapse_run += 1
        else:
            self.collapse_run = 0

        row = {
            "id": id_val,
            "ema_id": self.ema_id,
            "pressure": self.pressure,
            "collapse_run": self.collapse_run,
            "n": summary["n"],
            "decay": summary["decay"],
            "top_mass": summary["top_mass"],
        }
        self.history.append(row)
        if self.path:
            self._persist()
        return row

    # -- consumption ------------------------------------------------------
    def novelty_weight(self, base: float = 0.0, ceiling: float = 0.6) -> float:
        """Weight to add to a novelty term — capped so the drive cannot swamp fitness."""
        return float(min(ceiling, base + self.pressure * ceiling))

    def should_restart(self, run_threshold: int = 3) -> bool:
        """True when collapse has persisted: the outer loop should mutate the
        SEARCH SPACE (Morph) rather than keep sampling a collapsed box."""
        return self.collapse_run >= int(run_threshold)

    def diagnostic(self) -> str:
        if not self.history:
            return "no readings"
        last = self.history[-1]
        if self.collapse_run >= 3:
            state = "COLLAPSED"
        elif self.pressure > 0.4:
            state = "narrowing"
        else:
            state = "healthy"
        return ("id=%.2f ema=%.2f pressure=%.2f %s (run=%d, n=%d)"
                % (last["id"], last["ema_id"], self.pressure, state,
                   last["collapse_run"], last["n"]))

    # -- persistence -------------------------------------------------------
    def _persist(self) -> None:
        path = self.path
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"setpoint": self.setpoint, "ema_id": self.ema_id,
                           "pressure": self.pressure,
                           "collapse_run": self.collapse_run,
                           "history": self.history[-128:]}, fh, indent=1)
        except Exception as exc:
            note_failure("loom:L292", exc)

    def load(self) -> None:
        if not self.path:
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            self.ema_id = d.get("ema_id")
            self.pressure = float(d.get("pressure", 0.0))
            self.collapse_run = int(d.get("collapse_run", 0))
            self.history = list(d.get("history", []))
        except Exception as exc:
            note_failure("loom:L305", exc)


def descriptors_from_archive(cells: Iterable[dict]) -> List[List[float]]:
    """Adapt QD archive cells into the descriptor point cloud Loom consumes.

    Accepts the archive's own shape (`descriptor` list, or `elite.cell`) so no
    new recording is needed.
    """
    out: List[List[float]] = []
    for c in cells:
        if not isinstance(c, dict):
            continue
        d = c.get("descriptor") or c.get("cell")
        if isinstance(d, (list, tuple)) and d:
            try:
                out.append([float(x) for x in d])
            except (TypeError, ValueError) as exc:
                note_failure("loom:L323", exc)
                continue
    return out
