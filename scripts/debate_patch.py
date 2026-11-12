#!/usr/bin/env python3
# coding: utf-8
"""debate_patch.py — adversarial patch debate over local models (RCR pattern).

A patch proposal is debated before it reaches the contract gate:
  1. PROPOSE (qwythos-9b): exact substitution for the finding, with context.
  2. CRITIQUE (ornith-9b): sees the proposal; names its single worst flaw
     (or approves). Cannot propose its own patch.
  3. REVISE (proposer): new substitution addressing the critique, or defend
     unchanged (must say why). Max 2 rounds; unchanged patch stops the loop.
  4. JUDGE (bounded options APPLY/REJECT + rationale). Agreement between
     critic verdict and judge is computed in Python, not asserted.

Design debt to the literature: critic-sees-proposal (specific rebuttals),
bounded judge options, deterministic tally/confidence, degeneration guard
(new patch or convergence per round), strict-JSON with repair. The model
only ARGUES — contract validation + test band (meta cycle) still decide.

Usage:
    python scripts/debate_patch.py --path src/X.py --line 83
        --detector assert_true --code "assert True" --why "..."
    python scripts/debate_patch.py --finding findings.json
    (finding: {path,line,detector,severity,code,why})

Output: transcript JSONL to state/debate_transcripts/<id>.jsonl; final patch
printed as JSON {subs:{path:[[old,new]]}} on --emit-patch. Exit 0 agreed,
1 rejected/no-convergence, 2 model unavailable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state"
TRANSCRIPT_DIR = STATE_DIR / "debate_transcripts"

OLLAMA_URL = os.environ.get("AZAZAEL_META_OLLAMA", "http://127.0.0.1:11434").rstrip("/")
# 2026-09-28: qwythos-9b / ornith-9b are not installed in Ollama (only
# eve-v2u, sam860/olmoe-1b-7b-0924, embed models) -> /api/chat 404 BLOCKED.
# Retargeted to installed chat models; critic differs from proposer/judge
# so the adversarial check is still cross-model. Env overrides still win.
PROPOSER_MODEL = os.environ.get("AZAZAEL_DEBATE_PROPOSER", "eve-v2u")
CRITIC_MODEL = os.environ.get("AZAZAEL_DEBATE_CRITIC", "sam860/olmoe-1b-7b-0924:latest")
JUDGE_MODEL = os.environ.get("AZAZAEL_DEBATE_JUDGE", "eve-v2u")
MAX_ROUNDS = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chat(model: str, system: str, user: str, temperature: float,
          timeout: int, num_ctx: int = 8192) -> str:
    """One Ollama chat call, returns raw content text. Raises on failure."""
    body = {"model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False, "think": False, "keep_alive": "30m",
            "options": {"temperature": temperature, "num_ctx": num_ctx}}
    req = urllib.request.Request(
        OLLAMA_URL + "/api/chat", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    return str((payload.get("message") or {}).get("content", ""))


def _extract_json(text: str) -> dict:
    """First complete JSON object in text (raw_decode, not slice)."""
    dec = json.JSONDecoder()
    i = text.find("{")
    while i >= 0:
        try:
            obj, _ = dec.raw_decode(text[i:])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        i = text.find("{", i + 1)
    raise ValueError("no JSON object in reply")


def normalize_patch(patch: dict, source_path: str | None = None) -> tuple[str, str]:
    """Canonical (old, new) pair from a proposal dict. Raises on shape.

    Accepts line-range form {"lines":[a,b],"new":...} (1-based, max 15
    lines): old text is extracted from the file by Python, since models
    miscount repeated lines but line numbers don't repeat. Needs
    source_path for that form.
    """
    if "lines" in patch and "old" not in patch:
        if not source_path:
            raise ValueError("line-range form needs source_path")
        a, b = int(patch["lines"][0]), int(patch["lines"][1])
        ftext = ROOT.joinpath(source_path).read_text(
            encoding="utf-8", errors="replace").splitlines()
        if not (1 <= a <= b <= len(ftext)) or (b - a) > 14:
            raise ValueError("line range out of bounds or over 15 lines")
        return "\n".join(ftext[a - 1:b]), str(patch.get("new", ""))
    old = str(patch.get("old", ""))
    new = str(patch.get("new", ""))
    if not old:
        raise ValueError("empty old text")
    return old, new


def patches_equal(a: dict, b: dict, source_path: str | None = None) -> bool:
    """Degeneration-guard comparison: same substitution = converged."""
    try:
        return normalize_patch(a, source_path) == normalize_patch(b, source_path)
    except ValueError:
        # Un-normalizable pair: equal only if literally identical dicts.
        return a == b


def agreement(critic_verdict: str, judge_decision: str) -> str:
    """Deterministic agreement label (Python, never model-asserted)."""
    cv = (critic_verdict or "").strip().lower()
    jd = (judge_decision or "").strip().upper()
    approve = (cv == "approve" and jd == "APPLY")
    reject = (cv != "approve" and jd == "REJECT")
    if approve or reject:
        return "unanimous"
    return "split"


def derive_confidence(agree: str) -> tuple[str, float]:
    if agree == "unanimous":
        return "HIGH", 1.0
    return "LOW", 0.33


def _context(path: str, line: int, radius: int = 40) -> str:
    lines = (ROOT / path).read_text(
        encoding="utf-8", errors="replace").splitlines()
    lo = max(0, line - 1 - radius)
    hi = min(len(lines), line + radius)
    return "\n".join(f"{i + 1:5d}: {lines[i]}" for i in range(lo, hi))


def propose(finding: dict, context: str, prior: dict | None,
            critique: str, timeout: int) -> dict:
    system = (
        "You write exact source substitutions. Reply with exactly one JSON "
        "object, no markdown, in ONE of two forms. Form A: {\"old\":\"exact "
        "substring\",\"new\":\"replacement\",\"note\":\"why\"} with old "
        "occurring exactly once. Form B (PREFERRED when the line repeats): "
        "{\"lines\":[start,end],\"new\":\"replacement\",\"note\":\"why\"} "
        "with 1-based lines spanning the full unique block (max 15). "
        "If the task says PATTERN with N sites, add \"repeat\":true to fix "
        "all of them with one pair. "
        "Change the minimum that fixes the finding.")
    if prior is None:
        user = ("File: %(path)s Line %(line)s Detector: %(detector)s\n"
                "Why suspect: %(why)s\nCode: %(code)s\n\nContext:\n%(ctx)s\n\n"
                "Propose the smallest exact substitution." % {
                    "path": finding["path"], "line": finding["line"],
                    "detector": finding["detector"], "why": finding["why"],
                    "code": finding["code"], "ctx": context})
    else:
        user = ("Your previous proposal was critiqued. Either revise the "
                "substitution to address the critique, or keep it and defend "
                "it (set note to DEFEND: <reason>). If the critique says the "
                "old text is not unique, WIDEN it with surrounding context "
                "lines until it occurs exactly once — never narrow the fix "
                "to a line the file repeats. A vacuous test must gain a real "
                "assertion, never become a vacuous failure.\n\nCritique: %s\n\n"
                "Context:\n%s\nPrevious old: %s\nPrevious new: %s" % (
                    critique, context, prior.get("old"), prior.get("new")))
    return _extract_json(_chat(PROPOSER_MODEL, system, user, 0.5, timeout))


def _proposal_text(proposal: dict) -> tuple[str, str]:
    """Render old/new for critic/judge prompts, either patch form."""
    if "lines" in proposal and "old" not in proposal:
        return (f"lines {proposal.get('lines')} (range form)",
                str(proposal.get("new"))[:400])
    return str(proposal.get("old"))[:400], str(proposal.get("new"))[:400]


def critique(finding: dict, proposal: dict, timeout: int) -> dict:
    system = (
        "You are the CRITIC. You do NOT answer and do NOT propose your own "
        "patch. Pressure-test the proposal below: name its single most "
        "important flaw (wrong location, non-unique old text, behavior "
        "change beyond the finding, no-op), or approve. Reply with exactly "
        "one JSON object, no markdown: {\"verdict\":\"approve\"|\"flawed\","
        "\"flaw\":\"...\", \"criterion\":\"uniqueness|locality|behavior\"}.")
    user = ("Finding: %(det)s at %(path)s:%(line)s — %(why)s\n"
            "Proposal old: %(old)r\nProposal new: %(new)r\nNote: %(note)s" % {
                "det": finding["detector"], "path": finding["path"],
                "line": finding["line"], "why": finding["why"],
                "old": _proposal_text(proposal)[0],
                "new": _proposal_text(proposal)[1],
                "note": str(proposal.get("note", ""))[:200]})
    return _extract_json(_chat(CRITIC_MODEL, system, user, 0.3, timeout))


def judge(finding: dict, proposal: dict, crit: dict, timeout: int) -> dict:
    system = (
        "You are the JUDGE. Options, exactly one: APPLY (the substitution "
        "fixes the finding, old text unique, no scope creep) or REJECT. "
        "Reply with exactly one JSON object, no markdown: "
        "{\"decision\":\"APPLY\"|\"REJECT\",\"reason\":\"...\"}.")
    user = ("Finding: %(det)s at %(path)s:%(line)s — %(why)s\n"
            "Final proposal old: %(old)r\nFinal proposal new: %(new)r\n"
            "Critic verdict: %(cv)s — %(flaw)s" % {
                "det": finding["detector"], "path": finding["path"],
                "line": finding["line"], "why": finding["why"],
                "old": _proposal_text(proposal)[0],
                "new": _proposal_text(proposal)[1],
                "cv": crit.get("verdict"), "flaw": str(crit.get("flaw", ""))[:300]})
    out = _extract_json(_chat(JUDGE_MODEL, system, user, 0.0, timeout))
    if out.get("decision") not in ("APPLY", "REJECT"):
        raise ValueError("judge decision outside bounded options")
    return out


def _old_occurrences(path: str, old: str) -> int:
    """Python-side uniqueness count (never ask the model to count)."""
    try:
        text = ROOT.joinpath(path).read_text(encoding="utf-8", errors="replace")
        return text.count(old) if old else -1
    except OSError:
        return -1


def run_debate(finding: dict, timeout: int = 590, transcript: list | None = None,
               max_rounds: int = MAX_ROUNDS) -> dict:
    """Full debate. Returns {patch|None, transcript, confidence, agreement}."""
    log: list[dict] = transcript if transcript is not None else []
    context = _context(finding["path"], int(finding["line"]))

    def emit(role: str, body: dict):
        log.append({"at": _now(), "role": role, **body})

    proposal = propose(finding, context, None, "", timeout)
    # Deterministic uniqueness pre-check (not a model judgment): a
    # single-shot-shaped old text that repeats is bounced back to the
    # proposer ONCE with an explicit lines-form demand, instead of burning
    # a critic round on a known-bad shape.
    try:
        _o, _ = normalize_patch(proposal, finding["path"])
        _n_occ = _old_occurrences(finding["path"], _o)
    except ValueError:
        _n_occ = -1
    if _n_occ > 1:
        emit("uniqueness-bounce",
             {"occurrences": _n_occ,
              "note": "old text repeats; demanding lines form"})
        proposal = propose(
            finding, context, proposal,
            "Your old text occurs %d times — you MUST use Form B "
            "{\"lines\":[start,end]} spanning the full unique block. "
            "A bare repeated line will be rejected." % _n_occ, timeout)
        emit("propose", {"proposal": proposal, "after_bounce": True})
    else:
        emit("propose", {"proposal": proposal})
    crit = critique(finding, proposal, timeout)
    emit("critique", {"critique": crit})
    rounds = 1
    current, current_crit = proposal, crit
    for _ in range(max_rounds - 1):
        if current_crit.get("verdict") == "approve":
            break
        revised = propose(finding, context, current,
                          str(current_crit.get("flaw", "")), timeout)
        emit("revise", {"proposal": revised})
        rounds += 1
        if patches_equal(current, revised, finding["path"]):
            current = revised
            break  # converged: degeneration guard stops the loop
        current = revised
        current_crit = critique(finding, current, timeout)
        emit("critique", {"critique": current_crit})
    # Pre-judge shape guard (2026-09-28): a malformed proposal must never
    # reach the judge — bounce it to the proposer ONCE with an explicit
    # shape demand, mirroring the uniqueness-bounce above. One extra model
    # call max; if still malformed the post-judge guard still rejects.
    try:
        normalize_patch(current, finding["path"])
    except ValueError as exc:
        emit("shape-bounce", {"error": str(exc)[:200],
                              "note": "proposal malformed; demanding valid shape"})
        current = propose(
            finding, context, current,
            "Your patch shape was rejected (%s) — emit ONLY valid JSON: "
            "either {\"old\": \"<exact source text, non-empty>\", \"new\": "
            "\"<replacement>\"} or {\"lines\": [start, end], \"new\": "
            "\"<replacement>\"} with 1-based lines spanning at most 15 lines. "
            "Empty old text is forbidden." % str(exc)[:120], timeout)
        emit("propose", {"proposal": current, "after_shape_bounce": True})
        current_crit = critique(finding, current, timeout)
        emit("critique", {"critique": current_crit})
    verdict = judge(finding, current, current_crit, timeout)
    emit("judge", {"verdict": verdict})
    agree = agreement(str(current_crit.get("verdict")),
                      str(verdict.get("decision")))
    conf, ratio = derive_confidence(agree)
    patch = None
    if verdict.get("decision") == "APPLY":
        try:
            old, new = normalize_patch(current, finding["path"])
            patch = {"subs": {finding["path"]: [[old, new]]}}
            # Form C passthrough: pattern idiom fixed at every site. The
            # meta validator (not the debate) verifies the repeat count
            # against the file (2..MAX_REPEAT_SITES).
            if current.get("repeat") is True:
                patch["repeat"] = True
        except ValueError:
            verdict = {"decision": "REJECT",
                       "reason": "winning patch malformed on normalize"}
            agree, (conf, ratio) = "split", derive_confidence("split")
    return {"patch": patch, "transcript": log, "rounds": rounds,
            "agreement": agree, "confidence": conf, "ratio": ratio,
            "verdict": verdict}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="debate_patch")
    ap.add_argument("--path", help="file under test")
    ap.add_argument("--line", type=int, default=0)
    ap.add_argument("--detector", default="")
    ap.add_argument("--severity", default="MED")
    ap.add_argument("--code", default="")
    ap.add_argument("--why", default="")
    ap.add_argument("--finding", help="JSON finding file")
    ap.add_argument("--rounds", type=int, default=MAX_ROUNDS)
    ap.add_argument("--timeout", type=int, default=590)
    ap.add_argument("--emit-patch", action="store_true")
    ap.add_argument("--out", default="",
                    help="transcript JSONL path (default state/debate_transcripts/<id>.jsonl)")
    args = ap.parse_args(argv)

    if args.finding:
        finding = json.loads(Path(args.finding).read_text(encoding="utf-8"))
    else:
        if not args.path:
            print("need --path or --finding")
            return 2
        finding = {"path": args.path, "line": args.line,
                   "detector": args.detector, "severity": args.severity,
                   "code": args.code, "why": args.why}
    try:
        result = run_debate(finding, timeout=args.timeout,
                            max_rounds=args.rounds)
    except Exception as exc:
        print(f"debate failed: {exc}")
        return 2
    fid = hashlib.sha256(json.dumps(
        finding, sort_keys=True).encode()).hexdigest()[:12]
    out_path = Path(args.out) if args.out else (
        TRANSCRIPT_DIR / f"{fid}.jsonl")
    if not args.out:
        # Never overwrite a prior debate on the same finding: version up.
        stem, n = out_path.stem, 2
        while out_path.exists():
            out_path = out_path.with_name(f"{stem}-r{n}.jsonl")
            n += 1
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            for row in result["transcript"]:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"transcript: {out_path}")
    except OSError as exc:
        print(f"transcript unwritable: {exc}")
    print(f"rounds={result['rounds']} agreement={result['agreement']} "
          f"confidence={result['confidence']}")
    if result["patch"] is None:
        print(f"REJECTED: {result['verdict'].get('reason')}")
        return 1
    if args.emit_patch:
        print(json.dumps(result["patch"]))
    else:
        print(f"APPLY: {result['verdict'].get('reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
