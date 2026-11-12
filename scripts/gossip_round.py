#!/usr/bin/env python3
"""Cross-env gossip round: export windows/ubuntu/kali ledgers, merge all.

Transport: C: drop dir (output/gossip/<env>.json) — the only bridge proven
to work in every direction (/mnt/g is NOT G:, \\wsl$ needs elevation).
Each env exports locally; Windows merges everything into its ledger, which
is the canonical training store (hourly spine job persists it).

Safe: exports are read-only snapshots; merge is setdefault/MAX semantics
inside merge_spine_gossip; per-distro timeouts; never raises.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from CORE.log_hygiene import quiet_noisy_loggers  # noqa: E402

quiet_noisy_loggers()

DROP = "C:/Azazael/azazael/output/gossip"
DISTROS = {"wsl2-ubuntu": "Ubuntu-26.04", "wsl2-kali": "kali-linux"}


def main():
    from CORE import spine_registry as sp
    os.makedirs(DROP, exist_ok=True)
    parts = {}
    # 1. local export
    try:
        payload = sp.export_spine_gossip()
        open(os.path.join(DROP, "windows.json"), "w", encoding="utf-8").write(
            json.dumps({"env": "windows", "payload": payload}))
        parts["windows"] = True
    except Exception as e:
        parts["windows"] = f"ERR {str(e)[:80]}"
    # 2. WSL exports (best-effort, 90s each)
    for env, distro in DISTROS.items():
        try:
            r = subprocess.run(
                ["wsl", "-d", distro, "--", "python3",
                 "/mnt/c/Azazael/azazael/scripts/gossip_export.py"],
                capture_output=True, text=True, timeout=90, encoding="utf-8", errors="replace")
            line = (r.stdout or "").strip().splitlines()
            ok = json.loads(line[-1]).get("success") if line else False
            parts[env] = bool(ok)
        except Exception as e:
            parts[env] = f"ERR {str(e)[:80]}"
    # 3. merge everything found in the drop dir
    merged, keys = 0, 0
    for fn in sorted(os.listdir(DROP)):
        if not fn.endswith(".json"):
            continue
        try:
            doc = json.load(open(os.path.join(DROP, fn), encoding="utf-8"))
            res = sp.merge_spine_gossip(doc.get("payload"))
            if res.get("success", True):
                merged += 1
        except Exception:
            continue
    try:
        view = sp.get_homeostat_view()
        keys = len(view)
        sp.save_spine_state()
    except Exception:
        pass
    print(json.dumps({"parts": parts, "merged": merged, "ledger_keys": keys}))


if __name__ == "__main__":
    main()