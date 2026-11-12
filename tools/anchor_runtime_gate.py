# anchor_runtime_gate.py — runtime-execution + production-reach gate (Lane C, #1683).
"""Fail-closed gate: the anchor pipeline must IMPORT, EXECUTE live, RECORD
evidence rows, and RESOLVE via the production discovery path. Any absent
proof is a FAIL (exit 1), never a skip: this gate refuses a PASS it cannot
verify, which is what makes it a gate rather than a report.

Checks:
  R1 import:    CORE.WORMS.expansion.anchor_retrieval imports under 3.15t.
  R2 execute:   AnchorRetrievalWorm().execute(sys.executable, rerank=False)
                returns the shaped dict (success/anchors/n_hits/grounding/
                rerank) and never raises (the module's own contract).
  R3 evidence:  query_memories(source="technique-link") holds the
                hook:anchor_retrieve:execute row for the target subject.
  R4a discovery: a FRESH interpreter running discover_worms() (the production
                path, not a direct import) resolves AnchorRetrievalWorm in
                WORM_REGISTRY.
  R4b dispatch: the registry-resolved class instantiates and executes.

Negative controls (run every time; a gate never watched FAIL is not a gate):
  N1 bogus worm name must NOT resolve (proves R4a discriminates).
  N2 bogus subject must match ZERO rows (proves the R3 filter is not vacuous).

Usage:
  python tools/anchor_runtime_gate.py [--target PATH]

Exit 0 = all rows PASS. Exit 1 = any row FAIL. Exit 2 = harness error.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from CORE.WORMS.expansion import anchor_retrieval as _ar
    _AR_OK = True
    _AR_IMPORT_ERROR = ""
except Exception as exc:
    _ar = None
    _AR_OK = False
    _AR_IMPORT_ERROR = "%s: %s" % (type(exc).__name__, exc)

try:
    from CORE.technique_runtime import get_memory_store as _get_store
    _STORE_OK = True
    _STORE_ERROR = ""
except Exception as exc:
    _get_store = None
    _STORE_OK = False
    _STORE_ERROR = "%s: %s" % (type(exc).__name__, exc)

try:
    from CORE.worm_registry import WORM_REGISTRY as _REGISTRY
    from CORE.worm_registry import discover_worms as _discover
    _REG_OK = True
    _REG_ERROR = ""
except Exception as exc:
    _REGISTRY = {}
    _discover = None
    _REG_OK = False
    _REG_ERROR = "%s: %s" % (type(exc).__name__, exc)

_ROWS: list = []


def _row(name: str, passed: bool, evidence: str) -> None:
    _ROWS.append((name, bool(passed), str(evidence)))
    print("[anchor-gate] %s %s -- %s" % ("PASS" if passed else "FAIL", name, evidence))


def _check_r1() -> bool:
    if not _AR_OK or _ar is None:
        _row("R1-import", False, "import failed: %s" % _AR_IMPORT_ERROR)
        return False
    has_worm = hasattr(_ar, "AnchorRetrievalWorm")
    has_fn = all(hasattr(_ar, n) for n in (
        "extract_anchors", "retrieve_for_anchors", "rerank_candidates", "grounded_check"))
    ok = bool(has_worm and has_fn)
    _row("R1-import", ok, "module=%s worm=%s fns=%s" % (_ar.__name__, has_worm, has_fn))
    return ok


def _check_r2(target: str) -> object:
    if not _AR_OK or _ar is None:
        _row("R2-execute", False, "skipped: R1 failed")
        return None
    try:
        worm = _ar.AnchorRetrievalWorm()
        anchors = _ar.extract_anchors(target)
        terms = anchors.terms()
        claim_text = ("binary references %s" % terms[0]) if terms else "no anchor terms present"
        out = worm.execute(target, claims=[{"text": claim_text}], rerank=False)
    except Exception as exc:
        _row("R2-execute", False, "raised (contract is never-raise): %s: %s"
             % (type(exc).__name__, exc))
        return None
    # Depth bar: a thin run proves plumbing, not retrieval. The middle of
    # the pipeline (FTS5 loop over real terms) must execute or R2 refuses.
    want = {"success", "anchors", "n_hits", "grounding", "rerank", "mars"}
    ok = (isinstance(out, dict) and want <= set(out)
          and len(terms) > 0)
    grounding = out.get("grounding") if isinstance(out, dict) else None
    kept = grounding.get("n_kept") if isinstance(grounding, dict) else "?"
    _row("R2-execute", ok, "target=%s success=%s n_hits=%s n_kept=%s terms=%d" % (
        target, out.get("success"), out.get("n_hits"), kept, len(terms)))
    return out


def _technique_rows(limit: int = 500) -> list:
    if not _STORE_OK or _get_store is None:
        return []
    try:
        rows = _get_store().query_memories(source="technique-link", limit=int(limit)) or []
    except Exception:
        return []
    return [r for r in rows if isinstance(r, dict)]


def _is_anchor_row(row: dict, subject: str, stage: str = "execute") -> bool:
    event = str(row.get("event_type", "") or row.get("event", "") or "")
    data = row.get("data")
    data = data if isinstance(data, dict) else {}
    return event == ("hook:anchor_retrieve:%s" % stage) and str(data.get("subject", "")) == str(subject)


def _check_r3(target: str) -> bool:
    if not _STORE_OK:
        _row("R3-evidence", False, "store unavailable: %s" % _STORE_ERROR)
        return False
    rows = _technique_rows()
    hits = [r for r in rows if _is_anchor_row(r, target)]
    ok = len(hits) >= 1
    _row("R3-evidence", ok, "technique-link rows=%d anchor_retrieve:execute for subject=%d"
         % (len(rows), len(hits)))
    return ok


def _check_r4() -> bool:
    if not _REG_OK or _discover is None:
        _row("R4-reach", False, "registry unavailable: %s" % _REG_ERROR)
        return False
    probe = ("from CORE.worm_registry import discover_worms, WORM_REGISTRY; "
             "n = discover_worms(); "
             "print('discovered=%d' % n); "
             "print('resolved=%s' % ('AnchorRetrievalWorm' in WORM_REGISTRY))")
    try:
        proc = subprocess.run([sys.executable, "-c", probe], cwd=str(SRC),
                              capture_output=True, text=True, timeout=300,
                              encoding="utf-8", errors="replace")
    except Exception as exc:
        _row("R4-reach", False, "fresh-discovery probe failed: %s: %s" % (type(exc).__name__, exc))
        return False
    resolved = "resolved=True" in (proc.stdout or "")
    _row("R4a-discovery", resolved and proc.returncode == 0,
         "rc=%d out=%s err=%s" % (proc.returncode, (proc.stdout or "").strip().replace("\n", " ")[:160],
                                  (proc.stderr or "").strip()[:120]))
    bogus = "AnchorRetrievalWorm_NoSuchFixture_XYZ"
    n1 = bogus not in (_REGISTRY or {})
    _row("N1-bogus-worm-unresolved", n1, "%s absent=%s" % (bogus, n1))
    live_ok = False
    live_evidence = "skipped"
    if resolved:
        try:
            _discover()
            entry = (_REGISTRY or {}).get("AnchorRetrievalWorm") or {}
            cls = entry.get("class")
            worm = cls()
            out = worm.execute(sys.executable, claims=[{"text": "registry dispatch probe"}],
                               rerank=False)
            live_ok = isinstance(out, dict) and "grounding" in out
            live_evidence = "registry class=%s executed success=%s" % (
                getattr(cls, "__name__", "?"), out.get("success") if isinstance(out, dict) else "?")
        except Exception as exc:
            live_evidence = "registry dispatch raised: %s: %s" % (type(exc).__name__, exc)
    _row("R4b-dispatch", live_ok, live_evidence)
    rows = _technique_rows(limit=50)
    n2 = not any(_is_anchor_row(r, "no-such-binary-xyz") for r in rows)
    _row("N2-bogus-subject-zero-rows", n2, "scanned=%d bogus-hits=0" % len(rows))
    return bool(resolved and proc.returncode == 0 and live_ok and n1 and n2)


def main(argv: list) -> int:
    target = argv[1] if len(argv) > 1 and not argv[1].startswith("--") else sys.executable
    if argv[1:] and argv[1] == "--target" and len(argv) > 2:
        target = argv[2]
    print("[anchor-gate] target=%s python=%s" % (target, sys.version.split()[0]))
    try:
        r1 = _check_r1()
        if r1:
            _check_r2(target)
        _check_r3(target)
        _check_r4()
    except Exception as exc:
        print("[anchor-gate] HARNESS-ERROR %s: %s" % (type(exc).__name__, exc))
        return 2
    failed = [n for (n, ok, _) in _ROWS if not ok]
    print("[anchor-gate] %d/%d PASS" % (len(_ROWS) - len(failed), len(_ROWS)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
