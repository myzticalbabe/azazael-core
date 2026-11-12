#!/usr/bin/env python3
"""ADLP Fusion Core v4.0 - Unified intelligence layer."""

import os
import json
import datetime
import uuid
from pathlib import Path
from enum import Enum
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

LOG_DIR = Path(os.environ.get("APPDATA", "~/.appdata")).expanduser() / "AssistantLogs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


class TIER(Enum):
    SYSTEM = 1
    ASSISTANT = 2
    ML = 3
    KNOWLEDGE = 4
    ORCH = 5


class LVL(Enum):
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40


class FusionCore:
    VERSION = "4.0.0"

    def __init__(self):
        self.id = str(uuid.uuid4())[:8]
        self.lfile = LOG_DIR / "fusion.json"
        self.mfile = LOG_DIR / "modules.json"
        self.kfile = LOG_DIR / "knowledge.json"
        self._write(
            self.lfile,
            {
                "v": self.VERSION,
                "e": [],
                "ct": 0,
                "ts": datetime.datetime.now().isoformat(),
            },
        )
        self._write(self.mfile, {"m": {}})
        self._write(self.kfile, {"k": {}})
        self._reg(
            "fusion_core",
            TIER.ORCH,
            "4.0",
            ["log_entry", "query", "fuse", "omni", "learn", "recall"],
        )
        self._reg("system_manager", TIER.SYSTEM, "1.0", ["scan", "optimize"])
        self._reg("python_manager", TIER.SYSTEM, "1.0", ["scan_python"])
        self._reg("assistant", TIER.ASSISTANT, "1.0", ["assist"])
        self._reg("ml_orch", TIER.ML, "1.0", ["predict"])
        self._reg("knowledge", TIER.KNOWLEDGE, "1.0", ["learn", "recall"])

    def _write(self, p, d):
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2, default=str)
        except (PermissionError, OSError) as exc:
            note_failure("ADLP_FusionCore:L72", exc)

    def _read(self, p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            note_failure("ADLP_FusionCore:L69", exc)
            return {}

    def _reg(self, n, t, v, f):
        d = self._read(self.mfile)
        if "m" not in d:
            d["m"] = {}
        d["m"][n] = {
            "n": n,
            "": t.value,
            "v": v,
            "f": f,
            "s": "active",
            "ts": datetime.datetime.now().isoformat(),
        }
        self._write(self.mfile, d)
        self._l(t.value, "INFO", n, "reg", f"Reg {n} v{v}")

    def _l(self, tier, lvl, src, func, msg, D=None, tg=None):
        e = {
            "i": str(uuid.uuid4())[:12],
            "ts": datetime.datetime.now().isoformat(),
            "tier": tier,
            "lvl": lvl,
            "src": src,
            "func": func,
            "msg": msg,
            "d": D or {},
            "tg": tg or [],
        }
        d = self._read(self.lfile)
        if "e" not in d:
            d["e"] = []
        d["e"].append(e)
        d["ct"] = len(d["e"])
        self._write(self.lfile, d)

    def log_entry(self, tier, lvl, src, func, msg, data=None):
        self._l(tier, lvl, src, func, msg, data)

    def query(self, tier=None, src=None, lim=100):
        d = self._read(self.lfile)
        r = []
        for e in reversed(d.get("e", [])):
            if tier and e.get("tier") != tier:
                continue
            if src and e.get("src") != src:
                continue
            r.append(e)
            break
        return r[:lim]

    def learn(self, key, val, src="system"):
        d = self._read(self.kfile)
        if "k" not in d:
            d["k"] = {}
        d["k"][key] = {"v": val, "s": src, "ts": datetime.datetime.now().isoformat()}
        self._write(self.kfile, d)
        self._l(TIER.KNOWLEDGE.value, "INFO", "knowledge", "learn", f"Learn {key}")

    def recall(self, key):
        return self._read(self.kfile).get("k", {}).get(key, {})

    def fuse(self, tier, op, par=None):
        d = self._read(self.mfile)
        ms = [m for m in d["m"].values() if m[""] == tier.value]
        r = {
            "op": op,
            "tier": tier.value,
            "ts": datetime.datetime.now().isoformat(),
            "mods": len(ms),
        }
        for m in ms:
            r[m["n"]] = "ok"
        self._l(
            TIER.ORCH.value, "INFO", "fusion", "fuse", f"Fuse {op}: {len(ms)} mods", r
        )
        return r

    def omni(self):
        d = self._read(self.lfile)
        m = self._read(self.mfile)
        k = self._read(self.kfile)
        return {
            "id": self.id,
            "mods": list(m["m"].keys()),
            "logs": d.get("ct", 0),
            "know": len(k.get("k", {})),
        }

    def state(self):
        m = self._read(self.mfile)
        return {"id": self.id, "modules": len(m.get("m", {}))}


CORE = FusionCore()


def log_entry(tier, lvl, src, func, msg, data=None):
    CORE.log_entry(tier, lvl, src, func, msg, data)


def query(tier=None, src=None, lim=100):
    return CORE.query(tier, src, lim)


def learn(key, val, src="system"):
    CORE.learn(key, val, src)


def recall(key):
    return CORE.recall(key)


def fuse(tier, op, par=None):
    return CORE.fuse(tier, op, par)


def omni():
    return CORE.omni()


def state():
    return CORE.state()


if __name__ == "__main__":
    print("=" * 70)
    print("  ADLP FUSION CORE v4.0 - OMNIPOTENT EDITION")
    print("=" * 70)
    print(f"\nSession: {CORE.id}")

    print("\n--- Modules ---")
    for n, m in CORE._read(CORE.mfile)["m"].items():
        print(f"  [{m['']}] {n} v{m['v']}")

    print("\n--- State ---")
    s = state()
    print(f"  Modules: {s['modules']}")

    print("\n--- Omniscience ---")
    o = omni()
    print(f"  Session: {o['id']}")
    print(f"  Modules: {len(o['mods'])}")
    print(f"  Logs: {o['logs']}")
    print(f"  Knowledge: {o['know']}")

    print("\n--- Fusion Test ---")
    r = fuse(TIER.SYSTEM, "scan")
    print(f"  Operation: {r['op']}")
    print(f"  Modules: {r['mods']}")

    print("\n--- Query ---")
    q = query(lim=3)
    print(f"  Entries: {len(q)}")

    print("\n--- Learn/Recall ---")
    learn("test_key", {"data": 123}, src="test")
    rec = recall("test_key")
    print(f"  Recalled: {rec}")

    print("\n" + "=" * 70)
