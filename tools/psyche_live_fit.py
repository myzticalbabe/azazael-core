# coding: utf-8
"""tools/psyche_live_fit.py — fit a person theta from live PAMS rows.

Pipeline: PAMS episodic rows (read-only) -> Ollama appraisal annotation
-> PAD responses -> regularized fit (identify.py machinery).

HONESTY CONTRACT (the whole point of this file): fitting a personality
from rows that carry no affective signal fabricates a person. So the tool
measures signal coverage FIRST (affect_density per row from the
annotator) and REFUSES to fit below threshold -- returning neutral theta
with an explicit insufficient-signal verdict instead of a confident
fiction. A null result with the mechanism recorded beats a positive
result with the mechanism hidden. This is the documented entry point
for real-human validation (Phase 5 follow-up): run it, read coverage,
and if signal exists, compare against questionnaire scores.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PSYCHE import affect as A
from PSYCHE import identify as I
from PSYCHE.traits import default_theta_path
import math

PMB_DB = (Path.home() / ".pmb" / "workspaces" / "04a151a73354"
          / "events.sqlite")
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL = "sam860/olmoe-1b-7b-0924:latest"
MIN_SIGNAL_ROWS = 5
DENSITY_THRESHOLD = 0.3

ANNOTATE_PROMPT = """Rate the human situation in this memory on these keys.
Reply with ONLY a JSON object, no other text:
{"desirability": <event goodness -1..1 or 0 if none>,
 "likelihood": <0..1>,
 "confirmed": <true/false/null>,
 "self_agent": <true if the person acted / false if acted upon / null>,
 "praiseworthiness": <-1..1 or 0>,
 "affect_density": <0..1: does this text carry any feeling at all?>}

Memory: %s"""


def fetch_rows(limit: int = 120) -> list[dict]:
    con = sqlite3.connect("file:%s?mode=ro" % PMB_DB, uri=True)
    rows = con.execute(
        "SELECT ulid, event_type, content, timestamp FROM events "
        "WHERE archived_at IS NULL ORDER BY rowid DESC LIMIT ?",
        (limit,)).fetchall()
    con.close()
    out = []
    for ulid, typ, content, ts in rows:
        if content and len(content) >= 40:
            out.append({"ulid": ulid, "type": typ, "text": content[:800],
                        "ts": ts})
    return out


def annotate(text: str, timeout: int = 120) -> dict:
    """Ask the local model for appraisal keys. Never raises; neutral fallback."""
    blank = {"desirability": 0.0, "likelihood": 0.5, "confirmed": None,
             "self_agent": None, "praiseworthiness": 0.0,
             "affect_density": 0.0}
    body = json.dumps({"model": OLLAMA_MODEL, "prompt": ANNOTATE_PROMPT % text,
                       "stream": False,
                       "options": {"temperature": 0.0,
                                   "num_predict": 200}}).encode()
    try:
        req = urllib.request.Request(
            OLLAMA_URL, data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        resp = urllib.request.urlopen(req, timeout=timeout)
        payload = json.loads(resp.read())
        txt = payload.get("response", "")
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            return blank
        got = json.loads(m.group(0))
        out = dict(blank)
        for key in ("desirability", "likelihood", "praiseworthiness",
                    "affect_density"):
            try:
                out[key] = max(-1.0, min(1.0, float(got.get(key, 0.0))))
            except (TypeError, ValueError):
                pass
        if isinstance(got.get("confirmed"), bool):
            out["confirmed"] = got["confirmed"]
        if isinstance(got.get("self_agent"), bool):
            out["self_agent"] = got["self_agent"]
        out["likelihood"] = max(0.0, min(1.0, out["likelihood"]))
        out["affect_density"] = max(0.0, min(1.0, out["affect_density"]))
        return out
    except Exception:
        return blank


def live_fit(limit: int = 120, verbose: bool = True) -> dict:
    """Full pipeline. Returns report; refuses (neutrally) below signal."""
    rows = fetch_rows(limit)
    annotated = []
    for row in rows:
        ann = annotate(row["text"])
        annotated.append((row, ann))
    signal = [(row, ann) for row, ann in annotated
              if ann["affect_density"] >= DENSITY_THRESHOLD]
    report: dict = {
        "n_rows": len(rows),
        "n_signal": len(signal),
        "coverage": round(len(signal) / max(1, len(rows)), 3),
        "verdict": "ok",
    }
    if len(signal) < MIN_SIGNAL_ROWS:
        report["verdict"] = "insufficient-signal"
        report["theta"] = {d: 3.0 for d in I.DOMAINS}
        report["reason"] = (
            "fewer than %d rows carry affective content; fitting would "
            "fabricate a person. Supply questionnaire answers or richer "
            "episodic rows." % MIN_SIGNAL_ROWS)
        if verbose:
            print("coverage %.3f (%d/%d): INSUFFICIENT SIGNAL - "
                  "returning neutral theta" % (
                      report["coverage"], len(signal), len(rows)))
        return report
    sits, pads = [], []
    for row, ann in signal:
        sit = {"kind": "event", "desirability": ann["desirability"],
               "likelihood": ann["likelihood"], "confirmed": ann["confirmed"]}
        out = A.appraise_event(**{k: v for k, v in sit.items()
                                  if k != "kind"})
        if not out:
            continue
        sits.append(sit)
        pads.append(A.emotion_to_pad(out[0][0], out[0][1]))
    fit = _fit_on_pairs(sits, pads)
    report["theta"] = fit["theta"]
    report["mse"] = fit["mse"]
    report["n_used"] = len(sits)
    if verbose:
        print("coverage %.3f (%d/%d): fitted theta %s" % (
            report["coverage"], len(signal), len(rows), fit["theta"]))
    return report


def _fit_on_pairs(sits: list, pads: list) -> dict:
    """Fit theta to (situation, PAD) pairs from real annotations."""
    theta = {d: 3.0 for d in I.DOMAINS}
    # Direct descent on the pairs (same objective shape as fit_traits).
    best = dict(theta)

    def mse(th):
        err = 0.0
        for pad, sit in zip(pads, sits):
            pred = I.predict_pad(th, sit)
            err += sum((a - b) ** 2 for a, b in zip(pad, pred))
        prior = sum((th[d] - 3.0) ** 2 for d in I.DOMAINS)
        return err / max(1, len(pads)) + 0.02 * prior

    cur = mse(best)
    for step in (0.5, 0.25, 0.1, 0.05):
        for _ in range(40):
            improved = False
            for d in I.DOMAINS:
                for cand in (best[d] - step, best[d] + step):
                    if not 1.0 <= cand <= 5.0:
                        continue
                    trial = dict(best)
                    trial[d] = round(cand, 3)
                    v = mse(trial)
                    if v < cur - 1e-12:
                        best, cur = trial, v
                        improved = True
            if not improved:
                break
    base = {d: 3.0 for d in I.DOMAINS}
    prior_mse = mse(base)
    return {"theta": best, "mse": round(cur, 5),
            "prior_mse": round(prior_mse, 5)}


def emit_theta(report: dict) -> str:
    """Persist a fitted theta as the live person snapshot (producer half).

    Writes ONLY on verdict "ok": emitting on insufficient-signal would
    publish a neutral fiction as a fitted person, violating this tool's
    honesty contract. Returns a human-readable outcome line; never raises.
    """
    try:
        if not isinstance(report, dict) or report.get("verdict") != "ok":
            return ("emit-theta: REFUSED (%s) - snapshot not written"
                    % (report.get("verdict", "no-report")
                       if isinstance(report, dict) else "no-report"))
        theta = report.get("theta", {}) or {}
        scores = {d: round(float(theta[d]), 3) for d in I.DOMAINS
                  if isinstance(theta.get(d), (int, float))
                  and 1.0 <= float(theta[d]) <= 5.0}
        if not scores:
            return "emit-theta: REFUSED (no usable domain scores)"
        snapshot = {"name": "live-fit", "source": "psyche_live_fit",
                    "domain_scores": scores, "mse": report.get("mse"),
                    "n_used": report.get("n_used")}
        dest = default_theta_path()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(snapshot, indent=2, sort_keys=True),
                        encoding="utf-8")
        return "emit-theta: wrote %s" % dest
    except Exception as exc:
        return "emit-theta: FAILED (%s)" % exc


if __name__ == "__main__":
    import sys as _sys
    args = [a for a in _sys.argv[1:] if not a.startswith("--")]
    emit = "--emit-theta" in _sys.argv[1:]
    limit = int(args[0]) if args else 120
    report = live_fit(limit)
    print(json.dumps(report, indent=2, default=str))
    if emit:
        print(emit_theta(report))
