#!/usr/bin/env python
# coding: utf-8
"""second_opinion -- an executable replacement for the review-agent clauses.

WHY THIS EXISTS
RULE 0V requires code-reviewer-deepseek before and after every code change, RULE
0G requires thinker-with-files-gemini at every major phase, RULE 0Z requires ten
spawnable agents. Measured 2026-10-31: `spawn_agents` has no implementation
anywhere in this repo -- the only occurrences are a docstring in
`src/SCRIPTS/sentinel_gate.py:27` and a PolicyRule in
`src/CORE/compliance/policy.py:548` -- and the ten agents are Codebuff-client
names. So those clauses could not be executed by any lane, only recited, which is
exactly how they decayed into prose.

Deleting them would remove a real defence. The clauses exist to stop a specific
failure: an agent substituting its own judgement for an independent one and then
approving its own work. That defence does not need an agent; it needs a SECOND
MODEL. This tool supplies one, and records the verdict.

THE RULE IT ENFORCES
The reviewer must differ from the author in PROVIDER **and** MODEL FAMILY. A
"review" by the same model family is not a second opinion, it is a re-read: it
shares the blind spots that produced the change. Families here are a heuristic
label, not a vendor guarantee, and are named as such in the output.

WHAT IT DOES NOT DO
It does not decide. It returns the critique and a machine-readable verdict, and
writes a row per call. A caller that reports "reviewed" without a row in the log
has not had a review. Network/provider failure is recorded as `status: error`
with the provider's own message -- a failed review is never silently a pass.

USAGE
  python tools/second_opinion.py --check                     # candidates, no calls
  python tools/second_opinion.py --self-test                 # offline wiring test
  python tools/second_opinion.py --text "plan: ..."          # review a plan
  python tools/second_opinion.py --file src/CORE/x.py        # review a change
  python tools/second_opinion.py --diff                      # review git diff
  python tools/second_opinion.py --file x.py --author-provider groq --provider gemini
  python tools/second_opinion.py --log                       # verdict history
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

LOG_PATH = Path.home() / ".azazael" / "second_opinion_log.jsonl"

#: provider -> model family label. Heuristic, kept explicit so a wrong label is
#: visible instead of implied. A reviewer from the same label is refused.
FAMILIES: Dict[str, str] = {
    "groq": "meta-llama", "cerebras": "meta-llama",
    "nvidia_nim": "meta-llama",
    "gemini": "google", "gemini_native": "google",
    "mistral": "mistral", "mistral_fim_native": "mistral",
    "cohere": "cohere", "cohere_native": "cohere",
    "zhipu": "glm",
    "siliconflow": "qwen",
    "github": "openai",
    "openrouter": "multi-vendor",
    "huggingface": "multi-vendor", "huggingface_direct": "multi-vendor",
    "huggingface_native": "multi-vendor",
    "cloudflare": "multi-vendor", "pollinations": "multi-vendor",
    "ovhcloud": "multi-vendor", "edge0": "edge0",
    "ollama": "local", "ollama_native": "local", "lmstudio": "local",
    "llamaserver": "local", "llama_cpp_direct": "local", "openvino": "local",
    "llama_mythos": "local", "llama_mythos_v2": "local",
    "llama_ornith": "local", "llama_qwopus": "local",
    "accelerator_sidecar": "local", "cloudflare_workers": "multi-vendor",
}

REVIEW_SYSTEM = (
    "You are an independent reviewer. You did not write this and you are not "
    "being asked to agree with it. Find what is WRONG: unverified claims, "
    "missing evidence, unreachable code paths, silent failures, scope creep, and "
    "contradictions with the stated requirement. If it is sound, say so plainly "
    "and briefly. Answer as JSON only, with keys: verdict (accept|revise|reject), "
    "risk (low|medium|high), defects (array of one-line strings), "
    "rationale (one short paragraph). Do not add prose outside the JSON."
)


def _providers():
    from CORE.inference import providers as p
    return p


def family_for(provider: str) -> str:
    return FAMILIES.get(provider, "unlabelled")


def candidates(author_provider: str) -> List[Dict[str, Any]]:
    """Configured providers whose family differs from the author's."""
    p = _providers()
    author_family = family_for(author_provider) if author_provider else ""
    out = []
    for name, cfg in sorted(p.PROVIDER_REGISTRY.items()):
        fam = family_for(name)
        try:
            configured = bool(p.provider_credential_configured(name))
        except Exception:
            configured = False
        out.append({
            "provider": name,
            "family": fam,
            "configured": configured,
            "model": getattr(cfg, "model", "") or "",
            "eligible": configured and fam != author_family,
        })
    return out


def _payload(args, text: str) -> str:
    where = args.file or ("git diff" if args.diff else "inline text")
    return (f"REVIEW TARGET: {where}\n"
            f"STATED REQUIREMENT (if any): {args.requirement or '(not supplied)'}\n"
            f"---\n{text}\n---\n"
            "Review the target above against the stated requirement.")


def _extract_json(reply: str) -> Optional[Dict[str, Any]]:
    """Pull the verdict out of a reply, tolerating fenced or chatty output."""
    if not reply:
        return None
    m = re.search(r"\{.*\}", reply, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        note_failure("tools/second_opinion.py", exc)
        return None
    if not isinstance(obj, dict):
        return None
    verdict = str(obj.get("verdict", "")).lower()
    obj["verdict"] = verdict if verdict in ("accept", "revise", "reject") else "unknown"
    return obj


def _append_log(row: Dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def review(text: str, args) -> Dict[str, Any]:
    p = _providers()
    author = args.author_provider or os.environ.get("AZAZAEL_AUTHOR_PROVIDER", "")
    pool = [c for c in candidates(author) if c["eligible"]]
    if args.provider:
        chosen = next((c for c in pool if c["provider"] == args.provider), None)
        if chosen is None:
            reason = ("not configured" if not any(
                c["provider"] == args.provider and c["configured"] for c in
                candidates(author)) else
                f"same family as author ({family_for(args.provider)})")
            return {"status": "refused", "reason": f"{args.provider}: {reason}",
                    "author_provider": author}
    else:
        # Prefer a family the author is least likely to share blind spots with.
        prefer = ("google", "mistral", "cohere", "glm", "qwen", "meta-llama",
                  "multi-vendor", "local")
        chosen = min(pool, key=lambda c: prefer.index(c["family"])
                     if c["family"] in prefer else len(prefer)) if pool else None
    if chosen is None:
        return {"status": "unavailable",
                "reason": "no configured provider outside the author's family",
                "author_provider": author}

    prompt = _payload(args, text)
    row: Dict[str, Any] = {
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "author_provider": author,
        "author_family": family_for(author) if author else "unset",
        "reviewer_provider": chosen["provider"],
        "reviewer_family": chosen["family"],
        "target": args.file or ("git-diff" if args.diff else "inline"),
        "requirement": args.requirement or "",
        "input_chars": len(text),
    }
    try:
        prov = p.get_provider(chosen["provider"])
        model = args.model or chosen["model"] or None
        resp = prov.chat(prompt, system_prompt=REVIEW_SYSTEM, model=model,
                         max_tokens=900, temperature=0.2)
    except Exception as exc:
        row.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        _append_log(row)
        return row


try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

    if not isinstance(resp, dict) or resp.get("error"):
        row.update({"status": "error",
                    "error": str((resp or {}).get("error", "no response"))})
        _append_log(row)
        return row

    content = resp.get("content") or resp.get("text") or ""
    verdict = _extract_json(content)
    row.update({"status": "ok", "model": resp.get("model") or (args.model or chosen["model"]),
                "elapsed_ms": resp.get("elapsed_ms"),
                "verdict": (verdict or {}).get("verdict", "unparsed"),
                "risk": (verdict or {}).get("risk", ""),
                "defects": (verdict or {}).get("defects", []),
                "rationale": (verdict or {}).get("rationale", "")[:800],
                "reply_chars": len(content)})
    if verdict is None:
        row["status"] = "unparsed"
        row["reply_head"] = content[:400]
    _append_log(row)
    return row


def self_test() -> int:
    """Offline: the wiring must hold without a network call."""
    problems = []
    # 1. families are declared for the providers that matter
    for need in ("groq", "gemini", "mistral", "nvidia_nim", "openrouter"):
        if FAMILIES.get(need) in (None, "unlabelled"):
            problems.append(f"no family label for {need}")
    # 2. same-family refusal is real
    same = [c for c in candidates("groq") if c["configured"]]
    if any(c["provider"] == "groq" and c["eligible"] for c in same):
        problems.append("groq considered eligible as its own reviewer")
    # 3. json extraction survives fences and chatter
    cases = [
        ('{"verdict":"reject","risk":"high","defects":["x"],"rationale":"r"}', "reject"),
        ('```json\n{"verdict":"ACCEPT","risk":"low","defects":[],"rationale":"ok"}\n```', "accept"),
        ('Sure! {"verdict":"revise"} thanks', "revise"),
        ('{"verdict":"maybe"}', "unknown"),
    ]
    for reply, want in cases:
        got = (_extract_json(reply) or {}).get("verdict")
        if got != want:
            problems.append(f"extract {reply[:24]!r} -> {got!r}, want {want!r}")
    if _extract_json("no json here") is not None:
        problems.append("extraction invented a verdict from plain prose")
    # 4. payload carries the target and the requirement
    class A:
        file = "src/x.py"; diff = False; requirement = "must fail on empty"
    body = _payload(A(), "code")
    for needle in ("src/x.py", "must fail on empty", "code"):
        if needle not in body:
            problems.append(f"payload missing {needle!r}")
    print(f"second_opinion self-test: {len(problems)} problem(s)")
    for pr in problems:
        print(f"  FAIL {pr}")
    return 0 if not problems else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--text")
    src.add_argument("--file")
    src.add_argument("--diff", action="store_true")
    ap.add_argument("--requirement", default="", help="what the change is supposed to do")
    ap.add_argument("--author-provider", default="",
                    help="the provider/model that produced the change")
    ap.add_argument("--provider", default="", help="force a reviewer")
    ap.add_argument("--model", default="")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.log:
        if not LOG_PATH.exists():
            print(f"no log at {LOG_PATH}")
            return 0
        for line in LOG_PATH.read_text(encoding="utf-8").splitlines()[-20:]:
            r = json.loads(line)
            print(f"{r.get('at','')} {r.get('status',''):10} "
                  f"{r.get('reviewer_provider',''):12} {r.get('verdict','')}"
                  f"{' risk=' + r['risk'] if r.get('risk') else ''}")
        return 0
    if args.check:
        author = args.author_provider or os.environ.get("AZAZAEL_AUTHOR_PROVIDER", "")
        rows = candidates(author)
        elig = [r for r in rows if r["eligible"]]
        print(f"author provider: {author or '(unset)'}  family: "
              f"{family_for(author) if author else '(any)'}")
        print(f"declared: {len(rows)}  configured: {sum(1 for r in rows if r['configured'])}"
              f"  eligible as reviewer: {len(elig)}")
        for r in rows:
            mark = "ELIGIBLE" if r["eligible"] else (
                "same-family" if r["configured"] else "unconfigured")
            print(f"  {r['provider']:18} {r['family']:13} {mark}")
        return 0 if elig else 1

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    elif args.diff:
        text = subprocess.run(["git", "diff"], cwd=str(ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=120).stdout
        if not text.strip():
            text = subprocess.run(["git", "diff", "--cached"], cwd=str(ROOT),
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=120).stdout
    elif args.text:
        text = args.text
    else:
        ap.print_help()
        return 2
    if not text.strip():
        print("nothing to review (empty target)", file=sys.stderr)
        return 2

    row = review(text, args)
    if args.json:
        print(json.dumps(row, indent=2))
    else:
        print(f"status   : {row.get('status')}")
        print(f"reviewer : {row.get('reviewer_provider')} ({row.get('reviewer_family')})"
              f"  author: {row.get('author_provider') or '(unset)'}")
        if row.get("verdict"):
            print(f"verdict  : {row['verdict']}  risk: {row.get('risk','')}")
            for d in row.get("defects") or []:
                print(f"  defect : {d}")
            if row.get("rationale"):
                print(f"rationale: {row['rationale']}")
        if row.get("error"):
            print(f"error    : {row['error']}")
        if row.get("reason"):
            print(f"reason   : {row['reason']}")
        print(f"log      : {LOG_PATH}")
    return 0 if row.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
