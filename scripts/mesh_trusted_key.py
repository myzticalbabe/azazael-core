#!/usr/bin/env python3
# coding: utf-8
"""mesh_trusted_key.py — operator CLI for the gossip known-signer allowlist.

Why this exists
---------------
`spine_registry.check_gossip_trust` reserves the verdict ``verified`` for KNOWN
signers: our own key, or a key on the operator-pinned allowlist
(``~/.azazael/mesh_trusted_keys.json``, or ``AZAZAEL_GOSSIP_TRUSTED_KEYS`` as a
path or inline JSON). Restoring that allowlist (2026-09-28, see
FORENSIC_FOLLOWUPS_2026-09-28.md) left it writable only by hand-editing JSON --
and an unreadable allowlist fails CLOSED, so one typo silently refuses every
peer. This is the write path, and it validates its own output with the reader.

Subcommands
-----------
  list                  show the pinned allowlist and the effective trust state
  pin ENV KEY           bind KEY to one claimed peer env id
  pin --key KEY         add KEY as an env-agnostic identity
  unpin ENV             drop an env binding
  unpin --key KEY       drop an env-agnostic key
  verify ENV KEY        exit 0 if KEY is accepted for ENV, else 2

Writes are atomic (temp file + os.replace) and are read back afterwards: if the
file the reader would load does not yield the new pin, the previous content is
restored and the command exits 1. An unparsable existing file is refused rather
than overwritten, because the reader treats it as an empty allowlist.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_SCHEMA_VERSION = 1
_RESERVED = ("version", "keys", "envs", "updated_at")


def default_path() -> Path:
    """The same path the reader uses, so the CLI cannot drift from it."""
    try:
        from CORE import spine_registry as sr
        return Path(sr._TRUSTED_KEYS_FILE)
    except Exception:
        return Path.home() / ".azazael" / "mesh_trusted_keys.json"


def read_allowlist(path: Path) -> dict:
    """Load the file into ``{version, keys, envs, updated_at}``.

    Raises ValueError on an unparsable existing file: silently starting from
    scratch would drop pins the operator still relies on.
    """
    doc = {"version": _SCHEMA_VERSION, "keys": [], "envs": {}, "updated_at": ""}
    if not path.exists():
        return doc
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return doc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError("%s is not valid JSON (%s)" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise ValueError("%s must hold a JSON object" % path)
    doc["version"] = data.get("version", _SCHEMA_VERSION)
    keys = data.get("keys") or []
    doc["keys"] = [k for k in keys if isinstance(k, str) and k] \
        if isinstance(keys, list) else []
    # Mirror the reader: a bare `{env: key}` map is also accepted, so a legacy
    # file is not silently rewritten without the pins the operator still has.
    envs = data.get("envs") if isinstance(data.get("envs"), dict) else data
    for env, val in (envs or {}).items():
        if str(env) in _RESERVED:
            continue
        for v in (val if isinstance(val, list) else [val]):
            if isinstance(v, str) and v:
                doc["envs"][str(env)[:40]] = v
    doc["updated_at"] = str(data.get("updated_at") or "")
    return doc


def write_allowlist(path: Path, doc: dict) -> None:
    """Atomically persist the allowlist (0600 where the OS honours it)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = dict(doc)
    doc["version"] = _SCHEMA_VERSION
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=True) + "\n",
                   encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(str(tmp), str(path))


def _apply(path: Path, mutate) -> dict:
    """Read-mutate-write with a read-back check and rollback on mismatch."""
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    doc = read_allowlist(path)
    mutate(doc)
    write_allowlist(path, doc)
    # The reader is the validator: re-parse the file it would load.
    reloaded = read_allowlist(path)
    if reloaded["envs"] != doc["envs"] or sorted(reloaded["keys"]) != sorted(doc["keys"]):
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(previous, encoding="utf-8")
        raise SystemExit("write did not round-trip through the reader; rolled back")
    return doc


def _emit(args, payload: dict) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
    else:
        for line in payload.get("lines", []):
            print(line)


def cmd_list(args) -> int:
    path = Path(args.file) if args.file else default_path()
    doc = read_allowlist(path)
    lines = ["allowlist: %s" % path,
             "version: %s   updated_at: %s" % (doc["version"],
                                               doc["updated_at"] or "-"),
             "env-bound pins: %d" % len(doc["envs"])]
    for env, key in sorted(doc["envs"].items()):
        lines.append("  %-20s %s..." % (env, key[:16]))
    lines.append("env-agnostic keys: %d" % len(doc["keys"]))
    for key in doc["keys"]:
        lines.append("  %s..." % key[:16])
    _emit(args, {"path": str(path), "envs": doc["envs"], "keys": doc["keys"],
                 "updated_at": doc["updated_at"], "lines": lines})
    return 0


def cmd_pin(args) -> int:
    path = Path(args.file) if args.file else default_path()
    if args.key_opt and args.env:
        print("use ENV KEY or --key KEY, not both", file=sys.stderr)
        return 1
    if args.key_opt:
        bare = args.key_opt

        def _add(d):
            if bare not in d["keys"]:
                d["keys"].append(bare)

        doc = _apply(path, _add)
        what = "env-agnostic key"
    elif args.env and args.key:
        env = args.env[:40]
        doc = _apply(path, lambda d: d["envs"].__setitem__(env, args.key))
        what = "env %s" % env
    else:
        print("pin needs ENV KEY or --key KEY", file=sys.stderr)
        return 1
    print("pinned %s -> %s" % (what, path))
    print("now: %d env pin(s), %d bare key(s)"
          % (len(doc["envs"]), len(doc["keys"])))
    return 0


def cmd_unpin(args) -> int:
    path = Path(args.file) if args.file else default_path()
    if args.key_opt:
        def _drop(d):
            d["keys"] = [k for k in d["keys"] if k != args.key_opt]
        doc = _apply(path, _drop)
        what = "key %s..." % args.key_opt[:16]
    elif args.env:
        doc = _apply(path, lambda d: d["envs"].pop(args.env[:40], None))
        what = "env %s" % args.env[:40]
    else:
        print("unpin needs ENV or --key KEY", file=sys.stderr)
        return 1
    print("unpinned %s from %s" % (what, path))
    print("now: %d env pin(s), %d bare key(s)"
          % (len(doc["envs"]), len(doc["keys"])))
    return 0


def cmd_verify(args) -> int:
    key = args.key_opt or args.key
    if not args.env or not key:
        print("verify needs ENV KEY", file=sys.stderr)
        return 1
    from CORE import spine_registry as sr
    saved = sr._TRUSTED_KEYS_FILE
    if args.file:
        sr._TRUSTED_KEYS_FILE = Path(args.file)
    try:
        # The reader's own rule, so this verdict cannot drift from the gate.
        ok = bool(sr._signer_is_known(args.env, key))
    finally:
        sr._TRUSTED_KEYS_FILE = saved
    print("%s %s for env %s" % (key[:16] + "...",
                                "is accepted" if ok else "is NOT accepted",
                                args.env))
    return 0 if ok else 2


def _common():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--file", default=None,
                   help="allowlist path (default: ~/.azazael/mesh_trusted_keys.json)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    return p


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    ap = argparse.ArgumentParser(description="Gossip known-signer allowlist CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", parents=[common], help="show the pinned allowlist")

    pin = sub.add_parser("pin", parents=[common],
                         help="pin a peer key (ENV KEY, or --key KEY)")
    pin.add_argument("env", nargs="?", default=None)
    pin.add_argument("key", nargs="?", default=None)
    pin.add_argument("--key", dest="key_opt", default=None)

    unpin = sub.add_parser("unpin", parents=[common],
                           help="drop a pin (ENV, or --key KEY)")
    unpin.add_argument("env", nargs="?", default=None)
    unpin.add_argument("--key", dest="key_opt", default=None)

    verify = sub.add_parser("verify", parents=[common],
                            help="exit 0 when ENV accepts KEY")
    verify.add_argument("env", nargs="?", default=None)
    verify.add_argument("key", nargs="?", default=None)
    verify.add_argument("--key", dest="key_opt", default=None)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return {"list": cmd_list, "pin": cmd_pin,
                "unpin": cmd_unpin, "verify": cmd_verify}[args.cmd](args)
    except ValueError as exc:
        print("refusing to write: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
