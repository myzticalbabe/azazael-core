#!/usr/bin/env python
# coding: utf-8
"""Troubleshoot the package inventory for 3.15t: which pinned packages have
cp315/cp315t wheels, which are pure-python (fine), which are sdist-only
(need a compiler) or missing. Writes state/package_audit_315.json.

Usage: python tools/package_audit_315.py [requirements...]
Default: requirements-3.15t.txt + requirements_315_full.txt
"""
import json
import re
import sys
import urllib.request
from pathlib import Path
from collections import Counter

HERE = Path(__file__).resolve().parent.parent
DEFAULTS = [HERE / "requirements-3.15t.txt", HERE / "requirements_315_full.txt"]
OUT = HERE / "state" / "package_audit_315.json"


def parse_reqs(path):
    reqs = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", " ", "\t")) or ";" in line and False:
            pass
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(==\s*([^;\s]+))?", line)
        if m and not line.startswith(("-", ".", "/")):
            reqs.append((m.group(1), (m.group(3) or "").strip() or None))
    return reqs


def pypi_files(name, version=None):
    url = f"https://pypi.org/pypi/{name}/{version}/json" if version \
        else f"https://pypi.org/pypi/{name}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            d = json.load(r)
        return [f["filename"] for f in d.get("urls", [])], None
    except Exception as e:
        return [], str(e)[:120]


def classify(files):
    wheels = [f for f in files if f.endswith(".whl")]
    if any("cp315t" in f for f in wheels):
        return "cp315t_wheel"
    if any("cp315" in f and "cp315t" not in f for f in wheels):
        return "cp315_wheel"
    if any("abi3" in f for f in wheels):
        return "abi3_wheel"
    if any(f.endswith("-py3-none-any.whl") or "-py2.py3-none-any.whl" in f
           for f in wheels):
        return "pure_python"
    if wheels:
        return "other_wheels_only"
    if any(f.endswith((".tar.gz", ".zip")) for f in files):
        return "sdist_only"
    return "missing_or_error"


def main():
    paths = sys.argv[1:] or [str(p) for p in DEFAULTS]
    seen, rows = set(), []
    for path in paths:
        if not Path(path).exists():
            print(f"skip (missing): {path}")
            continue
        for name, version in parse_reqs(path):
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            files, err = pypi_files(name, version)
            rows.append({"package": name, "pinned": version,
                         "verdict": classify(files) if files else ("error:" + (err or "?")),
                         "files_checked": len(files)})
            print(f"{name:28s} {version or '(latest)':14s} -> {rows[-1]['verdict']}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"packages": rows}, indent=1), encoding="utf-8")
    print(Counter(r["verdict"] for r in rows))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
