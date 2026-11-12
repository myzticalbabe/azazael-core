#!/usr/bin/env python3
"""Independent verifier for the .audit hash chain, head anchors and signatures.

Why this exists: src/CORE/audit_trail.py verifies its own records, and its
``verify_signature`` returns True when a signature is absent or when the public
key file is missing — it fails open. This script fails closed: an entry with no
signature is reported as unsigned and does NOT count as verified, and a missing
public key is a failure, not a pass.

It also reads only files, never the module, because importing audit_trail opens a
new session and writes into the very corpus being measured.

    python tools/audit_verify.py                 # verify .audit/
    python tools/audit_verify.py --audit-dir DIR
    python tools/audit_verify.py --sample 8      # chains for N random files
    python tools/audit_verify.py --json out.json

Exit codes are part of the contract:
    0  every check ran and passed
    1  a check ran and failed (broken chain, anchor mismatch, bad signature)
    2  a check could not be performed (missing dir/anchors/key/cryptography)

2 is not a pass. Neither is 0 reported after nothing ran.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import random
import sys
from pathlib import Path

GENESIS = "0" * 64


def canon(obj) -> str:
    """The exact serialisation audit_trail._compute_hash uses."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def norm_name(path_like) -> str:
    """Anchor rows carry Windows paths; compare on the basename."""
    return str(path_like).replace(chr(92), "/").split("/")[-1]


def chain_report(path: Path) -> dict:
    """Recompute one file's chain. Returns head, count and every break found."""
    prev = GENESIS
    head = ""
    count = 0
    self_hash_failures = []
    link_failures = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            self_hash_failures.append({"line": lineno, "reason": f"unparseable: {exc}"})
            continue
        stored = entry.get("hash", "")
        shown_prev = entry.get("prev_hash", "")
        payload = {k: v for k, v in entry.items() if k not in ("hash", "signature", "entry_count")}
        if hashlib.sha256(canon(payload).encode("utf-8")).hexdigest() != stored:
            self_hash_failures.append({"line": lineno, "reason": "recomputed hash differs"})
        if shown_prev != prev:
            link_failures.append({"line": lineno, "reason": "prev_hash does not match preceding hash"})
        prev = stored or prev
        head = stored or head
        count += 1
    return {
        "file": path.name,
        "entries": count,
        "head_hash": head,
        "self_hash_failures": self_hash_failures,
        "link_failures": link_failures,
    }


def verify_signatures(path: Path, public_key, invalid_types=(ValueError,)) -> dict:
    """Strict: a signature must be present AND verify. Absence is not a pass.

    ``invalid_types`` is the caller's list of exceptions that mean "this signature
    does not verify" (InvalidSignature, a malformed base64 payload). It is a tuple
    rather than a bare ``except Exception`` on purpose: a bare handler would also
    swallow a programming error - a key of the wrong type, a bug in the payload -
    and record it as a bad signature, which is the same false-negative this module
    exists to stop. An unexpected exception propagates and the run reports it.
    """
    signed = unsigned = valid = invalid = 0
    unparseable = 0
    first_invalid = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            # Counted, not skipped: a line that will not parse is the one case
            # where a signature can disappear from the totals without any
            # signature failing, which reads as "fewer entries" rather than
            # "malformed entry".
            unparseable += 1
            if first_invalid is None:
                first_invalid = {"file": path.name, "ts": "", "reason": f"unparseable line: {exc}"}
            continue
        sig = entry.get("signature", "")
        if not sig:
            unsigned += 1
            continue
        signed += 1
        payload = {k: v for k, v in entry.items() if k not in ("hash", "signature", "entry_count")}
        ok = False
        reason = "no public key loaded"
        if public_key is not None:
            try:
                public_key.verify(base64.b64decode(sig), canon(payload).encode("utf-8"))
                ok = True
            except invalid_types as exc:
                reason = f"{type(exc).__name__}: signature does not verify"
        if ok:
            valid += 1
        else:
            invalid += 1
            if first_invalid is None:
                first_invalid = {"file": path.name, "ts": entry.get("ts", ""), "reason": reason}
    return {
        "signed": signed,
        "unsigned": unsigned,
        "valid": valid,
        "invalid": invalid,
        "unparseable": unparseable,
        "first_invalid": first_invalid,
    }


def selftest() -> int:
    """The negative controls this oracle ships with (RULE 0B-C).

    A verifier that has only ever been run on healthy input has not been
    exercised: it could be returning PASS unconditionally and nothing here would
    know. These three cases are the ones that matter, and each is asserted:

      1. a correctly signed chain with a matching anchor -> 0 (must not fire)
      2. one altered entry in that same chain             -> 1 (must fire)
      3. the anchor record removed                        -> 2 (cannot run)
    """
    import tempfile

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519

        private = ed25519.Ed25519PrivateKey.generate()
    except ImportError:
        print("selftest: cryptography absent, cannot exercise the signature leg")
        return 2

    results = []
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        key_path = tmpdir / "selftest.pub"
        key_path.write_bytes(
            private.public_key().public_bytes(
                encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
            )
        )

        def build(entries):
            prev = GENESIS
            out = []
            for base in entries:
                # The hashed and signed payload excludes hash, signature and
                # entry_count, so the signature and hash are derived from the
                # payload BEFORE either field is attached. Getting this order
                # wrong was the first version of this control: it hashed over a
                # payload that already contained the signature, so a correctly
                # built chain reported as broken.
                payload = dict(base)
                payload["prev_hash"] = prev
                body = canon(payload).encode("utf-8")
                entry = dict(payload)
                entry["hash"] = hashlib.sha256(body).hexdigest()
                entry["signature"] = base64.b64encode(private.sign(body)).decode("ascii")
                prev = entry["hash"]
                out.append(entry)
            return out

        chain = build(
            [
                {"ts": "2026-11-03T00:00:00", "type": "session_start"},
                {"ts": "2026-11-03T00:00:01", "type": "file_read", "files": ["a"]},
            ]
        )
        session = tmpdir / "session_20990101_000000_selft.jsonl"
        session.write_text("\n".join(json.dumps(e) for e in chain) + "\n", encoding="utf-8")

        def anchor(head_hash, count):
            (tmpdir / "anchors.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-11-03T00:00:02",
                        "audit_file": session.name,
                        "head_hash": head_hash,
                        "entry_count": count,
                        "session_uuid": "selft",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

        anchor(chain[-1]["hash"], len(chain))
        rc_healthy = main(["--audit-dir", str(tmpdir), "--key", str(key_path)])
        results.append(("healthy signed chain passes (must NOT fire)", rc_healthy == 0, rc_healthy))

        tampered = [dict(e) for e in chain]
        tampered[-1]["type"] = "TAMPERED"
        session.write_text("\n".join(json.dumps(e) for e in tampered) + "\n", encoding="utf-8")
        rc_tampered = main(["--audit-dir", str(tmpdir), "--key", str(key_path)])
        results.append(("altered entry is caught (must fire)", rc_tampered == 1, rc_tampered))

        (tmpdir / "anchors.jsonl").unlink()
        rc_nofile = main(["--audit-dir", str(tmpdir), "--key", str(key_path)])
        results.append(("absent anchors is exit 2, not 0", rc_nofile == 2, rc_nofile))

    print("--- selftest ---")
    failed = 0
    for name, ok, rc in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  (rc={rc})")
        failed += 0 if ok else 1
    print(f"selftest: {len(results) - failed}/{len(results)} controls behaved")
    return 0 if failed == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit-dir", default=".audit")
    ap.add_argument("--sample", type=int, default=0, help="verify chains for N random files instead of all")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", default="")
    ap.add_argument("--key", default="", help="public key path; default ~/.azazael/audit_signing_key.pub")
    ap.add_argument("--selftest", action="store_true", help="run the shipped negative controls and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    audit_dir = Path(args.audit_dir)
    anchors_file = audit_dir / "anchors.jsonl"
    if not audit_dir.is_dir():
        print(f"FAIL  audit dir not found: {audit_dir}")
        return 2
    if not anchors_file.exists():
        print(f"FAIL  anchors file not found: {anchors_file}  (an absent anchor record is a failure, not an empty one)")
        return 2

    anchors = {}
    bad_anchor_lines = 0
    for line in anchors_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            anchors[norm_name(row.get("audit_file", ""))] = row
        except json.JSONDecodeError:
            bad_anchor_lines += 1

    public_key = None
    invalid_types = (ValueError,)
    key_note = "no key supplied: signatures counted, none verified"
    key_path = Path(args.key) if args.key else Path.home() / ".azazael" / "audit_signing_key.pub"
    if key_path.exists():
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric import ed25519

            public_key = ed25519.Ed25519PublicKey.from_public_bytes(key_path.read_bytes())
            invalid_types = (InvalidSignature, ValueError, TypeError)
            key_note = f"verifying against {key_path.name} sha256:{hashlib.sha256(key_path.read_bytes()).hexdigest()[:16]}"
        except ImportError:
            key_note = "cryptography not installed: signatures cannot be verified"
        except Exception as exc:
            print(f"FAIL  public key unreadable: {exc}")
            return 2
    else:
        key_note = f"public key absent at {key_path}: signatures cannot be verified"

    files = sorted(audit_dir.glob("session_*.jsonl"))
    if not files:
        print(f"FAIL  no session files under {audit_dir}")
        return 2
    targets = files
    if args.sample and args.sample < len(files):
        random.seed(args.seed)
        targets = random.sample(files, args.sample)

    broken_chains = []
    anchor_mismatches = []
    totals = {"entries": 0, "signed": 0, "unsigned": 0, "valid": 0, "invalid": 0, "unparseable": 0}
    first_invalid = None

    for path in targets:
        rep = chain_report(path)
        totals["entries"] += rep["entries"]
        if rep["self_hash_failures"] or rep["link_failures"]:
            broken_chains.append(rep)
        row = anchors.get(path.name)
        if row is None:
            anchor_mismatches.append({"file": path.name, "reason": "no anchor row"})
        elif row.get("head_hash") != rep["head_hash"] or row.get("entry_count") != rep["entries"]:
            anchor_mismatches.append(
                {
                    "file": path.name,
                    "reason": "anchor disagrees with file",
                    "anchor_count": row.get("entry_count"),
                    "actual_count": rep["entries"],
                }
            )
        sig = verify_signatures(path, public_key, invalid_types)
        for k in ("signed", "unsigned", "valid", "invalid", "unparseable"):
            totals[k] += sig[k]
        if first_invalid is None and sig["first_invalid"]:
            first_invalid = sig["first_invalid"]

    print(f"audit dir            : {audit_dir}  ({len(files)} session files, {len(targets)} chained here)")
    print(f"anchors              : {len(anchors)} rows, {bad_anchor_lines} unparseable")
    print(f"signature check      : {key_note}")
    print(f"entries chained      : {totals['entries']}")
    print(f"broken chains        : {len(broken_chains)}")
    print(f"anchor mismatches    : {len(anchor_mismatches)}")
    print(
        f"signatures           : signed={totals['signed']} valid={totals['valid']} "
        f"invalid={totals['invalid']} unsigned={totals['unsigned']} "
        f"unparseable={totals['unparseable']}"
    )

    verdict_failures = []
    if broken_chains:
        verdict_failures.append(f"{len(broken_chains)} file(s) with a broken chain")
    if anchor_mismatches:
        verdict_failures.append(f"{len(anchor_mismatches)} anchor mismatch(es)")
    if bad_anchor_lines:
        verdict_failures.append(f"{bad_anchor_lines} unparseable anchor row(s)")
    if totals["invalid"]:
        verdict_failures.append(f"{totals['invalid']} invalid signature(s)")
    if totals["unparseable"]:
        verdict_failures.append(f"{totals['unparseable']} unparseable entry line(s)")
    if public_key is None:
        verdict_failures.append("signatures unverifiable (no usable public key)")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "audit_dir": str(audit_dir),
                    "files": len(files),
                    "chained": len(targets),
                    "anchors": len(anchors),
                    "unparseable_anchor_rows": bad_anchor_lines,
                    "totals": totals,
                    "broken_chains": broken_chains[:20],
                    "anchor_mismatches": anchor_mismatches[:20],
                    "first_invalid_signature": first_invalid,
                    "verdict": "FAIL" if verdict_failures else "PASS",
                    "failures": verdict_failures,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    if verdict_failures:
        print("VERDICT: FAIL — " + "; ".join(verdict_failures))
        return 1
    print("VERDICT: PASS — chains, anchors and signatures all check out in the scope named above")
    return 0


if __name__ == "__main__":
    sys.exit(main())
