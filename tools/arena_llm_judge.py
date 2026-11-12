#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Arena judge swarm that actually asks a model.

``tools/arena_judge_swarm.py`` declares six judge prompts and then executes six
pure-Python keyword heuristics: the ``prompt_template`` fields are never sent
anywhere.  A covenant citing "6-judge arena swarm" against that file is citing
its own rubric names, not a model's judgment.  This module closes that gap
without forking the rubric: it imports ``JUDGES`` from ``arena_judge_swarm`` and
puts the declared angle in front of a local generative model for each evidence
pin.

What it judges is an evidence packet (``ARC/cain/evidence_packet.json``) whose
quotes were sliced from bytes saved under ``ARC/cain/raw/`` and pinned by
sha256.  The judge sees the claim, the source status, the tier, and the quote;
it never sees the mark's prose.

Honesty constraints, encoded here rather than promised in a README:

* **One model, six angles is not six independent judges.**  The run records the
  model path and says so in ``meta``.  Six personas reduce single-prompt bias;
  they do not give six independent readings.
* **A judge that cannot answer is BLIND, not a vote.**  Unparseable output, a
  timeout, or a refusal is recorded with its raw text and excluded from the
  tally; a pin with any BLIND row is graded BLIND.
* **A verdict needs a quorum.**  SUPPORTED requires a supermajority; anything
  else keeps the pin below that grade.
* The vocabulary is SUPPORTED / PARTIAL / UNSUPPORTED / BLOCKED.  "Admissible"
  is a court's word, not this instrument's.

Usage::

    python tools/arena_llm_judge.py --packet ARC/cain/evidence_packet.json \
        --out ARC/cain/arena_llm_judgments_r1.json [--url http://127.0.0.1:8081]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from arena_judge_swarm import JUDGES

DEFAULT_URL = os.environ.get("ARC_JUDGE_URL", "http://127.0.0.1:8081")
DEFAULT_MODEL = os.environ.get("ARC_JUDGE_MODEL", "local")
QUORUM = 0.66


def _setting(key: str, fallback):
    """`config/defaults.json` through the shared reader, with a constant fallback.

    The endpoint, the worker count and the quorum were literals here, so a
    machine whose judge lives on another port -- or a slower one that needs a
    longer timeout -- had to edit the tool. They are knobs now (HED-5), and the
    env overlay means one machine's slower endpoint does not touch the others.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import defaults_profile            # noqa: PLC0415
        return defaults_profile.read_default(key, fallback)
    except Exception:
        return fallback

VERDICTS = ("SUPPORTED", "PARTIAL", "UNSUPPORTED", "BLOCKED")
STRENGTH = {"SUPPORTED": 3, "PARTIAL": 2, "UNSUPPORTED": 1, "BLOCKED": 0}

RUBRIC = """You are {judge_id}, judging EVIDENCE for an accountability finding.

Your angle: {angle}.
{angle_focus}

You are given ONE claim and the sources retrieved for it. Each source carries
its HTTP status, its tier (A = primary document, B = official page, C =
credible secondary, D = snippet), and a verbatim quote sliced from the bytes
that were saved. Judge only what the packet contains.

Rules:
1. The quote outranks the tier. A tier-A page whose quote never addresses the
   claim proves nothing.
2. A vendor's own statement can support a TECHNICAL mechanism ("the firmware
   does X"). It cannot support an INTENT claim ("they did X knowing Y") unless
   the quote itself states the intent.
3. An element of the claim with no supporting text in any quote is a missing
   element. List it. Do not fill it from general knowledge.
4. If a source is an error page, a script shell, or unrelated text, say
   BLOCKED for that source rather than inferring anything.
5. Never restate the claim as if it were evidence.

Verdicts:
- SUPPORTED: the quotes state every material element of the claim.
- PARTIAL: the quotes state some material elements and the rest are missing.
- UNSUPPORTED: the quotes do not state the material elements.
- BLOCKED: the sources could not be read, so nothing can be concluded.

Answer with JSON only, no prose, exactly these keys:
{{"verdict":"SUPPORTED|PARTIAL|UNSUPPORTED|BLOCKED","confidence":0.0,
"supports_elements":["..."],"missing_elements":["..."],
"best_quote":"<=30 words, copied from a quote above","reason":"one sentence"}}"""


def _post_json(url: str, payload: Dict[str, Any], timeout: float) -> Tuple[int, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, body
    except Exception as exc:  # noqa: BLE001 - the failure is the reading
        return 0, "%s: %s" % (type(exc).__name__, exc)


def probe_model(url: str) -> Dict[str, Any]:
    """Return the served model record, or raise. A dead judge is not a verdict."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/v1/models", timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001 - surface as BLIND upstream
        raise RuntimeError("judge endpoint %s unreachable: %s: %s" % (url, type(exc).__name__, exc))
    models = body.get("data") or body.get("models") or []
    if not models:
        raise RuntimeError("judge endpoint %s served no model" % url)
    first = models[0]
    caps = first.get("capabilities") or []
    if caps and "completion" not in caps:
        raise RuntimeError("model %s cannot complete (capabilities=%s)" % (first.get("name"), caps))
    return first


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Pull the first balanced JSON object out of a model reply."""
    fenced = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    for candidate in (fenced, text):
        start = candidate.find("{")
        while start != -1:
            depth = 0
            for idx in range(start, len(candidate)):
                if candidate[idx] == "{":
                    depth += 1
                elif candidate[idx] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(candidate[start:idx + 1])
                        except json.JSONDecodeError:
                            break
            start = candidate.find("{", start + 1)
    return None


def _elide(text: Any, width: int) -> str:
    """A cut string that SAYS it was cut.

    `normalise` used to slice a judge's quote and reason silently, so a row that
    quoted 300 of 900 characters was indistinguishable from a judge that wrote
    300. The marker is the difference between a short quote and a shortened one.
    """
    text = str(text)
    return text if len(text) <= width else text[:width] + "..."


def _elide_list(values: Any, width: int) -> Tuple[List[str], bool]:
    """(capped list, was_it_capped). The flag, not a fake "..." element, so no
    downstream count is inflated by a placeholder that is not an element."""
    items = [str(x) for x in (values or [])]
    return items[:width], len(items) > width


def normalise(parsed: Dict[str, Any]) -> Dict[str, Any]:
    verdict = str(parsed.get("verdict", "")).strip().upper()
    if verdict not in VERDICTS:
        return {"error": "verdict %r not in %s" % (verdict, VERDICTS)}
    try:
        confidence = float(parsed.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    supports, supports_cut = _elide_list(parsed.get("supports_elements"), 8)
    missing, missing_cut = _elide_list(parsed.get("missing_elements"), 8)
    out = {
        "verdict": verdict,
        "confidence": max(0.0, min(1.0, confidence)),
        "supports_elements": supports,
        "missing_elements": missing,
        "supports_truncated": supports_cut,
        "missing_truncated": missing_cut,
        "best_quote": _elide(parsed.get("best_quote", ""), 300),
        "reason": _elide(parsed.get("reason", ""), 400),
    }
    return out


def compact(pin: Dict[str, Any], quote_chars: int) -> Dict[str, Any]:
    """The view a judge needs: claim plus status, tier and a bounded quote.

    Full packet rows carry provenance the judge cannot use (raw filename, fetch
    method, full sha) and they cost prompt-processing time on every call.  The
    provenance stays in the packet; only the readable part is sent.
    """
    sources = []
    for ev in pin["evidence"]:
        src = {"url": ev["url"], "http_status": ev["status"], "tier": ev["tier"],
               "quote": (ev["quote"][:quote_chars] if quote_chars > 0 else ev["quote"])}
        # A gloss is an aid to reading, never a substitute for the quoted
        # original, so it travels beside the quote and keeps its label.
        if ev.get("gloss"):
            src["gloss"] = ev["gloss"]
            src["gloss_label"] = ev.get("gloss_label", "")
        sources.append(src)
    view: Dict[str, Any] = {
        "claim": pin["claim"],
        "vendor": pin["vendor"],
        "elements_to_check": pin.get("elements") or [],
        "sources": sources,
    }
    # A countable fact travels with its method.  Round 5 was built with this
    # field and this function dropped it, so six judges answered "missing"
    # about a measurement they were never shown: the run measured the view,
    # not the evidence.
    if pin.get("proof"):
        view["proof"] = pin["proof"]
    return view


def judge_pin(judge_id: str, spec: Dict[str, Any], pin: Dict[str, Any], url: str,
              model: str, timeout: float, quote_chars: int = 320,
              max_tokens: int = 300) -> Dict[str, Any]:
    system = RUBRIC.format(judge_id=judge_id, angle=spec["angle"],
                           angle_focus=spec["prompt_template"].strip())
    payload = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(compact(pin, quote_chars), ensure_ascii=False)},
        ],
    }
    # A reply cut off at the token budget yields no balanced JSON object.  That is
    # a budget defect, not a judge refusing to answer, so it is retried once at a
    # larger ceiling rather than recorded as BLIND.  Measured 2026-11-04: one C3
    # call returned "{\"verdict\": \"PARTIAL\" ... \"supports_elements\": [ \"el" and
    # was reported BLIND by a 320-token budget.
    budgets = [max_tokens, max_tokens + 320]
    content = ""
    status = 0
    body = ""
    started = time.time()
    for budget in budgets:
        payload["max_tokens"] = budget
        status, body = _post_json(url, payload, timeout)
        if status != 200:
            break
        try:
            envelope = json.loads(body)
            content = envelope["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            content = ""
            break
        if extract_json(content) is not None:
            break
    elapsed = round(time.time() - started, 2)
    row: Dict[str, Any] = {"judge": judge_id, "angle": spec["angle"], "pin": pin["pin"],
                           "http_status": status, "seconds": elapsed,
                           "max_tokens_used": payload["max_tokens"]}
    if status != 200:
        row.update({"grade": "BLIND", "error": "HTTP %s" % status, "raw": body[:600]})
        return row
    if not content:
        row.update({"grade": "BLIND", "error": "unreadable reply", "raw": body[:600]})
        return row
    parsed = extract_json(content)
    if parsed is None:
        row.update({"grade": "BLIND", "error": "no JSON object in reply after two budgets",
                    "raw": content[:600]})
        return row
    clean = normalise(parsed)
    if "error" in clean:
        row.update({"grade": "BLIND", "error": clean["error"], "raw": content[:600]})
        return row
    row.update(clean)
    row["grade"] = clean["verdict"]
    row["quote_is_grounded"] = any(
        clean["best_quote"][:40].lower() in ev["quote"].lower()
        for ev in pin["evidence"]
    ) if clean["best_quote"] else False
    return row


def grade_pin(rows: List[Dict[str, Any]], quorum: Optional[float] = None) -> Dict[str, Any]:
    """Aggregate one pin's votes using the production grading rules.

    ``quorum`` lets offline replay use the same grade semantics at candidate
    thresholds without mutating the judge's process-global configured default.
    """
    active_quorum = QUORUM if quorum is None else float(quorum)
    if active_quorum <= 0.5 or active_quorum > 1.0:
        raise ValueError("quorum must be in (0.5, 1.0]")
    blind = [r for r in rows if r.get("grade") == "BLIND"]
    votes = [r for r in rows if r.get("grade") in STRENGTH]
    tally = {v: sum(1 for r in votes if r["grade"] == v) for v in VERDICTS}
    base = {"votes": len(votes), "blind": len(blind), "tally": tally,
            "ungrounded_quotes": sum(1 for r in votes if not r.get("quote_is_grounded"))}
    if blind:
        return dict(base, grade="BLIND", reason="%d judge(s) could not answer" % len(blind))
    if not votes:
        return dict(base, grade="BLIND", reason="no judge answered")
    top = max(tally, key=lambda v: tally[v])
    share = tally[top] / len(votes)
    if top == "SUPPORTED" and share >= active_quorum:
        return dict(base, grade="SUPPORTED", reason="%d/%d judges SUPPORTED" % (tally[top], len(votes)))
    if STRENGTH[top] > 1 and share >= active_quorum:
        return dict(base, grade="PARTIAL", reason="%d/%d judges PARTIAL" % (tally[top], len(votes)))
    # A split vote is not a weak verdict, it is the ABSENCE of one. The previous
    # line reported `PARTIAL` whenever any judge said PARTIAL, so 3 UNSUPPORTED /
    # 2 PARTIAL / 1 BLOCKED -- 0.50 against a 0.66 quorum -- printed as a PARTIAL
    # grade with "no quorum" beside it (measured 2026-11-05, round r2, pin C4).
    # The reason contradicted the grade, and a reader takes the grade.
    return dict(base, grade="NO_QUORUM",
                reason="no quorum: %s (%.2f < %.2f needed)"
                       % (json.dumps(tally), share, QUORUM))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="LLM arena judge over the CAIN evidence packet")
    ap.add_argument("--packet", default="ARC/cain/evidence_packet.json")
    ap.add_argument("--out", default="ARC/cain/arena_llm_judgments.json")
    # Every one of these is a knob now; the constant stays as the shipped
    # fallback so a missing config degrades instead of crashing (HED-5).
    global QUORUM
    QUORUM = float(_setting("arena.quorum", QUORUM))
    ap.add_argument("--url", default=str(_setting("arena.endpoint", DEFAULT_URL)))
    ap.add_argument("--timeout", type=float,
                    default=float(_setting("arena.timeout_s", 180.0)))
    ap.add_argument("--workers", type=int,
                    default=int(_setting("arena.workers", 3)))
    ap.add_argument("--round", default="r1")
    ap.add_argument("--quote-chars", type=int,
                    default=int(_setting("arena.quote_chars", 320)))
    ap.add_argument("--max-tokens", type=int,
                    default=int(_setting("arena.max_tokens", 300)))
    ap.add_argument("--pins", default="", help="re-judge only these pins, e.g. C3")
    ap.add_argument("--checkpoint", default="",
                    help="append each finished row here as JSONL, so a timeout leaves a partial artifact")
    ap.add_argument("--regrade", action="store_true",
                    help="re-derive the pin grades from an existing --out artifact's rows "
                         "without calling the model (a rule change, not a re-judge)")
    args = ap.parse_args(argv)

    if args.regrade:
        # The rows are the evidence and the tally is a rule applied to them, so a
        # rule change must not require 42 fresh model calls. This is also the only
        # honest way to show a corrected rule against the SAME votes.
        path = Path(args.out)
        if not path.exists():
            print("regrade: %s does not exist -- nothing to re-derive" % path)
            return 2
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("rows") or []
        if not rows:
            print("regrade: %s holds no rows -- nothing to re-derive" % path)
            return 2
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row.get("pin"), []).append(row)
        data["pins"] = {pin_id: grade_pin(pin_rows)
                        for pin_id, pin_rows in grouped.items()}
        data.setdefault("meta", {})["regraded_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        data["meta"]["quorum"] = QUORUM
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        for pin_id, grade in data["pins"].items():
            print("  %-3s %-11s %s" % (pin_id, grade["grade"], grade["reason"]))
        return 0 if all(g["grade"] not in ("BLIND", "NO_QUORUM")
                        for g in data["pins"].values()) else 1

    try:
        model_rec = probe_model(args.url)
    except RuntimeError as exc:
        print("BLIND: %s" % exc)
        return 2
    model = model_rec.get("name") or model_rec.get("model") or DEFAULT_MODEL

    packet_path = Path(args.packet)
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    pins = packet["pins"]
    if args.pins:
        wanted = {p.strip().upper() for p in args.pins.split(",") if p.strip()}
        pins = [p for p in pins if p["pin"].upper() in wanted]
        if not pins:
            print("no pin matched --pins %s" % args.pins)
            return 2

    jobs = [(jid, spec, pin) for pin in pins for jid, spec in JUDGES.items()]
    rows: List[Dict[str, Any]] = []
    checkpoint = Path(args.checkpoint) if args.checkpoint else None
    if checkpoint is not None:
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text("", encoding="utf-8")
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(judge_pin, jid, spec, pin, args.url, model, args.timeout,
                               args.quote_chars, args.max_tokens)
                   for jid, spec, pin in jobs]
        for fut, job in zip(futures, jobs):
            row = fut.result()
            rows.append(row)
            if checkpoint is not None:
                with open(checkpoint, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print("  [%d/%d] %-14s %-3s %-11s %5.1fs" % (
                len(rows), len(jobs), job[0].replace("ARENA-JUDGE-", "J")[:14],
                job[2]["pin"], row.get("grade", "?"), row.get("seconds", 0.0)), flush=True)

    by_pin: Dict[str, Dict[str, Any]] = {}
    for pin in pins:
        pin_rows = [r for r in rows if r["pin"] == pin["pin"]]
        by_pin[pin["pin"]] = grade_pin(pin_rows)

    blind_rows = [r for r in rows if r.get("grade") == "BLIND"]
    meta = {
        "round": args.round,
        "judged_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "endpoint": args.url,
        "model": model,
        "judges": list(JUDGES),
        "quorum": QUORUM,
        "packet": str(packet_path),
        "packet_sha256": hashlib.sha256(
            json.dumps(packet, indent=1, ensure_ascii=False).encode("utf-8")).hexdigest(),
        "calls": len(rows),
        "blind_calls": len(blind_rows),
        "independence": ("ONE model serving SIX angles. Angle diversity, not model "
                         "diversity: six personas are not six independent judges."),
        "vocabulary": list(VERDICTS),
    }
    out = {"meta": meta, "pins": by_pin, "rows": rows}
    Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    print("model=%s  calls=%d  blind=%d  -> %s" % (model.split("/")[-1], len(rows), len(blind_rows), args.out))
    for pin_id, grade in by_pin.items():
        print("  %-3s %-11s %s" % (pin_id, grade["grade"], grade["reason"]))
    if blind_rows:
        print("BLIND calls (excluded from tally):")
        for r in blind_rows[:6]:
            print("  %-22s %-3s %s | %s" % (r["judge"][:22], r["pin"], r.get("error", "")[:60],
                                            r.get("raw", "")[:80].replace("\n", " ")))
    # A pin that reached no quorum is a reading that was not obtained: exit 1 so a
    # caller cannot mistake "the judges split" for "the judges agreed".
    no_reading = [pin_id for pin_id, grade in by_pin.items()
                  if grade["grade"] in ("BLIND", "NO_QUORUM")]
    if no_reading:
        print("no verdict obtained for: %s" % ", ".join(no_reading))
    return 0 if not blind_rows and not no_reading else 1


if __name__ == "__main__":
    sys.exit(main())
