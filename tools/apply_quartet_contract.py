#!/usr/bin/env python3
"""Apply the inverted Vermillion LightFury contract to a pillar's terence.py.

The quartet used to assert that `vermillion_lightfury` must NOT sit beside the
Windows or Ubuntu trees ("Kali-native, deprecated on Windows"). The user
corrected that on 2026-10-31: the name covers the quartet architecture and it
runs in every environment, so ABSENCE is the fault and presence in a pillar is
correct. A gate that fires on the correct arrangement trains people to ignore it.

Pillar copies drift, so this patches the three sites that make up the assertion
(contract, pure detector, both callers) and refuses if an anchor is missing.
Line endings are preserved: the Kali copy is 100% CRLF and `patch` refuses a
diff whose endings differ, which is why this does the edit in Python.

Usage:
    python tools/apply_quartet_contract.py /root/azazael           # dry run
    python tools/apply_quartet_contract.py /root/azazael --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

OLD_CONTRACT = '''        # a sibling of the tree: a Kali-native pillar must not sit NEXT TO the ML node
        "forbidden_base": ["vermillion_lightfury"],
        "why": ("Ubuntu-26.04 is the quartet's ML ACCELERATOR; sync-ubuntu.exclude "
                "keeps it lean (~400 MB) and HTML->MP4 rendering belongs to Windows"),
    },
    "windows": {
        "forbidden": [],
        "forbidden_base": ["vermillion_lightfury"],
        "why": "SYNC.md: vermillion_lightfury is Kali-native, deprecated on Windows",
    },
    "kali": {"forbidden": [], "why": "canonical source; owns vermillion_lightfury"},
    "g_drive": {"forbidden": [], "why": "archive / source of truth"},'''

NEW_CONTRACT = '''        # Vermillion LightFury runs in ALL environments, so a sibling copy is
        # REQUIRED here, not forbidden (user-stated 2026-10-31).
        "required_base": ["vermillion_lightfury"],
        "why": ("Ubuntu-26.04 is the quartet's ML ACCELERATOR; sync-ubuntu.exclude "
                "keeps it lean (~400 MB) and HTML->MP4 rendering belongs to Windows. "
                "Vermillion LightFury is required in every pillar."),
    },
    "windows": {
        "forbidden": [],
        "required_base": ["vermillion_lightfury"],
        "why": ("the quartet architecture runs in every environment; a missing "
                "vermillion_lightfury is the fault, not the presence of one"),
    },
    "kali": {"forbidden": [],
             "required_base": ["vermillion_lightfury"],
             "why": "canonical source; carries vermillion_lightfury"},
    # The archive is not a pillar that RUNS anything, so presence is not
    # asserted here: G: holds whatever the pillars have published.
    "g_drive": {"forbidden": [], "why": "archive / source of truth"},'''

DETECTOR = '''def _contract_missing(root, env, contract, base=None):
    """PURE: which REQUIRED siblings are absent for `env`.

    The mirror of `_contract_violations`: that one reports paths whose
    PRESENCE is the fault, this one reports paths whose ABSENCE is.
    """
    spec = contract.get(env) or {}
    if not base:
        return []
    return [rel for rel in spec.get("required_base", ())
            if not os.path.exists(os.path.join(base, *rel.split("/")))]


def _looks_like_project_root(path):'''

ORACLE_ANCHOR = '''                       % (env, ENV_CONTRACT[env]["why"]),
                severity="high", remedy=None, verifier=None))'''

ORACLE_ADDITION = ORACLE_ANCHOR + '''
        for miss in _contract_missing(AZ, env, ENV_CONTRACT, base):
            faults.append(Fault(
                fault_id="envrole:%s:missing:%s" % (env, miss),
                klass="environment_role_violation",
                subject=os.path.join(base, miss),
                detail=("REQUIRED in the %s environment but absent: %s"
                        % (env, ENV_CONTRACT[env]["why"])),
                severity="high", remedy=None, verifier=None))'''

AUDIT_ANCHORS = [
    ('''    hits = _contract_violations(path, env, ENV_CONTRACT, base)''',
     '''    hits = _contract_violations(path, env, ENV_CONTRACT, base)
    # Absence can be the fault too; without this call the CLI reported
    # "0 violations" while the contract it claims to check went unenforced.
    missing = _contract_missing(path, env, ENV_CONTRACT, base)'''),
    ('''    for h in hits:
        print("  [VIOLATION] %s  (%s)" % (h["path"], h["abs"]))''',
     '''    for h in hits:
        print("  [VIOLATION] forbidden here: %s  (%s)" % (h["path"], h["abs"]))
    for m in missing:
        print("  [VIOLATION] REQUIRED but absent: %s  (%s)"
              % (m, os.path.join(base, m)))'''),
    ('''    total = len(hits) + (1 if len(mirrors) > 1 else 0)''',
     '''    total = len(hits) + len(missing) + (1 if len(mirrors) > 1 else 0)'''),
]


CONTROL_OLD = '''        os.makedirs(os.path.join(base, "vermillion_lightfury"), exist_ok=True)
        if not _contract_violations(os.path.join(base, "tree"), "ubuntu",
                                    ENV_CONTRACT, base):
            return False, "missed a forbidden sibling (base) path"'''

CONTROL_NEW = '''        # ABSENT pillar = the fault now.
        if not _contract_missing(os.path.join(base, "tree"), "ubuntu",
                                 ENV_CONTRACT, base):
            return False, "missed a REQUIRED pillar that is absent"
        # PRESENT pillar = correct, must stay silent in both detectors.
        os.makedirs(os.path.join(base, "vermillion_lightfury"), exist_ok=True)
        if _contract_missing(os.path.join(base, "tree"), "ubuntu",
                             ENV_CONTRACT, base):
            return False, "fired on a REQUIRED pillar that is present"
        if _contract_violations(os.path.join(base, "tree"), "ubuntu",
                                ENV_CONTRACT, base):
            return False, "a present Vermillion LightFury tree is not a violation"'''

CONTROL_DOC_OLD = '''    """A tree matching its role must stay silent; a tree with a forbidden path
    must fire. Runs the pure detectors on synthetic fixtures, not the live tree."""'''

CONTROL_DOC_NEW = '''    """A tree matching its role must stay silent; a tree with a forbidden path
    must fire. Runs the pure detectors on synthetic fixtures, not the live tree.

    UPDATED for the inverted Vermillion LightFury contract: the old control
    asserted a sibling copy was a VIOLATION, which is now the required state.
    """'''


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tree_root", help="pillar tree, e.g. /root/azazael")
    ap.add_argument("--apply", action="store_true", help="write the change")
    a = ap.parse_args(argv)

    target = Path(a.tree_root) / "tools" / "terence_invariants.py"
    if not target.is_file():
        print("MISSING: %s" % target)
        return 2
    raw = target.read_bytes()
    crlf = raw.count(b"\r\n") > 0 and raw.count(b"\r\n") == raw.count(b"\n")
    text = raw.decode("utf-8").replace("\r\n", "\n")

    edits = [(OLD_CONTRACT, NEW_CONTRACT),
             ("def _looks_like_project_root(path):", DETECTOR),
             (ORACLE_ANCHOR, ORACLE_ADDITION),
             (CONTROL_DOC_OLD, CONTROL_DOC_NEW),
             (CONTROL_OLD, CONTROL_NEW)] + AUDIT_ANCHORS
    patched = text
    applied = 0
    # Idempotent PER EDIT, not all-or-nothing: a pillar patched before the
    # control was updated must still receive the control fix, otherwise its
    # self-test keeps asserting the superseded rule and reports the oracle
    # UNVALIDATED while the code is right.
    for i, (anchor, replacement) in enumerate(edits, 1):
        if replacement in patched:
            continue
        if anchor not in patched:
            print("ANCHOR %d NOT FOUND in %s:\n%s" % (i, target, anchor.splitlines()[0]))
            return 3
        patched = patched.replace(anchor, replacement, 1)
        applied += 1

    if not applied:
        print("ALREADY APPLIED: %s" % target)
        return 0

    try:
        compile(patched, str(target), "exec")
    except SyntaxError as exc:
        print("REFUSING: patched source does not compile: %s" % exc)
        return 4

    if not a.apply:
        print("DRY RUN ok: %s would receive %d edit(s) (%d -> %d bytes, eol=%s)"
              % (target, applied, len(raw), len(patched.encode("utf-8")),
                 "crlf" if crlf else "lf"))
        return 0

    out = patched.replace("\n", "\r\n") if crlf else patched
    target.write_bytes(out.encode("utf-8"))
    print("PATCHED: %s (%d -> %d bytes, eol=%s)"
          % (target, len(raw), len(out.encode("utf-8")), "crlf" if crlf else "lf"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
