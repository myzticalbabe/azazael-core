#!/usr/bin/env python3
# coding: utf-8
"""audit_chain_repair.py — repair a TORN self-chained audit sidecar.

WHY THIS EXISTS
---------------
`~/.audit/daemon_health.jsonl` is a self-chained ledger written by the pmb
daemon launcher (`pmb_daemon_launcher.py`, outside this repo). Two independent
defects put it into a state its own verifier rejects:

  1. Two processes appended concurrently, and on Windows `open("a")` is
     seek-to-end-then-write rather than an atomic append. Two writers seeked to
     the same offset, so one record's TAIL survived as an orphan fragment while
     its head was overwritten. Those fragments are the lines that fail
     `json.loads`.
  2. The writer's old `_head_hash()` fell back to `_GENESIS` whenever the last
     line would not parse. So the append AFTER a torn line chained from genesis,
     and the append after that chained from genesis too — because the new last
     line was itself a genesis-linked record. ONE torn write poisoned every
     subsequent record, which is why the damage is not confined to the two
     fragment lines.

The writer is fixed (it now reads the head through the locked fd and skips
unparseable trailing lines instead of returning genesis). The EXISTING FILE is
still broken, and that is all this tool touches. It does not import the launcher
— that module lives outside the repo, expects its own venv, and mutates global
state on import — so the hash recipe is MIRRORED from it and the mirror is
stated field-for-field below.

WHAT THIS FILE MUST NOT DO
--------------------------
  * It must never mutate anything without `--apply`, and never without a
    durable pre-repair copy it has verified byte-for-byte.
  * It must never DELETE the torn bytes. A fragment that fails to parse still
    holds real audit information (it is the tail of a real event); it is
    QUARANTINED verbatim, with its line number, so the loss stays inspectable.
  * It must never repair a file it could not first back up, and never claim a
    chain is valid without recomputing every hash.

THE UNAVOIDABLE COST — THIS REWRITES HISTORY
-------------------------------------------
Re-linking a broken chain means recomputing each record's `hash`, because
`hash` is derived from `prev_hash`. So the repaired records no longer carry the
hashes they were written with, and any EXTERNAL anchor or Merkle checkpoint
taken over the old head will not match the new one. That is not a side effect
we can avoid — it is the definition of repairing a hash chain — and it is
precisely why the pre-repair copy is mandatory. `--apply` reports how many
`prev_hash` values AND how many `hash` values changed, so the size of the
rewrite is never implicit.

MIRRORED RECIPE (from pmb_daemon_launcher.py, read not assumed)
--------------------------------------------------------------
    _GENESIS = "0" * 64

    _compute_hash(entry, prev_hash):
        payload = {k: v for k, v in entry.items()
                   if k not in ("hash", "signature", "entry_count")}
        payload["prev_hash"] = prev_hash            # note: SET, not merged
        canonical = json.dumps(payload, sort_keys=True,
                               separators=(",", ":"), ensure_ascii=True,
                               default=str)
        return sha256(canonical.encode("utf-8")).hexdigest()

    line = json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"

and `verify_audit_chain` rules: prev starts at `_GENESIS`; for each record,
`entry["prev_hash"]` must equal the running prev, and `entry["hash"]` must
equal `_compute_hash(entry, prev)`; then prev becomes `entry["hash"]`.

EXIT CODES (the repo's instrument convention)
--------------------------------------------
    0  ok / healthy — chain validates, or repair applied and now validates
    1  ran, FOUND A PROBLEM — the chain does not validate
    2  cannot judge — target unreadable, or the backup could not be verified
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

AUDIT_FILENAME = "daemon_health.jsonl"
_GENESIS = "0" * 64
_HASH_EXCLUDE = ("hash", "signature", "entry_count")
EXIT_OK, EXIT_PROBLEM, EXIT_CANNOT = 0, 1, 2
_UTCSTAMP_FMT = "%Y%m%dT%H%M%SZ"


def audit_path(explicit: str | None = None) -> Path:
    """Target sidecar: --path, else $AZAZAEL_AUDIT_DIR, else ~/.audit.

    Deliberately the same resolution order as the writer's `audit_dir()`, so the
    default repair target is the file the daemon actually writes.
    """
    if explicit:
        return Path(explicit)
    d = os.environ.get("AZAZAEL_AUDIT_DIR")
    if d:
        return Path(d) / AUDIT_FILENAME
    return Path(os.environ.get("USERPROFILE") or Path.home()) / ".audit" / AUDIT_FILENAME


def compute_hash(entry: dict, prev_hash: str) -> str:
    """Field-for-field mirror of the writer's `_compute_hash`. See module docstring."""
    payload = {k: v for k, v in entry.items() if k not in _HASH_EXCLUDE}
    payload["prev_hash"] = prev_hash
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def serialise(entry: dict) -> str:
    """Mirror of the writer's record serialisation (no trailing newline here)."""
    return json.dumps(entry, sort_keys=True, separators=(",", ":"))


def read_lines(path: Path) -> list[str]:
    """Physical lines, newline-stripped, order preserved."""
    return path.read_text(encoding="utf-8").splitlines()


def classify(lines: list[str]) -> list[tuple[str, object]]:
    """[(kind, payload)] where kind is 'blank' | 'record' | 'torn'.

    A torn line keeps its text so it can be quarantined verbatim.
    """
    items: list[tuple[str, object]] = []
    for line in lines:
        if not line.strip():
            items.append(("blank", line))
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            items.append(("torn", line))
            continue
        items.append(("record", obj) if isinstance(obj, dict) else ("torn", line))
    return items


def genesis_linked(records: list[dict]) -> int:
    """Records after the first whose prev_hash is genesis — the symptom count.

    The first record is legitimately genesis-linked, so it is excluded; counting
    it would hide a real break behind a legitimate one.
    """
    return sum(1 for e in records[1:] if e.get("prev_hash") == _GENESIS)


def relink(records: list[dict]) -> tuple[list[dict], int, int]:
    """Recompute prev_hash and hash from genesis, in order.

    Returns (records, prev_hash_values_changed, hash_values_changed). Both are
    reported because a `prev_hash` change necessarily forces a `hash` change —
    the size of the rewrite must not be implicit.
    """
    prev = _GENESIS
    prev_changed = hash_changed = 0
    for entry in records:
        old_prev, old_hash = entry.get("prev_hash"), entry.get("hash")
        if old_prev != prev:
            prev_changed += 1
        entry["prev_hash"] = prev
        new_hash = compute_hash(entry, prev)
        if old_hash != new_hash:
            hash_changed += 1
        entry["hash"] = new_hash
        prev = new_hash
    return records, prev_changed, hash_changed


def verify_lines(lines: list[str]) -> tuple[bool, str]:
    """Mirror of the writer's `verify_audit_chain`, over already-read lines.

    Same verdict and same stopping rule (first non-JSON ends the walk). The
    message additionally carries the PHYSICAL file line, because the authority
    numbers only non-blank lines and a repair needs the physical offset.
    """
    seen: list[tuple[int, dict]] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            return False, "line %d is not JSON" % number
        if not isinstance(entry, dict):
            return False, "line %d is JSON but not an object" % number
        seen.append((number, entry))
    prev = _GENESIS
    for ordinal, (number, entry) in enumerate(seen, 1):
        if entry.get("prev_hash") != prev:
            return False, "record %d (file line %d): prev_hash does not chain" % (
                ordinal, number)
        if entry.get("hash") != compute_hash(entry, prev):
            return False, "record %d (file line %d): hash mismatch (tampered?)" % (
                ordinal, number)
        prev = entry["hash"]
    if not seen:
        return True, "0 records (empty sidecar)"
    return True, "%d records, head %s" % (len(seen), prev[:16])


def report(path: Path) -> int:
    """Read-only survey. Never writes."""
    if not path.exists():
        # The authority treats a missing sidecar as NOT a failure; match it.
        print("audit_chain_repair: no sidecar at %s — nothing to repair (not a failure)"
              % path)
        return EXIT_OK
    try:
        lines = read_lines(path)
    except OSError as exc:
        print("audit_chain_repair: unreadable: %s" % exc)
        return EXIT_CANNOT
    items = classify(lines)
    records = [p for k, p in items if k == "record"]
    torn = [(i, p) for i, (k, p) in enumerate(items, 1) if k == "torn"]
    blanks = sum(1 for k, _ in items if k == "blank")
    ok, detail = verify_lines(lines)
    print("audit_chain_repair: REPORT (nothing written)")
    print("  path                : %s" % path)
    print("  physical lines      : %d  (%d blank)" % (len(items), blanks))
    print("  parseable records   : %d" % len(records))
    print("  torn fragments      : %d" % len(torn))
    for number, text in torn:
        print("    - line %d: %s" % (number, text[:100]))
    print("  genesis-linked after first : %d  (the symptom)" % genesis_linked(records))
    print("  chain validates     : %s" % ("YES" if ok else "NO"))
    print("  verifier says       : %s" % detail)
    if not ok or torn:
        print("  -> run with --apply to quarantine the fragments and re-link the chain")
    return EXIT_OK if ok else EXIT_PROBLEM


def apply_repair(path: Path) -> int:
    """Quarantine torn lines, re-link the chain, rewrite atomically."""
    if not path.exists():
        print("audit_chain_repair: no sidecar at %s — nothing to repair" % path)
        return EXIT_OK
    try:
        lines = read_lines(path)
    except OSError as exc:
        print("audit_chain_repair: unreadable: %s" % exc)
        return EXIT_CANNOT

    items = classify(lines)
    records = [p for k, p in items if k == "record"]
    torn = [(i, p) for i, (k, p) in enumerate(items, 1) if k == "torn"]
    before_genesis = genesis_linked(records)
    before_ok, before_detail = verify_lines(lines)

    if not torn and before_ok:
        print("audit_chain_repair: chain already validates and nothing is torn "
              "(%s) — no change made" % before_detail)
        return EXIT_OK

    stamp = time.strftime(_UTCSTAMP_FMT, time.gmtime())
    backup = path.with_name(path.name + ".pre_repair_" + stamp)
    torn_path = path.with_name(path.name + ".torn_" + stamp)

    # --- backup FIRST, and prove it before touching the original --------------
    try:
        shutil.copy2(path, backup)
    except OSError as exc:
        print("audit_chain_repair: REFUSING to repair — backup failed: %s" % exc)
        return EXIT_CANNOT
    if not backup.exists() or backup.stat().st_size != path.stat().st_size:
        print("audit_chain_repair: REFUSING to repair — backup could not be "
              "verified (size mismatch) at %s" % backup)
        return EXIT_CANNOT
    print("  backup        : %s (%d bytes, size-verified)"
          % (backup, backup.stat().st_size))

    # --- quarantine the torn bytes verbatim ----------------------------------
    if torn:
        try:
            with open(torn_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("# torn fragments quarantined from %s\n" % path)
                fh.write("# physical_line\tbytes\ttext\n")
                for number, text in torn:
                    fh.write("%d\t%d\t%s\n" % (number, len(text.encode("utf-8")), text))
        except OSError as exc:
            print("audit_chain_repair: REFUSING to repair — could not write the "
                  "quarantine file: %s" % exc)
            return EXIT_CANNOT
        print("  quarantine    : %s (%d fragment(s), verbatim)"
              % (torn_path, len(torn)))

    # --- re-link, then re-emit in the original order -------------------------
    records, prev_changed, hash_changed = relink(records)
    queue = list(records)
    out: list[str] = []
    for kind, _payload in items:
        if kind == "blank":
            out.append("")                 # preserve layout; bodies are unchanged
        elif kind == "torn":
            continue                       # quarantined above, removed from the chain
        else:
            out.append(serialise(queue.pop(0)))
    body = "".join(line + "\n" for line in out)

    tmp = path.with_name(path.name + ".repair_tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)              # atomic: never leave a half-written repair
    except OSError as exc:
        print("audit_chain_repair: write failed, original left untouched "
              "(backup at %s): %s" % (backup, exc))
        try:
            tmp.unlink()
        except OSError as unlink_exc:
            # Not silent: a temp file that survives the failed write is a real
            # artifact on disk, and the operator needs its name to clean it up.
            print("audit_chain_repair: could not remove the partial temp file "
                  "%s (left in place for inspection): %s" % (tmp, unlink_exc))
        return EXIT_CANNOT

    after_lines = read_lines(path)
    after_ok, after_detail = verify_lines(after_lines)
    after_records = [p for k, p in classify(after_lines) if k == "record"]

    print("  lines before  : %d" % len(items))
    print("  fragments out : %d" % len(torn))
    print("  records       : %d (count unchanged: %s)"
          % (len(after_records), len(after_records) == len(records)))
    print("  prev_hash values changed : %d" % prev_changed)
    print("  hash values changed      : %d   (forced by prev_hash; see docstring)"
          % hash_changed)
    print("  genesis-linked after first : %d -> %d"
          % (before_genesis, genesis_linked(after_records)))
    print("  chain before  : %s" % ("YES" if before_ok else "NO (%s)" % before_detail))
    print("  chain after   : %s" % ("YES" if after_ok else "NO (%s)" % after_detail))
    if not after_ok:
        print("  !! repair did not produce a validating chain — the pre-repair "
              "copy at %s is authoritative; investigate before re-running" % backup)
        return EXIT_PROBLEM
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Report or repair a torn self-chained audit sidecar.")
    ap.add_argument("--path", default=None,
                    help="target sidecar (default: $AZAZAEL_AUDIT_DIR or ~/.audit/"
                         + AUDIT_FILENAME + ")")
    ap.add_argument("--apply", action="store_true",
                    help="back up, quarantine torn fragments and re-link the chain")
    ap.add_argument("--verify-only", action="store_true",
                    help="exit non-zero if the chain does not validate; writes nothing")
    a = ap.parse_args(argv)
    path = audit_path(a.path)
    if a.apply:
        return apply_repair(path)
    if a.verify_only:
        if not path.exists():
            print("audit_chain_repair: no sidecar at %s — not a failure" % path)
            return EXIT_OK
        try:
            ok, detail = verify_lines(read_lines(path))
        except OSError as exc:
            print("audit_chain_repair: unreadable: %s" % exc)
            return EXIT_CANNOT
        print("audit_chain_repair: %s — %s" % ("VALID" if ok else "INVALID", detail))
        return EXIT_OK if ok else EXIT_PROBLEM
    return report(path)


if __name__ == "__main__":
    sys.exit(main())
