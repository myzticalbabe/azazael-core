"""The three canonical sentinel.py copies stay byte-identical to the LIVE file.

Provenance, measured 2026-11-03 by sha256 across C:/Azazael:

  canonical (all 237144d722b16e3b...):
    azazael/Project_FLAMING_SWORD/tools/sentinel.py      (the live, edited tree)
    publish/flaming-sword/tools/sentinel.py
    publish_proof/staged_flaming_sword/tools/sentinel.py

  NOT canonical, deliberately ungated:
    publish_clean/*  (e1bbf899... -- a different generation; its drift is named
    here so nobody mistakes the silence below for agreement)
    azazael/Project_CRADLE/tools/sentinel.py  (a different sentinel, different file)

The property patterned here is the one the render-handoff rules already bought:
"a copy you cannot read back did not land". The proof: if a sentinel edit
commits without re-running `tools/stage_publish_repos.py`, the publish and
proof copies silently AGE -- and every guided run inside publish/, which reads
the chain FROM the publish copy, then verifies a tool that no longer exists
byte-for-byte elsewhere. This test fires at exactly that point, and the fix it
names is the same one command -- re-stage the repos.

Copy paths outside the repo root are SKIPPED when absent (a fresh clone or a
different checkout shape), so this gate never blocks a host that never had the
copies; on this host, where they exist, it holds.

NAMED DRIFT (2026-11-03): the sentinel filename also belongs to copies that are
NOT the reaper and must never be silently unified with it -- an earlier
generation in publish_clean/, a different project's sentinel in Project_CRADLE,
and the pre-remedy staging snapshot in publish_repos/. Their state and reasons
live in tests/fixtures/sentinel_copy_manifest.json, and the second test here
asserts each entry is still at its recorded hash and still different from the
live file: an unmanifested change to either side (a copy drifted, or someone
"helpfully" copied the reaper over a non-reaper sentinel) now fails with the
manifest named as the thing to update in the same change.
"""
from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
AZAZAEL_ROOT = REPO.parent

LIVE = REPO / "Project_FLAMING_SWORD" / "tools" / "sentinel.py"
CANONICAL_COPIES = (
    AZAZAEL_ROOT / "publish" / "flaming-sword" / "tools" / "sentinel.py",
    AZAZAEL_ROOT / "publish_proof" / "staged_flaming_sword" / "tools" / "sentinel.py",
)


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_canonical_copies_are_byte_identical_to_the_live_file():
    assert LIVE.exists(), "the live sentinel is this repo's own tool -- never skip it"
    live_hash = _sha256(LIVE)
    for copy in CANONICAL_COPIES:
        if not copy.exists():
            pytest.skip("canonical copy absent on this host: %s" % copy)
        assert _sha256(copy) == live_hash, (
            "%s drifted from the live sentinel -- re-run "
            "tools/stage_publish_repos.py in the same change" % copy)


# --------------------------------------------------------------------------- #
# Named drift: same filename, different sentinel -- never silently unified
# --------------------------------------------------------------------------- #

MANIFEST = pathlib.Path(__file__).resolve().parent / "fixtures" / \
    "sentinel_copy_manifest.json"


def test_named_drift_copies_stay_at_their_recorded_hash_and_never_unify():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    base = AZAZAEL_ROOT                # manifest paths are AZAZAEL-root relative,
                                       # same convention as CANONICAL_COPIES above
    live_hash = _sha256(LIVE)
    assert manifest["named_drift"], "the lsit asserts nothing -- that IS the defect"
    entries = manifest["named_drift"]
    for entry in entries:
        assert entry.get("reason"), (
            "a named-drift entry without a reason is an "
            "ungated copy: %r" % entry.get("path"))
        assert entry.get("sha256"), (
            "a named-drift entry without a hash pauses "
            "nothing: %r" % entry.get("path"))
        copy = base / entry["path"]
        if not copy.exists():
            pytest.skip("named-drift copy absent on this host: %s" % entry["path"])
        seen = _sha256(copy)
        assert seen == entry["sha256"], (
            "%s changed without its manifest entry update -- update "
            "tests/fixtures/sentinel_copy_manifest.json in the same change, "
            "with the new sha and a fresh date" % entry["path"])
        assert seen != live_hash, (
            "%s now equals the live sentinel -- if this was intentional, remove "
            "it from named_drift and gate it as canonical instead" % entry["path"])
