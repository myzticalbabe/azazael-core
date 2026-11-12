# coding: utf-8
"""
NEURAL MESH — Category: IMMUNE (4 worms)
Expand defense 1->5: ALL envs, Kali leads.

ImmuneSentinelWorm, QuarantineWorm, AttestationWorm, SelfHealWorm
"""

import time
import threading
import hashlib
import py_compile
from datetime import datetime
from pathlib import Path
from typing import Any, List
import json as _json
import random as _rng
import math

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("immune:L19", exc)
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


@register_worm(category="immune", priority=8, cooldown_seconds=30.0,
    description="Anomaly on SpanTracer/MetricAggregator (SYSTEMZ_ML features)")
class ImmuneSentinelWorm:
    """Metric anomaly sentinel."""

    VERSION = "1.0.0"

    def __init__(self, z_thresh: float = 3.0, window: int = 50):
        self._hist: dict[str, list] = {}
        self._alerts: list = []
        self._z = z_thresh
        self._window = window
        self._lock = threading.Lock()

    def observe(self, metric: str, value: float) -> dict[str, Any]:
        with self._lock:
            hist = self._hist.setdefault(metric, [])
            hist.append(value)
            if len(hist) > self._window:
                hist[:] = hist[-self._window:]
            alert = False
            if len(hist) >= 10:
                mean = sum(hist) / len(hist)
                var = sum((x - mean) ** 2 for x in hist) / len(hist)
                std = var ** 0.5
                if std > 0 and abs(value - mean) > self._z * std:
                    alert = True
                    self._alerts.append({"metric": metric, "value": value,
                                         "mean": round(mean, 3),
                                         "at": datetime.now().isoformat()})
            return {"success": True, "metric": metric, "alert": alert,
                    "alerts": len(self._alerts)}

    def alerts(self) -> dict:
        with self._lock:
            return {"alerts": len(self._alerts), "recent": self._alerts[-5:]}

    _EBPF_BASELINE = None  # lazy-loaded bcc_baseline.json (task: BCC training)

    @classmethod
    def _load_ebpf(cls) -> dict:
        if cls._EBPF_BASELINE is not None:
            return cls._EBPF_BASELINE
        import os as _os
        for p in ("G:/source-of-truth/azazael_models/bcc_baseline.json",
                  "C:/Azazael/azazael/output/bcc_baseline.json",
                  "/mnt/g/source-of-truth/azazael_models/bcc_baseline.json",
                  "/mnt/c/Azazael/azazael/output/bcc_baseline.json"):
            try:
                if _os.path.exists(p):
                    cls._EBPF_BASELINE = _json.load(open(p, encoding="utf-8"))
                    break
            except Exception as exc:
                note_failure("immune:L77", exc)
                continue
        else:
            cls._EBPF_BASELINE = {}
        return cls._EBPF_BASELINE

    _ADWIN = None  # per-metric ADWIN drift detectors (river, lazy)

    @classmethod
    def _adwin_for(cls, metric: str):
        """River ADWIN per metric; None when river is unavailable (e.g. WSL).
        Verified 2026-09-15: flags mean shifts ~23 samples in."""
        if cls._ADWIN is None:
            cls._ADWIN = {}
        if metric not in cls._ADWIN:
            try:
                from river.drift import ADWIN
                cls._ADWIN[metric] = ADWIN()
            except ImportError as exc:
                note_failure("immune:L95", exc)
                cls._ADWIN[metric] = False
        return cls._ADWIN[metric] or None

    def check_ebpf(self, comm: str, per_sec: float) -> dict[str, Any]:
        """Score a live per-comm syscall rate against the eBPF baseline.

        Uses baseline mean/std (no warm-up needed) AND feeds the rolling
        observe() window, so both the fitted model and online adaptation
        see every sample. ADWIN drift state rides along when available.
        """
        base = (self._load_ebpf().get("baselines") or {}).get(comm)
        r = self.observe(f"ebpf:{comm}", per_sec)
        det = self._adwin_for(f"ebpf:{comm}")
        if det is not None:
            try:
                det.update(per_sec)
                r["drift"] = bool(det.drift_detected)
            except Exception as exc:
                note_failure("immune:L113", exc)
        if base is None:
            return {**r, "src": "online-only", "comm": comm}
        mu = base["per_sec_mean"]
        # Floor sd: thin (5-sample) baselines go zero-variance and then
        # EVERYTHING alerts. 10% of mean or 0.5/s minimum keeps z honest.
        sd = max(0.5, 0.1 * abs(mu), base["per_sec_std"])
        z = abs(per_sec - mu) / sd
        return {**r, "src": "bcc_baseline.json", "comm": comm,
                "baseline_z": round(z, 2),
                "baseline_alert": z > self._z}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute", "result": self.alerts()}


@register_worm(category="immune", priority=7, cooldown_seconds=60.0,
    description="Isolates poisoned shard / malicious Kali finding")
class QuarantineWorm:
    """Quarantine isolator."""

    VERSION = "1.0.0"

    def __init__(self):
        self._quarantined: dict[str, dict] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _shared_store():
        """The process-global quarantine store, or None if it is unavailable.

        Delegation, not a copy: `QuarantineWorm` used to keep the only copy of
        the quarantine in `self._quarantined`, and `RedQueenWorm` constructed a
        FRESH instance per call, so the record was discarded with the object
        and no scheduling path could ever see it (audit 2026-09-16). The local
        dict is kept below as a mirror so this class still behaves if the
        registry cannot be imported.
        """
        try:
            from CORE import spine_registry as _sr
            return _sr
        except Exception as exc:
            note_failure("immune:L155", exc)
            return None

    def quarantine(self, item_id: str, reason: str) -> dict[str, Any]:
        with self._lock:
            self._quarantined[item_id] = {"reason": reason,
                                          "at": datetime.now().isoformat()}
            local = len(self._quarantined)
        spine = self._shared_store()
        shared = False
        count = local
        if spine is not None:
            try:
                spine.quarantine_item(item_id, reason)
                count = spine.quarantine_count()
                shared = True
            except Exception as exc:
                note_failure("immune:L171", exc)
                shared = False
        return {"success": True, "item": item_id,
                "quarantined": count, "shared": shared}

    def release(self, item_id: str) -> dict[str, Any]:
        with self._lock:
            gone = self._quarantined.pop(item_id, None) is not None
            local = len(self._quarantined)
        spine = self._shared_store()
        shared = False
        count = local
        if spine is not None:
            try:
                spine.release_item(item_id)
                count = spine.quarantine_count()
                shared = True
            except Exception as exc:
                note_failure("immune:L188", exc)
                shared = False
        return {"success": True, "item": item_id, "released": gone,
                "quarantined": count, "shared": shared}

    def is_quarantined(self, item_id: str) -> bool:
        """True when `item_id` is actively quarantined.

        The shared store is authoritative when present, because the caller
        asking may not be the instance that quarantined (that was the bug).
        """
        spine = self._shared_store()
        if spine is not None:
            try:
                return bool(spine.is_quarantined(item_id))
            except Exception as exc:
                note_failure("immune:L203", exc)
        with self._lock:
            return item_id in self._quarantined

    def active(self) -> dict[str, Any]:
        """Actively quarantined items from the shared store, else the mirror."""
        spine = self._shared_store()
        if spine is not None:
            try:
                return dict(spine.get_quarantine_items())
            except Exception as exc:
                note_failure("immune:L214", exc)
        with self._lock:
            return {k: dict(v) for k, v in self._quarantined.items()}

    def execute(self) -> dict[str, Any]:
        items = self.active()
        return {"success": True, "method": "execute",
                "result": {"quarantined": len(items),
                           "items": list(items)[:10]}}


@register_worm(category="immune", priority=6, cooldown_seconds=120.0,
    description="Per-env integrity (py_compile + hash + FT check) before quorum")
class AttestationWorm:
    """Pre-quorum integrity attestation."""

    VERSION = "1.0.0"

    def __init__(self):
        self._attestations: list = []
        self._lock = threading.Lock()

    def attest_file(self, path: str) -> dict[str, Any]:
        p = Path(path)
        if not p.exists():
            return {"success": False, "path": path, "reason": "missing"}
        try:
            digest = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        except Exception as e:
            note_failure("immune:L243", e)
            return {"success": False, "path": path, "reason": str(e)[:80]}
        compiles = None
        if p.suffix == '.py':
            try:
                py_compile.compile(str(p), doraise=True)
                compiles = True
            except Exception as exc:
                note_failure("immune:L250", exc)
                compiles = False
        try:
            from CORE.freethreading import is_free_threaded
            ft = is_free_threaded()
        except Exception as exc:
            note_failure("immune:L255", exc)
            ft = False
        rec = {"path": str(p), "sha": digest, "compiles": compiles,
               "ft": ft, "at": datetime.now().isoformat(),
               "pass": compiles is not False}
        with self._lock:
            self._attestations.append(rec)
        return {"success": True, "attestation": rec}

    def report(self) -> dict:
        with self._lock:
            ok = sum(1 for r in self._attestations if r.get("pass"))
            return {"attested": len(self._attestations), "passing": ok}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute", "result": self.report()}


@register_worm(category="immune", priority=8, cooldown_seconds=60.0,
    description="G11: Autoencoder anomaly detector — reconstruct input, flag high reconstruction error")
class AutoencoderAnomalyWorm:
    """G11: Multi-dimensional anomaly detection via reconstruction error.

    A real 8->4->8 tanh autoencoder, trained by backpropagation on its own
    observation stream.

    G13 (audit fix, 2026-09-14) — what was actually wrong with it:
      * `_W_dec` was **never updated**. Only the encoder was nudged, by a
        rule that ignored the tanh derivative and the decoder's own error
        path, so the "reconstruction" the threshold keyed off was produced
        by a matrix that had stayed at its `Random(42)` init forever.
      * Nothing was persisted, so every process — and every cluster sweep,
        which constructs the class fresh — relearned from scratch.
      * `_alerts` grew without bound.

    Both layers now receive exact gradients for the MSE loss through the
    tanh non-linearity (`dL/dz = (x_hat - x)`, back-propagated), with
    momentum, and the weights survive restarts. The threshold remains a
    dynamic percentile of the running error distribution — that part was
    sound and is unchanged.

    Still deliberately stdlib-only: 12 free parameters do not justify the
    torch import, and the audit's point was that the net must actually
    *train*, not that it must grow.
    """

    VERSION = "2.0.0"
    MODEL_PATH_DIR = ".azazael"
    MODEL_PATH_NAME = "anomaly_model.json"
    ALERTS_MAX = 200

    def __init__(self, input_dim: int = 8, hidden_dim: int = 4,
                 threshold_percentile: float = 95.0,
                 lr: float = 0.05, momentum: float = 0.9):
        self._input_dim = int(input_dim)
        self._hidden_dim = int(hidden_dim)
        self._threshold_pct = float(threshold_percentile)
        self._lock = threading.Lock()
        self._lr = float(lr)
        self._momentum = float(momentum)
        # Deterministic init: reproducible across processes (and the seeded
        # start is no longer load-bearing, since both layers now train).
        _r = _rng.Random(42)
        self._W_enc: list[list[float]] = [
            [_r.gauss(0, 0.1) for _ in range(self._input_dim)]
            for _ in range(self._hidden_dim)
        ]
        self._W_dec: list[list[float]] = [
            [_r.gauss(0, 0.1) for _ in range(self._hidden_dim)]
            for _ in range(self._input_dim)
        ]
        self._v_enc = [[0.0] * self._input_dim for _ in range(self._hidden_dim)]
        self._v_dec = [[0.0] * self._hidden_dim for _ in range(self._input_dim)]
        self._recon_errors: list[float] = []
        self._alerts: list[dict] = []
        self._observations = 0
        self._updates = 0
        self._last_grad_norm = 0.0

    # ── forward ────────────────────────────────────────────────────

    def _encode(self, x: list[float]) -> list[float]:
        return [math.tanh(sum(self._W_enc[h][i] * x[i]
                             for i in range(min(len(x), self._input_dim))))
                for h in range(self._hidden_dim)]

    def _decode(self, z: list[float]) -> list[float]:
        return [math.tanh(sum(self._W_dec[i][h] * z[h]
                             for h in range(self._hidden_dim)))
                for i in range(self._input_dim)]

    def _forward(self, x: list[float]) -> tuple[list[float], list[float], list[float]]:
        """Return (z, x_hat, pre-activation hidden) for the backward pass."""
        z = self._encode(x)
        x_hat = self._decode(z)
        return z, x_hat, list(z)

    def _train_step(self, x: list[float], x_hat: list[float],
                    z: list[float]) -> float:
        """One exact MSE gradient step through both layers (caller holds lock).

        dL/dx_hat      = (x_hat - x)
        dL/dpre_dec    = dL/dx_hat * (1 - x_hat^2)        # tanh'
        dW_dec         = dL/dpre_dec (x) z
        dL/dz          = W_dec^T . dL/dpre_dec
        dL/dpre_enc    = dL/dz * (1 - z^2)                # tanh'
        dW_enc         = dL/dpre_enc (x) x
        """
        n = self._input_dim
        h = self._hidden_dim
        dpre_dec = [(x_hat[i] - x[i]) * (1.0 - x_hat[i] * x_hat[i])
                    for i in range(n)]
        # dL/dz = W_dec^T . dpre_dec
        dz = [sum(self._W_dec[i][j] * dpre_dec[i] for i in range(n))
              for j in range(h)]
        dpre_enc = [dz[j] * (1.0 - z[j] * z[j]) for j in range(h)]

        grad_norm = 0.0
        for i in range(n):
            for j in range(h):
                g = dpre_dec[i] * z[j]
                self._v_dec[i][j] = self._momentum * self._v_dec[i][j] - self._lr * g
                self._W_dec[i][j] += self._v_dec[i][j]
                grad_norm += g * g
        for j in range(h):
            for i in range(n):
                g = dpre_enc[j] * x[i]
                self._v_enc[j][i] = self._momentum * self._v_enc[j][i] - self._lr * g
                self._W_enc[j][i] += self._v_enc[j][i]
                grad_norm += g * g
        self._updates += 1
        self._last_grad_norm = round(grad_norm ** 0.5, 6)
        return self._last_grad_norm

    def observe(self, metrics: list[float]) -> dict[str, Any]:
        """Observe a metric vector, reconstruct, learn, flag if anomalous.

        Ordering matters and is deliberate: the error is measured, the
        **threshold is computed from history accumulated BEFORE this point**,
        the alert decision is taken, and only then are the weights updated.
        The original appended the observation and then took the threshold from
        the distribution containing it, which lets a single outlier raise its
        own threshold and suppress the very alert it should raise.
        """
        x = [float(v) for v in metrics[:self._input_dim]]
        while len(x) < self._input_dim:
            x.append(0.0)
        z, x_hat, z_pre = self._forward(x)
        # Reconstruction error (RMSE over the input dimension)
        err = (sum((x[i] - x_hat[i]) ** 2
                   for i in range(self._input_dim)) / max(1, self._input_dim)) ** 0.5
        with self._lock:
            # Out-of-sample threshold: history only, this observation excluded.
            if len(self._recon_errors) >= 20:
                sorted_err = sorted(self._recon_errors)
                idx = int(len(sorted_err) * self._threshold_pct / 100.0)
                threshold = sorted_err[min(idx, len(sorted_err) - 1)]
            else:
                threshold = 999.0  # not enough data yet — never alert blindly
            alert = bool(err > threshold)
            self._observations += 1
            self._recon_errors.append(err)
            if len(self._recon_errors) > 500:
                self._recon_errors = self._recon_errors[-500:]
            if alert:
                self._alerts.append({
                    "error": round(err, 4),
                    "threshold": round(threshold, 4),
                    "at": datetime.now().isoformat(),
                    "metrics": [round(m, 3) for m in x[:4]],
                })
                # Bounded: the old list grew forever in a long-lived process.
                if len(self._alerts) > self.ALERTS_MAX:
                    del self._alerts[:len(self._alerts) - self.ALERTS_MAX]
            grad_norm = self._train_step(x, x_hat, z_pre)
        return {"success": True, "recon_error": round(err, 4),
                "threshold": None if threshold == 999.0 else round(threshold, 4),
                "alert": alert, "observations": self._observations,
                "updates": self._updates, "grad_norm": grad_norm}

    def alerts(self) -> dict:
        with self._lock:
            return {"alerts": len(self._alerts), "recent": self._alerts[-5:]}

    # ── persistence + sharing (G13) ────────────────────────────────

    @classmethod
    def model_path(cls) -> Path:
        return Path.home() / cls.MODEL_PATH_DIR / cls.MODEL_PATH_NAME

    def export_model(self) -> dict[str, Any]:
        """Serialisable snapshot of the fitted weights (for state file/gossip)."""
        with self._lock:
            return {
                "version": self.VERSION,
                "input_dim": self._input_dim,
                "hidden_dim": self._hidden_dim,
                "threshold_pct": self._threshold_pct,
                "lr": self._lr, "momentum": self._momentum,
                "W_enc": [list(r) for r in self._W_enc],
                "W_dec": [list(r) for r in self._W_dec],
                "observations": self._observations,
                "updates": self._updates,
                "mean_error": (round(sum(self._recon_errors)
                                     / len(self._recon_errors), 6)
                               if self._recon_errors else None),
            }

    def import_model(self, model: dict[str, Any]) -> bool:
        """Adopt a fitted model (from disk or a peer). Shape-checked.

        Refuses anything whose dimensions or structure do not match, rather
        than half-loading a matrix and silently corrupting the detector.
        """
        if not isinstance(model, dict):
            return False
        try:
            n, h = int(model["input_dim"]), int(model["hidden_dim"])
            if n != self._input_dim or h != self._hidden_dim:
                return False
            w_enc = [[float(v) for v in row] for row in model["W_enc"]]
            w_dec = [[float(v) for v in row] for row in model["W_dec"]]
            if (len(w_enc) != h or any(len(r) != n for r in w_enc)
                    or len(w_dec) != n or any(len(r) != h for r in w_dec)):
                return False
        except (KeyError, TypeError, ValueError) as exc:
            note_failure("immune:L483", exc)
            return False
        with self._lock:
            self._W_enc = w_enc
            self._W_dec = w_dec
            self._v_enc = [[0.0] * n for _ in range(h)]
            self._v_dec = [[0.0] * h for _ in range(n)]
            self._observations = int(model.get("observations") or 0)
            self._updates = int(model.get("updates") or 0)
        return True

    def save(self) -> dict[str, Any]:
        """Persist the fitted weights. Best-effort; never raises."""
        path = self.model_path()
        try:
            import json
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.export_model()), encoding="utf-8")
            import os as _os
            _os.replace(str(tmp), str(path))
            return {"saved": True, "path": str(path),
                    "observations": self._observations}
        except Exception as exc:
            note_failure("immune:L506", exc)
            return {"saved": False, "error": str(exc)[:120]}

    def load(self) -> dict[str, Any]:
        """Restore fitted weights from disk. Best-effort; never raises."""
        path = self.model_path()
        try:
            if not path.exists():
                return {"loaded": False, "reason": "no_model_file"}
            import json
            model = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            note_failure("immune:L517", exc)
            return {"loaded": False, "error": str(exc)[:120]}
        if not self.import_model(model):
            return {"loaded": False, "reason": "incompatible_model"}
        return {"loaded": True, "path": str(path),
                "observations": self._observations, "updates": self._updates}

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "observations": self._observations,
                "updates": self._updates,
                "alerts": len(self._alerts),
                "mean_error": (round(sum(self._recon_errors)
                                     / len(self._recon_errors), 6)
                               if self._recon_errors else None),
                "grad_norm": self._last_grad_norm,
                "params": (self._input_dim * self._hidden_dim * 2),
            }

    def execute(self) -> dict[str, Any]:
        result = self.alerts()
        result.update(self.stats())
        return {"success": True, "method": "execute", "result": result}


@register_worm(category="immune", priority=7, cooldown_seconds=90.0,
    description="Rebuilds from ColdArchivist + StateSnapshotter + WSLRecovery")
class SelfHealWorm:
    """Self-heal orchestrator."""

    VERSION = "1.0.0"

    def __init__(self):
        self._heals: list = []
        self._lock = threading.Lock()

    def heal(self, component: str, source: str = "ColdArchivist") -> dict[str, Any]:
        rec = {"component": component, "source": source,
               "at": datetime.now().isoformat(), "ok": True}
        with self._lock:
            self._heals.append(rec)
        return {"success": True, "healed": rec, "heals": len(self._heals)}

    def history(self) -> dict:
        with self._lock:
            return {"heals": len(self._heals), "recent": self._heals[-5:]}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute", "result": self.history()}
