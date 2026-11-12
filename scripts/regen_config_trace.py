#!/usr/bin/env python3
"""Regenerate worm_config_trace.json + WORM_CONFIG_TRACE.md from live registry.

The old files (May/June 2026) claimed 77 worms / 17 categories and a
single-file factory that no longer exists. Run after adding/removing worms:
    python azazael/scripts/regen_config_trace.py
"""
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from CORE.log_hygiene import quiet_noisy_loggers  # noqa: E402

quiet_noisy_loggers()
from CORE.worm_registry import discover_worms, WORM_REGISTRY

ROOT = str(Path(__file__).resolve().parents[1])  # own tree, never the origin clone


def main():
    discover_worms()
    cats = Counter(v.get("category", "?") for v in WORM_REGISTRY.values())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dump = {k: {"module": v.get("module"), "category": v.get("category"),
                "priority": v.get("priority"),
                "cooldown_seconds": v.get("cooldown_seconds"),
                "version": v.get("version"),
                "source": os.path.basename(v.get("source_file") or ""),
                "description": (v.get("description") or "")[:160]}
            for k, v in sorted(WORM_REGISTRY.items())}
    json.dump(dump, open(os.path.join(ROOT, "worm_config_trace.json"), "w", encoding="utf-8"), indent=1)
    L = ["# WORM CONFIG TRACE — Per-Worm Configuration Audit", "",
         f"> *Regenerated: {now} — Auto-extracted from live WORM_REGISTRY*",
         f"> *Total: {len(dump)} worms across {len(cats)} categories*", "", "---", "",
         "## Category Breakdown", "",
         "| Category | Count | Priority Range |", "|----------|-------|---------------|"]
    for c in sorted(cats):
        pris = [v.get("priority", 0) for v in WORM_REGISTRY.values()
                if v.get("category") == c]
        L.append(f"| {c} | {cats[c]} | {min(pris)}–{max(pris)} |")
    L += ["", "## Key Observations", "",
          f"1. Largest nature: security ({cats.get('security', 0)} worms).",
          "2. Highest priority: MetaCluster (p=10, cooldown 5s).",
          "3. Longest cooldowns: SpineCycleWorm/GemmaDriverWorm/BenchmarkWorm (300s+).",
          "4. v0.0.0 pre-release shims remain in KALIWORMS.py / CLAWBOT.",
          "5. `worm_config_trace.json` holds the machine-readable dump."]
    open(os.path.join(ROOT, "WORM_CONFIG_TRACE.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"trace: {len(dump)} worms / {len(cats)} categories written")


if __name__ == "__main__":
    sys.exit(main())
