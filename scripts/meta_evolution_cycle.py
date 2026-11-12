#!/usr/bin/env python3
"""Bounded self-evolution cycle for the current Azazael control plane.

Unlike the historical mesh-chain audits, this controller has one explicit
lifecycle and one retention authority:

    observe -> select -> propose -> evaluate -> retain/revert -> verify

The default is observation-only.  ``--apply`` permits at most one exact source
substitution.  Protected control/evaluator/test paths are rejected, a test band
that collects nothing is refused, and the targeted audit count must fall.  A
model may propose; only independently measured evidence may retain.

Outputs:
  state/meta_evolution/current.json       latest machine-readable report
  state/meta_evolution/lineage.jsonl      append-only decisions
  state/meta_evolution/proof.json         current architecture/reachability map
  state/meta_evolution/patches/<id>.json  one bounded proposal when generated
  logs/current_vacuous_audit.json         fresh source observations
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from CORE import meta_evolution as me  # noqa: E402
import subprocess
import hashlib

STATE_DIR = Path(os.environ.get("AZAZAEL_META_EVOLUTION_DIR", ROOT / "state" / "meta_evolution"))
REPORT_PATH = STATE_DIR / "current.json"
PROOF_PATH = STATE_DIR / "proof.json"
LINEAGE_PATH = STATE_DIR / "lineage.jsonl"
PATCH_DIR = STATE_DIR / "patches"
AUDIT_PATH = ROOT / "logs" / "current_vacuous_audit.json"
OLLAMA_URL = os.environ.get("AZAZAEL_META_OLLAMA", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = os.environ.get("AZAZAEL_META_MODEL", "eve-v2u")
#: Second-opinion model when the primary proposal is unusable.
#: 2026-09-28: was qwythos-9b, not installed in Ollama -> /api/chat 404 and
#: meta verdict BLOCKED x2. Retargeted to installed sam860/olmoe (chat OK).
#: Env AZAZAEL_META_MODEL_FALLBACK still overrides.
FALLBACK_MODEL = os.environ.get("AZAZAEL_META_MODEL_FALLBACK", "sam860/olmoe-1b-7b-0924:latest")
DEFAULT_BAND = (
    "tests/test_mesh_fabric_followups.py",
    "tests/test_mesh_fabric_gaps.py",
    "tests/test_mesh_fabric_health.py",
)

EXIT_OK = 0
EXIT_BLOCKED = 2
EXIT_FAILURE = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_lineage(row: Mapping[str, Any]) -> None:
    LINEAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LINEAGE_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _run(cmd: Sequence[str], timeout: int) -> dict[str, Any]:
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                              text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"rc": None, "timeout": True, "stdout": "", "stderr": "timeout"}
    return {"rc": proc.returncode, "timeout": False,
            "stdout": (proc.stdout or "")[-2000:],
            "stderr": (proc.stderr or "")[-2000:]}


def _scan_source() -> dict[str, Any]:
    """Recompute observations from current source; never reuse an old report."""
    script = ROOT / "tools" / "vacuous_check_audit.py"
    result = _run([sys.executable, str(script), "--json", str(AUDIT_PATH), "--top", "20"], 180)
    report = _load_json(AUDIT_PATH, {})
    if result["rc"] != 0 or not isinstance(report, dict) or not report.get("items"):
        return {"ok": False, "error": "fresh source audit failed",
                "scanner": result, "findings": []}
    return {"ok": True, "files_scanned": report.get("files_scanned"),
            "findings": report.get("items") or [],
            "patterns": report.get("patterns") or [],
            "by_severity": report.get("by_severity") or {},
            "artifact": str(AUDIT_PATH.relative_to(ROOT)).replace("\\", "/"),
            "artifact_mtime": AUDIT_PATH.stat().st_mtime,
            "caveat": report.get("caveat", "")}


def _pattern_for(candidate: me.Candidate,
                 patterns: list[dict]) -> dict | None:
    """Pattern group matching this candidate (same file + detector + code).

    Returns the group dict (with locations/count) or None. Pure; tested.
    """
    try:
        with open(ROOT / candidate.path, encoding="utf-8",
                   errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    code = str(candidate.code or "").strip()
    if not code:
        return None
    norm = " ".join(code.split())
    for p in patterns or []:
        if not isinstance(p, dict):
            continue
        if (str(p.get("path")) == candidate.path
                and str(p.get("detector")) == candidate.detector
                and " ".join(str(p.get("code", "")).split()) == norm
                and int(p.get("count", 0)) >= 2):
            return p
    return None


def _test_band_for(path: str) -> tuple[list[str], list[str]]:
    tools_dir = ROOT / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    try:
        import self_modify
        return self_modify.resolve_band((path,))
    except Exception:
        return list(DEFAULT_BAND), []


def _attempted_ids() -> set[str]:
    """Candidate ids a cycle has already consumed (never re-select them).

    This guard was dead code: it read a TOP-LEVEL ``candidate_id`` that no
    writer ever produced -- lineage rows nest the id under ``candidate.id``
    and mutation_ledger rows carry no id at all (measured 2026-09-25: 15
    ledger rows, 0 with the field). The set was therefore ALWAYS empty and
    ``select`` re-picked the identical top candidate every hour while 725
    findings queued behind it (FORENSIC_INVESTIGATION_2026-09-24.md PART 8).
    Both shapes are accepted here, and _finish() now stamps the flat key.
    """
    ids: set[str] = set()
    # Every ledger generation, not just the live file: the ledger rotates
    # (`mutation_ledger.jsonl.N`), and this guard's whole job is to NEVER
    # re-select a consumed candidate -- reading only the live file would let it
    # re-pick anything whose row had been archived away.
    _ledger_files = sorted((ROOT / "logs").glob("mutation_ledger.jsonl*"))
    for path in (LINEAGE_PATH, *_ledger_files):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            cid = row.get("candidate_id")
            if not cid and isinstance(row.get("candidate"), dict):
                cid = row["candidate"].get("id")
            if cid:
                ids.add(str(cid))
    return ids


def _is_def_line(stripped: str) -> bool:
    return (stripped.startswith("def ") or stripped.startswith("class ")
            or stripped.startswith("async def "))


def _scope_of(lines: list[str], target: int) -> tuple[int, int, str]:
    """Enclosing def/class block of a 0-based line index.

    Returns (lo, hi, funcname); funcname "" when the line sits at module
    level (no enclosing scope). The block runs from the enclosing def/class
    line through to (not incl.) the next def/class at an indent at or above
    the enclosing one — nested defs stay INSIDE the scope. Never raises;
    falls back to the single line.
    """
    try:
        n = len(lines)
        target = max(0, min(target, n - 1))
        indent_of = len(lines[target]) - len(lines[target].lstrip())
        funcname, def_idx, d0 = "", -1, None
        if indent_of > 0:
            i = target
            while i > 0:
                i -= 1
                prev = lines[i]
                stripped = prev.strip()
                if not stripped or stripped.startswith("#") or stripped.startswith("@"):
                    continue
                prev_indent = len(prev) - len(prev.lstrip())
                if prev_indent < indent_of and _is_def_line(stripped):
                    def_idx, d0 = i, prev_indent
                    m = stripped.split("(")[0].split()[-1].rstrip(":")
                    funcname = m
                    break
                if prev_indent < indent_of:
                    def_idx, d0 = i, prev_indent
                    break
        lo = def_idx if def_idx >= 0 else target
        hi = target + 1
        if def_idx >= 0:
            j = def_idx + 1
            while j < n:
                nxt = lines[j]
                stripped = nxt.strip()
                if stripped and not stripped.startswith("#"):
                    nxt_indent = len(nxt) - len(nxt.lstrip())
                    if nxt_indent <= d0 and _is_def_line(stripped):
                        break
                j += 1
            hi = j
        return lo, hi, funcname
    except Exception:
        return target, target + 1, ""


def _context_for(candidate: me.Candidate, radius: int = 8,
                 max_lines: int = 200) -> str:
    """Source context for a proposal: enclosing scope + nearest caller.

    The fixed ±8-line window hid real defect sites (bake-off 2026-09-25).
    Scope rule: the flagged line's enclosing function, PLUS the nearest
    same-file function that calls it (fixes routinely live at the comparison
    site, not the guard — e.g. the empty-band guard vs the max() tie-break
    60 lines down). Total capped at max_lines; module-level targets keep the
    radius window. Pure; unit-tested.
    """
    lines = (ROOT / candidate.path).read_text(
        encoding="utf-8", errors="replace").splitlines()
    n = len(lines)
    if not n:
        return ""
    target = max(0, min(candidate.line - 1, n - 1))
    lo = max(0, target - radius)
    hi = min(n, target + radius + 1)
    try:
        slo, shi, funcname = _scope_of(lines, target)
        if not funcname:
            # Module-level target: no enclosing scope; the radius window is
            # the context (the previous/next top-level defs are siblings).
            pass
        else:
            lo, hi = min(lo, slo), max(hi, shi)
        if funcname:
            import re as _re
            call = _re.compile(r"(?<![\w.])" + _re.escape(funcname) + r"\s*\(")
            best, best_dist = -1, None
            for i, text in enumerate(lines):
                if lo <= i < hi:
                    continue
                if call.search(text):
                    dist = abs(i - target)
                    if best < 0 or dist < best_dist:
                        best, best_dist = i, dist
            if best >= 0:
                clo, chi, _ = _scope_of(lines, best)
                lo, hi = min(lo, clo), max(hi, chi)
                if hi - lo > max_lines:
                    # Keep the flagged scope whole; trim the caller side.
                    if target - lo > max_lines // 2:
                        lo = target - max_lines // 2
                    hi = min(n, lo + max_lines)
    except Exception:
        pass
    lo, hi = max(0, lo), min(n, hi)
    return "\n".join(f"{i + 1:5d}: {lines[i]}" for i in range(lo, hi))


def _code_occurrences(candidate: me.Candidate) -> int:
    """How many times the flagged code occurs in its file (Python-side).

    The contract demands exact-once old text; telling the model the count
    up front (instead of letting it discover non-uniqueness by rejection)
    converts a wasted round-trip into a scoped first attempt.
    Returns -1 when uncountable (fail-open: no instruction appended).
    """
    try:
        text = (ROOT / candidate.path).read_text(
            encoding="utf-8", errors="replace")
        code = str(candidate.code or "")
        if not code:
            return -1
        return text.count(code)
    except Exception:
        return -1


def _model_available(model: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        names = sorted(str(item.get("name", "")) for item in payload.get("models", []))
        return {"ok": bool(names), "models": names,
                "requested": model, "endpoint": OLLAMA_URL}
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return {"ok": False, "models": [], "requested": model,
                "endpoint": OLLAMA_URL, "error": str(exc)[:200]}


def _ask_model(candidate: me.Candidate, model: str, timeout: int = 600,
              pattern: dict | None = None) -> dict[str, Any]:
    # 180s was too tight: the first --apply run (2026-09-25T00:35) blocked at
    # exactly the cap with a cold model load. Budget the proposal generously;
    # the chain's meta job timeout is the real bound.
    context = _context_for(candidate)
    uniqueness = ""
    occurrences = _code_occurrences(candidate)
    if occurrences > 1:
        uniqueness = (
            f"\nWARNING: the flagged line occurs {occurrences} times in this "
            "file. Your old substring MUST span surrounding context lines "
            "until it occurs exactly once — a bare repeated line will be "
            "rejected.")
    elif occurrences == 0:
        uniqueness = (
            "\nNOTE: the flagged line text was not found verbatim (whitespace "
            "drift); match the context lines exactly as shown above.")
    system = (
        "You repair one verified false-success check. Reply with exactly one JSON "
        "object and no markdown, in ONE of two forms. Form A (substring): "
        "{\"old\":\"exact substring\",\"new\":\"replacement\"} where old occurs "
        "exactly once. Form B (line range, PREFERRED when the line repeats): "
        "{\"lines\":[start,end],\"new\":\"replacement\"} with 1-based lines "
        "spanning the full unique block (max 15 lines). Change the minimum; "
        "preserve behavior that genuinely handles zero items; make absence of "
        "evidence fail closed. Do not weaken or remove tests. "
        "Form C (repeated idiom, only when the PATTERN note below names "
        "2+ sites): {\"old\":\"...\",\"new\":\"...\",\"repeat\":true} — one "
        "pair applied at every occurrence (cap 12 sites)."
    )
    pattern_note = ""
    if isinstance(pattern, dict) and int(pattern.get("count", 0)) >= 2:
        locs = ", ".join(str(l) for l in pattern.get("locations", [])[:12])
        pattern_note = (
            f"\nPATTERN: this idiom occurs {pattern['count']} times in this "
            f"file (lines {locs}). Fix ALL of them with Form C "
            "(\"repeat\":true); the validator applies one verified pair at "
            "every site.")
    user = (
        f"File: {candidate.path}\nDetector: {candidate.detector}\n"
        f"Why it is suspect: {candidate.why}\nLine {candidate.line}: {candidate.code}\n\n"
        f"Context:\n{context}{uniqueness}{pattern_note}\n\nReturn the smallest exact substitution that makes "
        "the check require real evidence."
    )
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "stream": False,
        # eve-v2u is a Qwen3.5 thinking-capable model: on a hard prompt its
        # hidden reasoning consumes the budget and message.content comes back
        # EMPTY (measured 2026-09-25: reply_head '', eval_count 1111 for a
        # 6-token answer). We want the patch directly, not the deliberation.
        "think": False,
        # Keep the model resident across hourly cycles: Ollama's default 5-min
        # idle unload would cold-load it before every proposal (the timeout
        # class above). Override with AZAZAEL_META_KEEP_ALIVE.
        "keep_alive": os.environ.get("AZAZAEL_META_KEEP_ALIVE", "6h"),
        "options": {"temperature": 0.0, "num_ctx": 8192},
    }
    request = urllib.request.Request(
        OLLAMA_URL + "/api/chat", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:240]}
    text = str((payload.get("message") or {}).get("content", ""))
    try:
        start, end = text.find("{"), text.rfind("}")
        parsed = json.loads(text[start:end + 1]) if start >= 0 < end else {}
    except ValueError:
        parsed = {}
    if not isinstance(parsed, dict):
        return {"ok": False, "error": "model reply was not a patch object",
                "reply_head": text[:200]}
    if "lines" in parsed and "old" not in parsed:
        # Form B: line range. Python extracts old text (models miscount
        # repeated lines; line numbers don't repeat). Bounded to 15 lines.
        try:
            a, b = int(parsed["lines"][0]), int(parsed["lines"][1])
            ftext = (ROOT / candidate.path).read_text(
                encoding="utf-8", errors="replace").splitlines()
            if not (1 <= a <= b <= len(ftext)) or (b - a) > 14:
                return {"ok": False,
                        "error": "line range out of bounds or over 15 lines",
                        "reply_head": text[:200]}
            parsed = {"old": "\n".join(ftext[a - 1:b]),
                      "new": str(parsed.get("new", ""))}
        except (ValueError, TypeError, IndexError, OSError) as exc:
            return {"ok": False, "error": f"bad line range: {exc}"[:200],
                    "reply_head": text[:200]}
    if "old" not in parsed or "new" not in parsed:
        return {"ok": False, "error": "model reply was not a patch object",
                "reply_head": text[:200]}
    patch: dict[str, Any] = {"encoding": "utf-8", "subs": {
        candidate.path: [[str(parsed["old"]), str(parsed["new"])]]}}
    # Form C passthrough: the validator (not the model) verifies the repeat
    # count against the file (2..MAX_REPEAT_SITES) and the applier replaces
    # every occurrence. Anything else here would let the model set blast
    # radius by assertion.
    if parsed.get("repeat") is True:
        patch["repeat"] = True
    return {"ok": True, "patch": patch,
            "model": model, "eval_count": payload.get("eval_count")}


def _propose_with_fallback(candidate: me.Candidate, model: str,
                           timeout: int = 600,
                           pattern: dict | None = None) -> dict[str, Any]:
    """Ask the primary model; on unusable output, run a full debate.

    Falls back when the reply is not a patch object OR fails contract
    validation (empty, identity, or non-unique old text — the measured
    eve-v2u failure modes). The fallback is scripts/debate_patch.py
    (proposer→critic→revise→judge with deterministic agreement), not a
    second single shot: the bake-off showed single shots fail on judgment,
    and debate adds the missing adversarial check. Attempts recorded; the
    caller still enforces the contract + evaluation harness on whichever
    patch survives.
    """
    attempts: list[dict[str, Any]] = []
    proposed = _ask_model(candidate, model, timeout=timeout, pattern=pattern)
    attempts.append({"model": model, "ok": bool(proposed.get("ok")),
                     "error": proposed.get("error", "")})
    if proposed.get("ok"):
        try:
            me.validate_substitution_patch(ROOT, proposed["patch"])
            proposed["attempts"] = attempts
            return proposed
        except me.MetaEvolutionError as exc:
            attempts[-1]["contract"] = str(exc)[:200]
    fb = (FALLBACK_MODEL or "").strip()
    debated = _run_debate_fallback(candidate, model, timeout, pattern)
    attempts.append(debated["attempt"])
    debated["result"]["attempts"] = attempts
    return debated["result"]


def _run_debate_fallback(candidate: me.Candidate, model: str,
                         timeout: int, pattern: dict | None = None) -> dict[str, Any]:
    """Run debate_patch as the fallback proposer. Fail-open: any harness
    trouble degrades to the legacy single-shot fallback model call."""
    why = candidate.why
    if isinstance(pattern, dict) and int(pattern.get("count", 0)) >= 2:
        locs = ", ".join(str(l) for l in pattern.get("locations", [])[:12])
        why = ("%s [PATTERN: same idiom at %d sites (%s) — fix all with "
               "Form C repeat:true]" % (why, pattern["count"], locs))
    finding = {"path": candidate.path, "line": candidate.line,
               "detector": candidate.detector,
               "severity": candidate.severity, "code": candidate.code,
               "why": why}
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import debate_patch
        result = debate_patch.run_debate(
            finding, timeout=max(60, timeout // 2))
        patch = result.get("patch")
        if patch:
            return {"attempt": {"model": "debate",
                                "ok": True,
                                "agreement": result.get("agreement"),
                                "confidence": result.get("confidence"),
                                "rounds": result.get("rounds")},
                    "result": {"ok": True, "patch": patch,
                               "model": "debate"}}
        return {"attempt": {"model": "debate", "ok": False,
                            "error": str(result.get("verdict", {}).get(
                                "reason", "rejected"))[:200],
                            "agreement": result.get("agreement")},
                "result": {"ok": False,
                           "error": "debate rejected: %s" % str(
                               result.get("verdict", {}).get(
                                   "reason", ""))[:200]}}
    except Exception as exc:
        fb = (FALLBACK_MODEL or "").strip()
        if fb and fb != model:
            proposed = _ask_model(candidate, fb, timeout=timeout)
            return {"attempt": {"model": fb, "ok": bool(proposed.get("ok")),
                                "error": proposed.get("error", ""),
                                "degraded": "debate harness unavailable: %s"
                                % str(exc)[:120]},
                    "result": proposed}
        return {"attempt": {"model": "debate", "ok": False,
                            "error": f"harness unavailable: {exc}"[:200]},
                "result": {"ok": False,
                           "error": f"debate harness unavailable: {exc}"[:200]}}


def _evaluate_with_self_modify(patch_path: Path,
                               band: Sequence[str]) -> dict[str, Any]:
    """Run the test-band evaluator over the patched tree.

    MEASURED (engram #569): this called
    `self_modify(patch, band, keep_on_tie=True, model=model, progress=False)`,
    but `tools/self_modify.self_modify` accepts neither `model` nor `progress`.
    EVERY `--apply` cycle therefore raised `TypeError` here and was reported as
    `verdict: rejected`, so the meta-evolution loop could never retain a mutation
    -- and the crash was reported as an evaluation rejection rather than as a
    broken harness. The evaluator is test-based (tier2 band, optional tier3
    suite) and takes no model, so none is forwarded.
    """
    tools_dir = ROOT / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    try:
        import self_modify
        return self_modify.self_modify(patch_path, band, keep_on_tie=True)
    except Exception as exc:
        # A distinct verdict keeps a harness crash from reading as a verdict on
        # the candidate; the cycle's own reason line is rewritten from this.
        return {"verdict": "harness_error",
                "reason": "harness error: %s: %s" % (type(exc).__name__, exc)}


def _restore(paths: Sequence[str], backups: Mapping[str, bytes]) -> list[str]:
    restored: list[str] = []
    for rel in paths:
        if rel not in backups:
            continue
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(backups[rel])
        restored.append(rel)
    return restored


def _record_direct_memory(report: Mapping[str, Any]) -> None:
    try:
        from CORE.direct_memory import DirectMemory
        DirectMemory().record("meta_evolution_cycle", dict(report), tags=("evolution", "control-plane"))
    except Exception:
        pass


def run_cycle(apply: bool = False, model: str = DEFAULT_MODEL,
              limit: int = 1, audit_timeout: int = 600) -> dict[str, Any]:
    started = time.time()
    life = me.Lifecycle()
    report: dict[str, Any] = {
        "schema": "azazael.meta_evolution.cycle.v1",
        "version": me.VERSION,
        "at": _now(),
        "apply": bool(apply),
        "model": model,
        "limit": max(1, min(int(limit), 3)),
        "phases": {},
    }

    life.transition("observe")
    observation = _scan_source()
    report["phases"]["observe"] = {
        "ok": observation["ok"], "files_scanned": observation.get("files_scanned"),
        "findings": len(observation.get("findings") or []),
        "by_severity": observation.get("by_severity"),
        "artifact": observation.get("artifact"),
        "error": observation.get("error", ""),
    }
    if not observation["ok"]:
        report.update({"verdict": "BLOCKED", "reason": observation["error"], "lifecycle": life.snapshot()})
        _finish(report, started)
        return EXIT_BLOCKED

    life.transition("select")

    def covered(path: str) -> bool:
        band, unresolved = _test_band_for(path)
        return bool(band) and not unresolved

    candidate = me.choose_candidate(ROOT, observation["findings"], covered,
                                    _attempted_ids())
    report["phases"]["select"] = {
        "ok": candidate is not None,
        "candidate": candidate.to_dict() if candidate else None,
        "reason": "" if candidate else "no covered, unattempted actionable candidate",
    }
    if candidate is None:
        life.transition("blocked")
        report.update({"verdict": "BLOCKED", "reason": "no safe candidate", "lifecycle": life.snapshot()})
        _finish(report, started)
        return EXIT_BLOCKED

    band, unresolved = _test_band_for(candidate.path)
    report["phases"]["select"]["test_band"] = band
    if unresolved or not band:
        raise RuntimeError("candidate selection admitted an uncovered file")

    availability = _model_available(model)
    report["phases"]["select"]["model_availability"] = availability
    if not apply:
        life.transition("blocked")
        report.update({"verdict": "DRY", "reason": "observation complete; no mutation authorized",
                       "lifecycle": life.snapshot(), "candidate": candidate.to_dict()})
        _finish(report, started)
        return EXIT_OK
    if not availability["ok"]:
        life.transition("blocked")
        report.update({"verdict": "BLOCKED", "reason": "no local patch model is serving",
                       "lifecycle": life.snapshot(), "candidate": candidate.to_dict()})
        _finish(report, started)
        return EXIT_BLOCKED

    life.transition("propose")
    pattern = _pattern_for(candidate, observation.get("patterns"))
    proposed = _propose_with_fallback(candidate, model, timeout=audit_timeout,
                                      pattern=pattern)
    report["phases"]["propose"] = {
        "ok": bool(proposed.get("ok")),
        "model": proposed.get("model", model),
        "attempts": proposed.get("attempts", []),
        "pattern_sites": int(pattern["count"]) if pattern else 0,
        "error": proposed.get("error", ""),
        "reply_head": proposed.get("reply_head", ""),
    }
    if not proposed.get("ok"):
        life.transition("blocked")
        report.update({"verdict": "BLOCKED", "reason": proposed.get("error", "proposal failed"),
                       "lifecycle": life.snapshot(), "candidate": candidate.to_dict()})
        _finish(report, started)
        return EXIT_BLOCKED
    try:
        rel, old, new = me.validate_substitution_patch(ROOT, proposed["patch"])
    except me.MetaEvolutionError as exc:
        life.transition("blocked")
        report.update({"verdict": "BLOCKED", "reason": f"patch contract: {exc}",
                       "lifecycle": life.snapshot(), "candidate": candidate.to_dict()})
        _finish(report, started)
        return EXIT_BLOCKED

    before_count = me.targeted_finding_count(observation["findings"], candidate)
    patch_path = PATCH_DIR / f"{candidate.id}.json"
    me.write_json_atomic(patch_path, proposed["patch"])
    target = ROOT / rel
    backup = target.read_bytes()
    report["phases"]["propose"].update({
        "patch_path": str(patch_path.relative_to(ROOT)).replace("\\", "/"),
        "target": rel, "old_length": len(old), "new_length": len(new),
        "targeted_before": before_count,
    })

    life.transition("evaluate")
    evaluated = _evaluate_with_self_modify(patch_path, band)
    report["phases"]["evaluate"] = {
        "verdict": evaluated.get("verdict"), "reason": evaluated.get("reason", ""),
        "band": band, "baseline": evaluated.get("baseline"),
        "tier1": evaluated.get("tier1"), "tier2": evaluated.get("tier2"),
    }

    # Re-scan independently, never infer the post-condition from the harness.
    post = _scan_source()
    post_candidate = me.Candidate(
        path=candidate.path, line=candidate.line, detector=candidate.detector,
        severity=candidate.severity, code=candidate.code, why=candidate.why,
        source_hash=me.file_sha256(ROOT / candidate.path))
    after_count = (me.targeted_finding_count(post["findings"], post_candidate)
                   if post["ok"] else before_count)
    t2 = evaluated.get("tier2") or {}
    evaluator_ok = evaluated.get("verdict") == "kept"
    retained, proof_reason = me.improvement_proved(
        before_count, after_count, evaluator_ok, t2)
    if evaluated.get("verdict") == "harness_error":
        # Do not let a broken evaluator present itself as a rejected candidate.
        proof_reason = str(evaluated.get("reason") or proof_reason)

    if retained:
        life.transition("retain")
        report["phases"]["retain"] = {
            "ok": True, "reason": proof_reason, "before": before_count,
            "after": after_count, "restored": [],
        }
    else:
        life.transition("revert")
        restored = _restore((rel,), {rel: backup})
        report["phases"]["revert"] = {
            "ok": True, "reason": proof_reason, "restored": restored,
            "before": before_count, "after": after_count,
        }

    life.transition("verify")
    final_observation = _scan_source()
    final_count = (me.targeted_finding_count(final_observation["findings"], post_candidate)
                   if final_observation["ok"] else before_count)
    final_hash = me.file_sha256(ROOT / rel)
    verified = (final_count == after_count
                and final_hash != _bytes_sha256(backup))
    if not retained:
        # Restoration is itself a verified end state.
        verified = final_count == before_count and final_hash == candidate.source_hash
    report["phases"]["verify"] = {
        "ok": bool(verified and final_observation["ok"]),
        "targeted_count": final_count,
        "source_hash": final_hash,
        "source_changed": final_hash != candidate.source_hash,
        "fresh_audit": final_observation["ok"],
    }
    if not report["phases"]["verify"]["ok"]:
        life.state = "done"
        report.update({"verdict": "FAIL", "reason": "independent postcondition verification failed",
                       "lifecycle": life.snapshot(), "candidate": candidate.to_dict()})
        _finish(report, started)
        return EXIT_FAILURE

    life.transition("done")
    report.update({
        "verdict": "KEPT" if retained else "REVERTED",
        "reason": proof_reason,
        "candidate": candidate.to_dict(),
        "patch_path": str(patch_path.relative_to(ROOT)).replace("\\", "/"),
        "lifecycle": life.snapshot(),
    })
    _log_proposal_outcome(candidate, report, retained, before_count,
                          after_count)
    _finish(report, started)
    return EXIT_OK if retained else EXIT_OK


def _log_proposal_outcome(candidate: me.Candidate, report: dict,
                          retained: bool, before_count: int,
                          after_count: int) -> None:
    """Append one ML-training row: proposal features -> outcome.

    This is the dataset the patch-success surrogate learns from (the "ML
    computes the angles" half): every evaluated proposal becomes a labeled
    row, so a future gate can predict success before spending model calls.
    Fail-open: logging must never break a cycle.
    """
    try:
        prop = report.get("phases", {}).get("propose", {})
        attempts = prop.get("attempts", []) or []
        row = {
            "at": _now(),
            "detector": candidate.detector,
            "severity": candidate.severity,
            "pattern_sites": int(prop.get("pattern_sites") or 0),
            "n_attempts": len(attempts),
            "models": [a.get("model") for a in attempts],
            "agreement": next((a.get("agreement") for a in attempts
                               if a.get("agreement")), ""),
            "contract_pass": True,
            "evaluator_kept": bool(
                report.get("phases", {}).get("evaluate", {}).get(
                    "verdict") == "kept"),
            "before": before_count,
            "after": after_count,
            "retained": bool(retained),
        }
        with open(STATE_DIR / "proposal_outcomes.jsonl", "a",
                   encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass


def _bytes_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


ESCALATION_PATH = STATE_DIR / "escalation.json"
ESCALATE_AFTER_BLOCKED = 3


def _consecutive_blocked(reason: str, limit: int = 25) -> int:
    """Count trailing BLOCKED lineage rows with the same reason (F-P0-2).

    Bounded read (last `limit` rows) so a long history cannot slow the cycle.
    """
    try:
        lines = LINEAGE_PATH.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return 0
    n = 0
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        # DRY (observation-only) rows are not attempts: skip, don't break.
        # A manual --no-apply run between apply runs must not reset a live
        # BLOCKED streak (self-inflicted 2026-09-26: my own DRY probe row).
        if row.get("verdict") == "DRY":
            continue
        if row.get("verdict") == "BLOCKED" and row.get("reason") == reason:
            n += 1
        else:
            break
    return n


def _maybe_escalate(report: dict[str, Any]) -> None:
    """Owner fallback for a stuck control plane (F-P0-2).

    Three consecutive BLOCKED verdicts on the same reason mean the loop is
    spinning without landing. The contract forbids silent downgrade, so the
    fallback is escalation, not auto-apply: a standing record naming the
    candidate + reason + required human action. Idempotent per streak.
    """
    if report.get("verdict") != "BLOCKED":
        return
    reason = str(report.get("reason", ""))
    # Lineage already holds this cycle's row (_finish appends first), so the
    # count includes the current verdict — no +1.
    streak = _consecutive_blocked(reason)
    report["blocked_streak"] = streak
    if streak < ESCALATE_AFTER_BLOCKED:
        return
    candidate = report.get("candidate") or {}
    escalation = {
        "schema": "azazael.meta_evolution.escalation.v1",
        "at": _now(),
        "consecutive_blocked": streak,
        "reason": reason,
        "candidate": candidate,
        "owner_action": (
            "Human review: either fix the flagged site directly with a "
            "material substitution + test, retire the detector finding as "
            "invalid with written rationale, or adjust the patch contract. "
            "Do not re-run --apply expecting a different model patch."
        ),
    }
    me.write_json_atomic(ESCALATION_PATH, escalation)
    try:
        rel_esc = str(ESCALATION_PATH.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        rel_esc = str(ESCALATION_PATH)
    report["escalation"] = rel_esc


def _finish(report: dict[str, Any], started: float) -> None:
    report["elapsed_s"] = round(time.time() - started, 2)
    me.write_json_atomic(REPORT_PATH, report)
    # Stamp the flat candidate_id on the lineage row so _attempted_ids() can
    # see it -- the schema mismatch above made the exclusion guard unrunnable.
    lineage_row = dict(report)
    candidate = lineage_row.get("candidate")
    if isinstance(candidate, dict) and candidate.get("id"):
        lineage_row["candidate_id"] = str(candidate["id"])
    _append_lineage(lineage_row)
    _maybe_escalate(report)
    # Re-persist so the escalation/streak lands in current.json, not just memory.
    me.write_json_atomic(REPORT_PATH, report)
    _record_direct_memory(report)


def _write_proof() -> int:
    proof = me.architecture_snapshot(ROOT)
    me.write_json_atomic(PROOF_PATH, proof)
    print("meta-evolution proof: %s" % PROOF_PATH)
    print("components=%d/%d functions=%d scheduled=%d test_only=%d uncalled=%d" % (
        proof["summary"]["existing_components"], proof["summary"]["components"],
        proof["summary"]["functions_enumerated"], proof["summary"]["scheduled_components"],
        proof["summary"]["test_only_functions"], proof["summary"]["uncalled_functions"]))
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="permit one bounded source mutation (default: observe only)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=1,
                        help="maximum proposals this invocation (currently bounded to 3)")
    parser.add_argument("--proof", action="store_true",
                        help="regenerate the current architecture/reachability proof only")
    args = parser.parse_args(argv)
    if args.proof:
        return _write_proof()
    exit_code = run_cycle(apply=args.apply, model=args.model, limit=args.limit)
    report = _load_json(REPORT_PATH, {})
    print(json.dumps(report, indent=2, sort_keys=True))
    return int(exit_code)


if __name__ == "__main__":
    sys.exit(main())