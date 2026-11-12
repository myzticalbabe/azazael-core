# coding: utf-8
"""Freeze the Ex Machina inputs so a gate run stops depending on live writers.

WHY THIS EXISTS
---------------
The Ex Machina gates read two things the rest of the ecosystem writes
continuously:

* the memory store, which the orchestrator appends to every cycle
  (`evolution_result` rows), and
* the `state/` artifacts, which the evolution cycle rewrites every round.

A gate whose INPUTS move under it cannot be compared with itself. Measured on
2026-09-12 during one session: the morph ledger grew 69 -> 72 rows, the signal
count 12 -> 13, and the mood band changed -- so "the gate passed" was a
statement about the moment it ran, not about the code. A failure could not be
reproduced either, because re-running read different data.

`freeze()` builds a small, self-contained copy of both inputs:

* a NEW sqlite file carrying the `memories` schema and only the rows for the
  event types these gates read. The live store is ~480 MB / 131k rows and
  mostly irrelevant to this surface; the copy is a few hundred rows.
* a copy of every small `state/*.json|jsonl` artifact, with mtimes PRESERVED so
  the panel's age columns still describe the snapshot rather than the copy time.

Nothing is written to the source. `fingerprint()` hashes what was copied, so a
gate can NAME its inputs instead of describing them, and `apply_viewer()` points
the wallboard module at the frozen copies.

WALL-CLOCK IS NOT FROZEN, ON PURPOSE
------------------------------------
Rows, artifacts and file mtimes are fixed, so every count, statistic and verdict
is reproducible. Fields that are a function of `now` (the panel's "alloc 2m"
ages, the header clock) still advance, because they describe how long ago the
snapshot was taken. That is why the gates compare their CHECK LINES across runs
rather than their rendered text.

Usage
-----
    python scripts/_exmachina_freeze.py --dir C:/tmp/frozen --print
    python scripts/_exmachina_freeze.py --print          # temp dir, kept

Gate flags (see `gate_freeze_from_argv`):

    python scripts/verify_exmachina.py --freeze
    python scripts/verify_exmachina_panel.py --frozen=C:/tmp/frozen
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

# The event types the Ex Machina surface actually reads. Keep this list tied to
# the readers, not to what happens to be in the store: `verify_exmachina.py`
# reads 'evolution_result' and the wallboard reads the same series; a wider copy
# would silently start feeding the gates data they never used before.
EVENT_TYPES = ("evolution_result", "evolution_promotion")

# Artifact files worth copying: the JSON/JSONL the panel and tier read. Anything
# larger than this is a data lake, not an artifact, and is skipped so a freeze
# stays fast.
_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
_ARTIFACT_SUFFIXES = (".json", ".jsonl")

STATEFUL_VIEWER_GLOBALS = ("DB", "_EX_STATE", "_EX_MORPH", "_EX_STORE")

# Written into every frozen dir. It is what makes `--frozen=DIR` a REUSE rather
# than a re-copy: re-snapshotting from the live source on every run would put
# the gate straight back on the moving inputs it was frozen to escape.
MANIFEST = "frozen.json"


def project_root(start: str = None) -> str:
    """The repo root, from this file's location (scripts/..)."""
    here = os.path.abspath(start or os.path.dirname(os.path.abspath(__file__)))
    return os.path.abspath(os.path.join(here, ".."))


def resolve_store(store: str = None) -> str:
    """The live memory store, resolved EXACTLY as the wallboard resolves it.

    Mirrored rather than re-invented: the viewer treats `AZAZAEL_MEMORY_DIR` as
    either the database file or the directory holding `memory_store.db`, and a
    freeze that disagreed about that would silently copy nothing.
    """
    if store:
        p = store
    else:
        p = os.environ.get("AZAZAEL_MEMORY_DIR",
                           os.path.join(os.path.expanduser("~"), ".azazael"))
    if not p.endswith(".db"):
        p = os.path.join(p, "memory_store.db")
    return p


def _memories_ddl(con) -> tuple:
    """(table DDL, index DDLs) for `memories`, and the column list.

    Triggers are deliberately NOT copied: the live ones maintain `memories_fts`,
    which the gates never query, and recreating a trigger whose FTS table is
    absent would make the frozen store refuse inserts.
    """
    table = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='memories'"
    ).fetchone()
    if not table or not table[0]:
        raise RuntimeError("source store has no `memories` table")
    indexes = [r[0] for r in con.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='memories' "
        "AND sql IS NOT NULL")]
    cols = [d[1] for d in con.execute("PRAGMA table_info(memories)")]
    return table[0], indexes, cols


def freeze(dest: str = None, root: str = None, store: str = None,
           event_types=EVENT_TYPES, per_type: int = 600) -> dict:
    """Copy the gate's inputs into `dest` (a temp dir when none is given).

    Returns a dict describing the frozen set: `dir`, `store`, `state`, `files`,
    `rows`, `source`. Never modifies the source store or artifacts.
    """
    root = root or project_root()
    src_store = resolve_store(store)
    dest = dest or tempfile.mkdtemp(prefix="exmachina-frozen-")
    os.makedirs(dest, exist_ok=True)
    frozen_state = os.path.join(dest, "state")
    os.makedirs(frozen_state, exist_ok=True)
    dst_store = os.path.join(dest, "memory_store.db")

    rows = {}
    if os.path.exists(src_store):
        src = sqlite3.connect("file:%s?mode=ro" % src_store.replace(os.sep, "/"),
                              uri=True)
        try:
            table_ddl, index_ddl, cols = _memories_ddl(src)
            if os.path.exists(dst_store):
                os.remove(dst_store)
            dst = sqlite3.connect(dst_store, timeout=30)
            try:
                dst.execute(table_ddl)
                for ddl in index_ddl:
                    try:
                        dst.execute(ddl)
                    except sqlite3.Error:
                        continue          # an index we cannot recreate is not fatal
                collist = ", ".join(cols)
                placeholders = ", ".join(["?"] * len(cols))
                insert = ("INSERT INTO memories (%s) VALUES (%s)"
                          % (collist, placeholders))
                for et in event_types:
                    found = src.execute(
                        "SELECT %s FROM memories WHERE event_type=? "
                        "ORDER BY id DESC LIMIT ?" % collist,
                        (et, int(per_type))).fetchall()
                    if not found:
                        rows[et] = 0
                        continue
                    dst.executemany(insert, found)
                    rows[et] = len(found)
                dst.commit()
            finally:
                dst.close()
        finally:
            src.close()

    files = []
    live_state = os.path.join(root, "state")
    if os.path.isdir(live_state):
        for name in sorted(os.listdir(live_state)):
            if not name.endswith(_ARTIFACT_SUFFIXES):
                continue
            src_path = os.path.join(live_state, name)
            try:
                if not os.path.isfile(src_path):
                    continue
                if os.path.getsize(src_path) > _MAX_ARTIFACT_BYTES:
                    continue
            except OSError:
                continue
            # copy2 keeps the mtime, so the panel's age columns describe the
            # SNAPSHOT's freshness instead of restarting at the copy.
            shutil.copy2(src_path, os.path.join(frozen_state, name))
            files.append(name)

    # The clock is part of the INPUT for a gate: the panel's ages, header time
    # and pulse are all functions of `now`, so two runs of the same snapshot
    # three seconds apart reported 'archive 10%' then 'archive 11%'. Pinning it
    # is what turns "the check lines match" into "the output matches".
    frozen_now = time.time()
    frozen = {"dir": os.path.abspath(dest),
              "store": dst_store,
              "state": frozen_state,
              "files": files,
              "rows": rows,
              "source": src_store,
              "now": frozen_now,
              "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    frozen["fingerprint"] = fingerprint(frozen)
    try:
        with open(os.path.join(frozen["dir"], MANIFEST), "w",
                  encoding="utf-8") as fh:
            json.dump(frozen, fh, indent=2, sort_keys=True)
    except OSError:
        pass
    return frozen


def load_frozen(dest: str) -> dict:
    """Reuse a snapshot created earlier. Never reads the live source.

    Fails loudly when the directory is not a frozen set: silently falling back
    to the live store is the failure mode this whole module exists to prevent.
    """
    manifest = os.path.join(dest, MANIFEST)
    if not os.path.exists(manifest):
        raise RuntimeError(
            "%s is not a frozen input set (no %s). Run with --freeze first."
            % (dest, MANIFEST))
    with open(manifest, encoding="utf-8") as fh:
        frozen = json.load(fh)
    if not os.path.exists(frozen.get("store") or ""):
        raise RuntimeError("frozen store missing: %s" % frozen.get("store"))
    frozen["fingerprint"] = fingerprint(frozen)
    return frozen


def _sha256(path: str, limit: int = None) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return "unreadable"
    return h.hexdigest()[:16]


def fingerprint(frozen: dict) -> dict:
    """A stable name for what a gate actually read.

    Hashes the frozen store and every copied artifact, and records the row
    counts per event type, so two gate runs can be compared by INPUT rather than
    by when they happened.
    """
    files = {}
    for name in frozen.get("files") or []:
        p = os.path.join(frozen.get("state") or "", name)
        files[name] = _sha256(p)
    store_hash = ("absent" if not os.path.exists(frozen.get("store") or "")
                  else _sha256(frozen["store"]))
    return {"store": store_hash,
            "rows": dict(frozen.get("rows") or {}),
            "files": files,
            "files_hashed": len(files),
            "now": frozen.get("now")}


def format_fingerprint(fp: dict) -> str:
    rows = ", ".join("%s=%d" % (k, v) for k, v in sorted(fp["rows"].items())) or "none"
    now = fp.get("now")
    return ("frozen inputs: store %s  rows[%s]  artifacts %d  clock %s"
            % (fp["store"], rows, fp["files_hashed"],
               "live" if now is None else "pinned"))


def apply_viewer(module, frozen: dict) -> list:
    """Point a loaded wallboard module at the frozen store and state dir.

    Returns the list of globals actually repointed. Only globals the module
    really has are touched, so a renamed constant shows up as a missing entry
    instead of a silent no-op -- and the gates report which ones they set.
    """
    applied = []
    wanted = {"DB": frozen.get("store"),
              "_EX_STATE": frozen.get("state"),
              "_EX_MORPH": os.path.join(frozen.get("state") or "",
                                        "proteus_morph.jsonl"),
              "_EX_STORE": frozen.get("store")}
    for name in STATEFUL_VIEWER_GLOBALS:
        if hasattr(module, name) and wanted.get(name):
            setattr(module, name, wanted[name])
            applied.append(name)
    return applied


def apply_tier(module, frozen: dict) -> list:
    """Point a loaded tier module at the frozen store, when it reads one."""
    applied = []
    for name in ("DB", "_DB", "STORE", "MEMORY_DB"):
        if hasattr(module, name) and frozen.get("store"):
            setattr(module, name, frozen["store"])
            applied.append(name)
    return applied


def gate_freeze_from_argv(argv, root: str = None, quiet: bool = False) -> dict:
    """Handle `--freeze` / `--frozen=<dir>` for a gate script.

    Mutates `argv` by removing the flag, and returns the frozen description or
    None. The callers must invoke this BEFORE importing anything that resolves
    the store at import time (`AZAZAEL_MEMORY_DIR` is read at module level by
    both the wallboard and `verify_exmachina.py`).
    """
    dest = None
    build = False
    reuse = False
    for arg in list(argv):
        if arg == "--freeze":
            argv.remove(arg)
            build = True                  # a fresh temp dir
        elif arg == "--frozen":
            argv.remove(arg)
            reuse = True
            dest = argv.pop(0) if argv else None
        elif arg.startswith("--frozen="):
            argv.remove(arg)
            reuse = True
            dest = arg.split("=", 1)[1]
    if not (build or reuse):
        return None
    frozen = (load_frozen(dest) if (reuse and dest and not build)
              else freeze(dest=dest, root=root))
    # Both the wallboard and the organ gate read this at import/module level, so
    # the env var is the one lever that works before the import happens. The
    # pinned clock rides along with it so ages and the header stop advancing
    # between two runs of the same snapshot.
    os.environ["AZAZAEL_MEMORY_DIR"] = frozen["store"]
    os.environ["AZAZAEL_FROZEN_NOW"] = "%.3f" % frozen["now"]
    if not quiet:
        print("[freeze] %s" % format_fingerprint(frozen["fingerprint"]))
        print("[freeze] inputs copied to %s" % frozen["dir"])
    return frozen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Freeze the Ex Machina inputs (memory store + state artifacts).")
    ap.add_argument("--dir", default=None,
                    help="destination directory (default: a temp dir)")
    ap.add_argument("--store", default=None, help="override the source store")
    ap.add_argument("--events", default=",".join(EVENT_TYPES),
                    help="comma-separated event types to copy")
    ap.add_argument("--per-type", type=int, default=600,
                    help="max rows per event type")
    ap.add_argument("--print", dest="show", action="store_true",
                    help="print the fingerprint and file list")
    args = ap.parse_args(argv)

    frozen = freeze(dest=args.dir, store=args.store,
                    event_types=tuple(e.strip() for e in args.events.split(",")
                                      if e.strip()),
                    per_type=args.per_type)
    print(format_fingerprint(frozen["fingerprint"]))
    print("dir:   %s" % frozen["dir"])
    print("store: %s" % frozen["store"])
    print("state: %s" % frozen["state"])
    if args.show:
        for name, digest in sorted(frozen["fingerprint"]["files"].items()):
            print("  %-34s %s" % (name, digest))
    print("source: %s" % frozen["source"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
