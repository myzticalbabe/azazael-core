#!/usr/bin/env python
"""Rotate a credential in ~/.env, or report how old the rotations are.

Why this file exists: `SecretRotator.rotate_secret` had zero call sites, so the
capability was declared and never reached. Wiring it into a schedule would have
been the wrong call -- it mutates credentials, and a scheduled rotation that
breaks a live provider key is an outage nobody asked for. So the reachable
entry point is an operator command, and the part that CAN run unattended is
`status`, which reads the audit trail and reports staleness without touching a
secret. That is the honest split: measured, not assumed.

Usage:
    python tools/rotate_secret.py status [--max-age-days 90]
    python tools/rotate_secret.py rotate KEY [--dry-run] [--create]

Exit codes: 0 success / nothing stale, 1 rotation failed or keys are stale,
2 usage error. A rotation that did not verify NEVER exits 0.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from CORE.WORMS.expansion.config import SecretRotator  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(prog="rotate_secret", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    st = sub.add_parser("status", help="report rotation age from the audit trail (read-only)")
    st.add_argument("--max-age-days", type=int, default=90)

    ro = sub.add_parser("rotate", help="rotate one key in ~/.env and verify it")
    ro.add_argument("key", help="the env key to rotate (must already exist unless --create)")
    ro.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    ro.add_argument("--create", action="store_true", help="add the key if it is absent")
    ro.add_argument("--env", default="", help="override the env file (testing)")

    args = ap.parse_args()
    rot = SecretRotator()

    if args.cmd == "status":
        out = rot.rotation_status(max_age_days=args.max_age_days)
        print(json.dumps(out, indent=2))
        if out["stale"]:
            print(f"\nSTALE: {len(out['stale'])} key(s) at or past {args.max_age_days} days",
                  file=sys.stderr)
            return 1
        if not out["keys_rotated"]:
            print("\nNo successful rotation is recorded. That is an absence of evidence,"
                  " not evidence that keys are fresh.", file=sys.stderr)
        return 0

    if args.cmd == "rotate":
        if args.dry_run:
            env = Path(args.env) if args.env else Path.home() / ".env"
            present = False
            if env.exists():
                present = any(l.strip().partition("=")[0].strip() == args.key
                              and not l.strip().startswith("#")
                              for l in env.read_text(encoding="utf-8",
                                                     errors="replace").splitlines())
            print(json.dumps({"dry_run": True, "key": args.key, "env": str(env),
                              "present": present,
                              "would": ("replace existing value" if present
                                        else ("create new key" if args.create
                                              else "REFUSE: unknown key"))}, indent=2))
            return 0
        res = rot.rotate_secret(args.key, create=args.create,
                                env_path=args.env or None)
        print(json.dumps(res, indent=2))
        return 0 if res.get("success") else 1

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
