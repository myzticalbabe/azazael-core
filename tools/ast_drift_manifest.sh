#!/usr/bin/env bash
# Per-file md5 manifest of a tree, pruned exactly like tools/ast_audit.py.
# Shipped with the 2026-11-01 cross-environment audit so both numbers can only
# describe the same file set: the census and the drift manifest disagree the
# moment their prune sets differ (measured: 5,268 vs 2,404 py files on one tree).
#
#   bash tools/ast_drift_manifest.sh /root/azazael > /mnt/c/.../kali.md5
set -u
ROOT="${1:-.}"
cd "$ROOT" || exit 1
find . -type d \
  \( -name .git -o -name .hg -o -name .svn -o -name __pycache__ -o -name node_modules \
     -o -name site-packages -o -name dist-packages -o -name .mypy_cache \
     -o -name .pytest_cache -o -name .ruff_cache -o -name .tox -o -name .eggs \
     -o -name .venv -o -name venv -o -name .venvs -o -name target -o -name graphify-out \
     -o -name archived -o -name archive -o -name .kali_staging -o -name .conan \
     -o -name .cache -o -name .pams_venv -o -name build -o -name dist -o -name sdist \) -prune \
  -o -name '*.py' -type f -print0 \
  | xargs -0 -r md5sum
