# coding: utf-8
"""
NEURAL MESH — Category: CONSENSUS (3 worms)
Agreement across all envs (light on Standalone).

QuorumVoterWorm, LeaderElectorWorm, SplitBrainResolverWorm
"""

import time
import threading
import hashlib
from datetime import datetime
from typing import Any, Iterable, List
from collections import Counter
import os as _os

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Fabric operational state (audit 2026-09-15, finding #7): votes, elected
# leader + term and model-fork history used to die with the instance.
try:
    from CORE.WORMS.expansion.fabric_state import (
        hydrate, publish, cap_list, cap_map,
    )
except ImportError as exc:  # pragma: no cover - defensive
    note_failure("consensus:L22", exc)
    def hydrate(instance: Any, provider: str, attrs: Iterable[str]) -> int:
        return 0

    def publish(instance: Any, provider: str, attrs: Iterable[str]) -> bool:
        return False

    def cap_list(seq, limit):
        return seq

    def cap_map(d, limit):
        return d

try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("consensus:L37", exc)
    def register_worm(category: str = "utility", priority: int = 9,
                      cooldown_seconds: float = 30.0, description: str = "",
                      tags: (set[str] | None) = None) -> Any:
        return lambda cls: cls


@register_worm(category="consensus", priority=9, cooldown_seconds=30.0,
    description="Majority vote on VulnScanner/RaceDetector findings across envs")
class QuorumVoterWorm:
    """Cross-env majority voter — quorum counts DISTINCT ENVS.

    Audit 2026-09-15: votes used to live only in this instance's dict, and
    `RedQueenWorm.order_quarantine()` built a FRESH voter per call and cast
    one local vote, so `decided` (count >= quorum) was unreachable — the
    quarantine gate was dead code. The authoritative tally now lives in
    `CORE.spine_registry.cast_quarantine_vote()`, which:

      * dedups by env (an env changing its mind replaces its own vote, it
        cannot stuff the ballot),
      * persists and gossips, so a peer env's vote is visible in-process, and
      * requires `quorum` DISTINCT envs — so one env still can never
        quarantine alone (the original safety intent, preserved).

    The instance dict is kept as a local mirror for callers that read
    `tally()`, and as the fallback when the ledger is unavailable.
    """

    VERSION = "1.1.0"

    PROVIDER = "QuorumVoterWorm"
    _STATE_ATTRS = ("_votes",)
    # finding #8: the instance mirror is unbounded in a long-lived process.
    # (The authoritative ledger in spine_registry has its own caps.)
    FINDINGS_MAX = 256
    VOTES_PER_FINDING_MAX = 50

    def __init__(self, quorum: int = 2):
        self._quorum = quorum
        self._votes: dict[str, list] = {}
        self._lock = threading.Lock()
        hydrate(self, self.PROVIDER, self._STATE_ATTRS)

    def vote(self, finding_id: str, env: str, verdict: str) -> dict[str, Any]:
        with self._lock:
            box = self._votes.setdefault(finding_id, [])
            box.append({"env": env, "verdict": verdict,
                        "at": datetime.now().isoformat()})
            cap_list(box, self.VOTES_PER_FINDING_MAX)
            cap_map(self._votes, self.FINDINGS_MAX)
            counts = Counter(v["verdict"] for v in box)
            top, n = counts.most_common(1)[0]
            result = {"success": True, "finding": finding_id, "votes": len(box),
                      "top": top, "count": n, "decided": n >= self._quorum,
                      "quorum": self._quorum}

        # Authoritative cross-env tally (distinct envs, persisted, gossiped).
        shared = None
        try:
            from CORE import spine_registry as _spine
            shared = _spine.cast_quarantine_vote(
                finding_id, env, verdict, quorum=self._quorum)
        except Exception as exc:
            note_failure("consensus:L97", exc)
            shared = None
        if shared and shared.get("success"):
            result.update({
                "decided": bool(shared.get("decided")),
                "count": int(shared.get("count") or 0),
                "top": shared.get("top") or result["top"],
                "envs": shared.get("envs"),
                "env_count": shared.get("env_count"),
                "tally": shared.get("tally"),
                "source": "spine_ledger",
            })
        else:
            result["source"] = "instance"
        publish(self, self.PROVIDER, self._STATE_ATTRS)
        return result

    def tally(self, finding_id: str) -> dict:
        """Per-env verdict tally from the shared ledger (falls back to local)."""
        try:
            from CORE import spine_registry as _spine
            ledger = _spine.get_quarantine_votes(finding_id).get(str(finding_id)) or {}
            if ledger:
                counts: dict[str, int] = {}
                for rec in ledger.values():
                    counts[rec.get("verdict", "?")] = (
                        counts.get(rec.get("verdict", "?"), 0) + 1)
                return {"finding": finding_id, "votes": len(ledger),
                        "tally": counts, "envs": sorted(ledger),
                        "source": "spine_ledger"}
        except Exception as exc:
            note_failure("consensus:L127", exc) # scanner:ignore
        with self._lock:
            box = self._votes.get(finding_id, [])
            counts = dict(Counter(v["verdict"] for v in box))
            return {"finding": finding_id, "votes": len(box), "tally": counts,
                    "source": "instance"}

    def execute(self) -> dict[str, Any]:
        try:
            from CORE import spine_registry as _spine
            open_findings = len(_spine.get_quarantine_votes())
        except Exception as exc:
            note_failure("consensus:L139", exc)
            with self._lock:
                open_findings = len(self._votes)
        return {"success": True, "method": "execute",
                "result": {"open_findings": open_findings,
                           "quorum": self._quorum}}


@register_worm(category="consensus", priority=8, cooldown_seconds=30.0,
    description="Bully/Raft-lite: elects trainer (Bare) vs server (Win) vs scanner (Kali)")
class LeaderElectorWorm:
    """Lightweight leader election."""

    VERSION = "1.1.0"
    ROLES = ("trainer", "server", "scanner", "archiver")
    PROVIDER = "LeaderElectorWorm"
    # `_term` matters most here: a restart that reset it to 0 would let a
    # stale leader claim a newer term than the live one (audit finding #7).
    _STATE_ATTRS = ("_nodes", "_leader", "_term")

    def __init__(self):
        self._nodes: dict[str, dict] = {}
        self._leader: dict | None = None
        self._term = 0
        self._lock = threading.Lock()
        hydrate(self, self.PROVIDER, self._STATE_ATTRS)

    def nominate(self, env: str, role: str, priority: int = 5) -> dict[str, Any]:
        with self._lock:
            self._nodes[env] = {"role": role, "priority": priority,
                                "at": datetime.now().isoformat()}
            best = max(self._nodes.items(), key=lambda kv: kv[1]["priority"])
            if self._leader is None or best[0] != self._leader.get("env"):
                self._term += 1
            self._leader = {"env": best[0], **best[1], "term": self._term}
            publish(self, self.PROVIDER, self._STATE_ATTRS)
            return {"success": True, "leader": self._leader,
                    "nodes": len(self._nodes)}

    def leader(self) -> dict:
        with self._lock:
            return {"leader": self._leader, "term": self._term,
                    "nodes": list(self._nodes.keys())}

    def apply_placement(self, path: (str | None) = None) -> dict[str, Any]:
        """Seed nominations from task7 placement.json; remaps bare->wsl2-ubuntu
        when BarePassthroughWorm reports no bare kernel (3-env mesh).

        Default resolution order: AZAZAEL_PLACEMENT_JSON env var, then
        repo-relative state/placement.json, then the legacy G: mount path.
        """
        if path is None:
            env_path = _os.environ.get("AZAZAEL_PLACEMENT_JSON", "")
            if env_path:
                path = env_path
            else:
                here = _os.path.dirname(_os.path.abspath(__file__))
                repo = _os.path.dirname(_os.path.dirname(_os.path.dirname(here)))
                candidate = _os.path.join(repo, "state", "placement.json")
                path = candidate if _os.path.exists(candidate) else \
                    "G:/source-of-truth/azazael_models/placement.json"
        import json as _json
        try:
            from CORE.fs_probe import bounded_read_text
        except Exception as exc:  # pragma: no cover - probe helper optional
            note_failure("consensus:L205", exc)
            def bounded_read_text(path: str, timeout_s: float = 5.0, label: str = "fs-read") -> (str | None):  # type: ignore
                try:
                    return open(path, encoding="utf-8").read()
                except Exception as exc:
                    note_failure("consensus:L209", exc)
                    return None  # scanner:ignore
        # Bounded read. The default `path` lives on G:, and against an
        # unreachable mount a bare `open()` blocks inside the syscall for
        # minutes -- long enough to hang the whole fabric cycle, which is
        # exactly what happened (hang #4). None means "no answer in time",
        # and that is reported as unreadable rather than retried blindly.
        raw = None
        try:
            raw = bounded_read_text(path)
        except OSError as exc:
            # Missing/unreachable placement file is an answer ("no placement"),
            # never an exception: callers (fabric cycle, tests on machines
            # without the mount) must get a dict, not a traceback.
            # Missing/unreachable placement file is an answer ("no placement"),
            # never an exception: callers (fabric cycle, tests on machines
            # without the mount) must get a dict, not a traceback.
            note_failure("consensus:L219", exc)
            raw = None
        if raw is None:
            return {"success": False,
                    "error": "placement unreadable: probe timed out"}
        try:
            assign = _json.loads(raw)
        except Exception as exc:
            note_failure("consensus:L229", exc)
            return {"success": False, "error": f"placement unreadable: {str(exc)[:800]}"}
        remapped = {}
        try:
            from CORE.WORMS.expansion.envmesh import BarePassthroughWorm
            bare_ok = BarePassthroughWorm().probe().get("via", "").startswith("ssh:") or \
                BarePassthroughWorm().probe_local().get("is_bare_kernel", False)
        except Exception as exc:
            note_failure("consensus:L236", exc)
            bare_ok = _os.environ.get("AZAZAEL_BARE_SSH", "") != ""
        for role, env in assign.items():
            if env == "bare" and not bare_ok:
                remapped[role] = "wsl2-ubuntu"
            else:
                remapped[role] = env
        out = []
        for role, env in remapped.items():
            out.append(self.nominate(env, role, priority=8))
        return {"success": True, "placement": remapped,
                "remapped_bare": [r for r in assign if assign[r] == "bare" and remapped[r] != "bare"],
                "leader": self._leader}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute", "result": self.leader()}


@register_worm(category="consensus", priority=7, cooldown_seconds=120.0,
    description="Resolves ModelVersioner forks after partition (keeps highest reward + hash)")
class SplitBrainResolverWorm:
    """Fork resolver: highest reward wins, hash breaks ties."""

    VERSION = "1.1.0"
    PROVIDER = "SplitBrainResolverWorm"
    _STATE_ATTRS = ("_forks",)
    FORKS_MAX = 256        # finding #8
    BRANCHES_PER_MODEL_MAX = 50

    def __init__(self):
        self._forks: dict[str, list] = {}
        self._lock = threading.Lock()
        hydrate(self, self.PROVIDER, self._STATE_ATTRS)

    def report_fork(self, model: str, env: str, reward: float, version_hash: str) -> dict[str, Any]:
        with self._lock:
            box = self._forks.setdefault(model, [])
            box.append({"env": env, "reward": reward, "hash": version_hash,
                        "at": datetime.now().isoformat()})
            cap_list(box, self.BRANCHES_PER_MODEL_MAX)
            cap_map(self._forks, self.FORKS_MAX)
            publish(self, self.PROVIDER, self._STATE_ATTRS)
            return {"success": True, "model": model, "branches": len(box)}

    def resolve(self, model: str) -> dict[str, Any]:
        with self._lock:
            box = self._forks.get(model, [])
            if not box:
                return {"success": True, "model": model, "resolved": False,
                        "reason": "no_forks"}
            winner = sorted(box, key=lambda b: (-b["reward"], b["hash"]))[0]
            self._forks[model] = [winner]
            publish(self, self.PROVIDER, self._STATE_ATTRS)
            return {"success": True, "model": model, "resolved": True,
                    "winner": winner, "dropped": len(box) - 1}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"tracked_models": len(self._forks)}}
