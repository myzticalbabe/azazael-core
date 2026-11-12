"""Honey flow: distilled knowledge moving 316t -> all trees -> Original Root.

Honey is what a tree learned, shorn of how long it took to learn:
  - the pinned oracle (frozen fixture + sha): replay it, don't re-run it;
  - the residual registry (per-URL flip history): where the world still moves;
  - the Root audit (manifest citing command + digest per claim): prove it all.

Preparation (search/read/replay from the manifest) substitutes for live
execution everywhere the registry says PINNED, and is FORBIDDEN as evidence
everywhere it says LIVE. The flow carries uncertainty stapled to every
conclusion -- a pin without a residual map is a lie shaped like a pin.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


def sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:  # scanner:ignore - binary read; encoding= is illegal in 'rb' mode
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def propagate(fixture_path: str, dest_dir: str, note: str = "",
              artifacts: Dict[str, str] | None = None,
              connectance: Dict | None = None) -> Dict:
    """Copy the frozen fixture to the canonical dir; write honey.json.

    artifacts: {label: path} -- every cited claim (pops, logs) recorded
    with its sha at propagate time, so the Root audit can re-verify.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    saved = dest / "fixture.json"
    shutil.copyfile(fixture_path, saved)
    cited = {}
    for label, path in (artifacts or {}).items():
        cited[label] = {"path": path, "sha": sha_file(path)}
    manifest = {
        "fixture_sha": sha_file(str(saved)),
        "frozen_from": fixture_path,
        "propagated_at": datetime.now(timezone.utc).isoformat(),
        "note": note,
        "artifacts": cited,
        "residuals": {},
        "connectance": connectance or {},
    }
    with open(dest / "honey.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def residual_report(log_paths: List[str]) -> Dict[str, Dict]:
    """Per-URL flip history across train logs -> PINNED vs LIVE.

    Reads 'answer' events (url + state outcome). A URL with zero outcome
    changes across every log is PINNED (preparation suffices); any flip
    makes it LIVE (only live runs are evidence there).
    """
    seqs: Dict[str, List[str]] = {}
    skipped = 0
    for lp in log_paths:
        with open(lp, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    skipped += 1  # malformed line: counted, never trusted
                    continue
                if e.get("event") == "answer" and "url" in e:
                    seqs.setdefault(e["url"], []).append(str(e.get("state")))
    report = {}
    for url, seq in seqs.items():
        flips = sum(1 for a, b in zip(seq, seq[1:]) if a != b)
        report[url] = {
            "n": len(seq),
            "outcomes": sorted(set(seq)),
            "flips": flips,
            "status": "LIVE" if flips else "PINNED",
        }
    # Skipped lines ride along, never silently: the caller (and the Root
    # manifest) sees exactly how much of the log was untrusted input.
    return {"urls": report, "skipped_lines": skipped}


def root_audit(manifest_path: str) -> Dict:
    """Re-verify the manifest: fixture sha + every cited artifact.

    Returns a verdict dict. PASS means the Root can be quoted; FAIL names
    the broken path. An honest FAIL is clean -- it reports itself.
    """
    base = Path(manifest_path).parent
    with open(manifest_path, encoding="utf-8") as f:
        man = json.load(f)
    legs = []
    fix = base / "fixture.json"
    if not fix.exists():
        legs.append({"name": "fixture.present", "status": "error",
                     "detail": "fixture.json missing"})
    elif sha_file(str(fix)) != man["fixture_sha"]:
        legs.append({"name": "fixture.sha", "status": "error",
                     "detail": "fixture digest mismatch: world moved"})
    else:
        legs.append({"name": "fixture.sha", "status": "ok",
                     "detail": man["fixture_sha"][:12]})
    for label, art in man.get("artifacts", {}).items():
        # Relative paths resolve against the manifest: the Root travels
        # with its evidence (state/honey/blobs/), so a fresh clone audits
        # PASS instead of failing on another machine's /tmp layout.
        p = art["path"]
        if not Path(p).is_absolute():
            p = str(base / p)
        if not Path(p).exists():
            legs.append({"name": "artifact." + label, "status": "error",
                         "detail": "missing: " + p})
        elif sha_file(p) != art["sha"]:
            legs.append({"name": "artifact." + label, "status": "error",
                         "detail": "digest mismatch: " + p})
        else:
            legs.append({"name": "artifact." + label, "status": "ok",
                         "detail": art["sha"][:12]})
    bad = [leg for leg in legs if leg["status"] != "ok"]
    return {"verdict": "FAIL" if bad else "PASS",
            "clean": True,  # honest either way; the gate refuses silence
            "legs": legs, "n": len(legs)}
