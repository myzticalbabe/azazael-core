#!/usr/bin/env python3
"""WSL-side gossip export: dump this env's spine payload to the C: drop dir.

Invoked from Windows: wsl -d <distro> -- python3 /mnt/c/.../gossip_export.py
Never raises (prints {} on failure). Keeps Manson-family separation: this
script only READS local ledger and writes one JSON file.
"""
import json
import sys
from pathlib import Path

# Resolve src/ from this file's location (works from Windows and from WSL, where
# this script runs as /mnt/c/.../scripts/gossip_export.py), no hardcoded user path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Quiet the noisy loggers by NAME. Best-effort and at module scope so the smoke
# test exercises it, but guarded: this script's contract is to NEVER raise for
# its WSL caller (it prints {} on failure), so a hygiene-import failure must not
# abort the export.
try:
    from CORE.log_hygiene import quiet_noisy_loggers  # noqa: E402

    quiet_noisy_loggers()
except Exception:  # noqa: BLE001 -- hygiene is best-effort, never fatal here
    pass

DROP = "/mnt/c/Azazael/azazael/output/gossip"


def main():
    try:
        from CORE.WORMS.expansion.envmesh import env_id_v2
        from CORE import spine_registry as sp
        env = env_id_v2()
        payload = sp.export_spine_gossip()
        import os
        os.makedirs(DROP, exist_ok=True)
        with open(f"{DROP}/{env}.json", "w", encoding="utf-8") as f:
            json.dump({"env": env, "payload": payload}, f)
        print(json.dumps({"success": True, "env": env}))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)[:150]}))


if __name__ == "__main__":
    main()
