"""Dreams: a local model invents readings, the ISA grades them, nothing lands free.

The judge swarm answers *"is this recorded claim supported?"*. A dream asks the
opposite question: *"what reading would this microcode get wrong?"* The model is
given the ISA's actual key vocabulary and asked for adversarial readings -- edge
cases the corpus does not contain -- and every dream is then run through the same
ops that grade real evidence.

Three rules make this native AI rather than a suggestion box:

1. **A dream is graded, never trusted.** The model's opinion of what should happen
   is recorded beside the engine's answer, and disagreement is the interesting
   output; agreement is not evidence that either is right.
2. **A dream cannot buy a patch.** :func:`discriminate` reports which policy knobs
   a dream would separate, and stamps every row ``a_dream_is_not_evidence``. To
   move the microcode, a dream must be pinned into the control corpus with the
   command and digest that produced its expected answer -- the same bar a recorded
   row must clear (:mod:`HYPER_EDGE.mutate`).
3. **No dream, no crash.** An unreachable endpoint, a malformed reply or a
   truncated object is recorded as BLIND with its reason; it is never an empty
   list that reads as "the model had no ideas".

The transport is the same OpenAI-compatible endpoint the judge swarm uses, probed
for its own model name so nothing here is pinned to a particular local build.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import kernel, microcode

__all__ = ["DREAM_FILE", "DEFAULT_URL", "discriminate", "dream", "grade", "replay",
           "served_model"]

DEFAULT_URL = os.environ.get("HYPER_EDGE_DREAM_URL", "http://127.0.0.1:8081")
DREAM_FILE = Path(__file__).resolve().parents[2] / "ARC" / "cain" / "hyper_edge_dreams.json"

#: The key vocabulary a dream may use. Derived from the ISA itself, so a new op
#: that forgets to publish its keys shows up as a dream the model cannot answer.
KEYS: Tuple[str, ...] = tuple(sorted({key for op in microcode.ISA for key in op.keys}))

PROMPT = """You are the adversarial case generator for an evidentiary admissibility
microcode. Invent {count} readings that a naive reviewer would accept and this
microcode should NOT.

The ops you may aim at (`at_stake` must be one of these codes):
{ops}

`record` is a flat JSON object using ONLY these keys, exactly as spelled:
{keys}

`id`, `at_stake` and `why` are SIBLINGS of `record` -- never inside it. `id` is a
descriptive slug unique within the set, not the literal word "short-name".

Return one JSON object, no prose:
{{"dreams":[{{"id":"undated-artifact","at_stake":"0x09",
  "record":{{"command":"tools/x.py --check","digest":"EXIT=0 OK","tier":"A"}},
  "why":"it carries a verdict and no date"}}]}}

Vary the ops at stake; include at least one absence (a key missing or null), one
contradiction between two keys, and one artifact that is real but unverifiable.
{seen}"""


def _post(url: str, payload: Mapping[str, Any], timeout: float) -> Tuple[int, str]:
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return 0, "%s: %s" % (type(exc).__name__, exc)


def served_model(url: Optional[str] = None, timeout: float = 15.0) -> str:
    """The model the endpoint is actually serving. Raises when nothing answers."""
    target = (url or DEFAULT_URL).rstrip("/") + "/v1/models"
    with urllib.request.urlopen(target, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8", "replace"))
    models = body.get("data") or []
    if not models:
        raise RuntimeError("endpoint %s serves no model" % target)
    return str(models[0].get("id") or "")


def _seen(limit: int = 12) -> List[str]:
    """Ids already dreamed, so a second run explores rather than repeats."""
    try:
        stored = json.loads(DREAM_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    ids = [str(entry.get("id")) for entry in stored.get("dreams") or []
           if isinstance(entry, dict) and entry.get("id")]
    return ids[-limit:]


def _extract(content: str) -> Tuple[Optional[object], str]:
    """Pull one JSON object out of a reply, or say why it could not."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.lstrip("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None, "reply carried no JSON object"
    try:
        return json.loads(text[start:end + 1]), ""
    except ValueError as exc:
        return None, "reply JSON did not parse: %s" % exc


def dream(count: int = 4, url: Optional[str] = None, timeout: float = 240.0,
          temperature: float = 0.9) -> Dict[str, Any]:
    """Ask the local model for ``count`` adversarial readings and grade each one."""
    endpoint = url or DEFAULT_URL
    try:
        model = served_model(endpoint)
    except (urllib.error.URLError, OSError, ValueError, RuntimeError) as exc:
        return {"status": "BLIND", "reason": "no dream endpoint: %s" % exc,
                "dreams": [], "model": ""}
    seen: List[str] = _seen()
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": 900,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": PROMPT.format(
                count=count, keys=", ".join(KEYS),
                ops="\n".join("%s %s -- %s" % (op.code, op.name, op.question)
                             for op in microcode.ISA),
                seen=("\nDo NOT repeat these readings already on file: " + "; ".join(seen)
                      if seen else ""))},
            {"role": "user", "content": "Invent the dream set now."},
        ],
    }
    status, body = _post(endpoint, payload, timeout)
    if status != 200:
        return {"status": "BLIND", "reason": "endpoint answered %s: %s" % (status, body[:160]),
                "dreams": [], "model": model}
    try:
        content = json.loads(body)["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return {"status": "BLIND", "reason": "reply shape unusable: %s" % exc,
                "dreams": [], "model": model}
    parsed, why = _extract(str(content))
    raw = parsed.get("dreams") if isinstance(parsed, dict) else None
    if not isinstance(raw, list) or not raw:
        return {"status": "EMPTY", "reason": why or "the model returned no dreams",
                "dreams": [], "model": model, "content": str(content)[:400]}

    dreams, refused = grade(raw)
    return {"status": "OK" if dreams else "EMPTY",
            "reason": "" if dreams else "; ".join(refused or ["no dream survived grading"]),
            "model": model, "endpoint": endpoint, "dreams": dreams,
            "graded_against": binding(),
            "refused": refused, "as_of": "recorded by HYPER_EDGE.dream"}


def binding() -> Dict[str, str]:
    """The control-case artefact these dreams were graded against.

    A dream is a hypothesis about a *specific* microcode and a specific policy
    file. Re-grading it after that artefact was re-issued would be grading a
    hypothesis against a different world, so the identity travels with the dream
    set and :func:`is_current` can refuse the comparison.
    """
    path = microcode.policy_path()
    identity = kernel.file_identity(path)
    return {"artifact": str(path), "artifact_id": (identity or {}).get("id", "")}


def is_current(entry: Mapping[str, Any]) -> bool:
    """True when the artefact a dream was graded against is still that artefact.

    An unbound dream (recorded before identities were carried) returns ``True``
    and is labelled ``unbound`` by the caller: discarding every stored dream over
    a missing field would delete evidence, which is the opposite of the point.
    """
    recorded = entry.get("artifact_id")
    if not recorded:
        return True
    live = kernel.file_identity(entry.get("artifact") or "")
    return bool(live and live["id"] == recorded)


def grade(raw: Sequence[Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Grade a model's proposals. Offline and pure, so it can be tested as such."""
    dreams: List[Dict[str, Any]] = []
    refused: List[str] = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("record"), dict):
            refused.append("not a record: %s" % str(entry)[:80])
            continue
        record = dict(entry["record"])
        unknown = sorted(set(record) - set(KEYS))
        # A key outside the ISA is dropped and NAMED, not fatal: measured
        # 2026-11-05, a local model nested "why" inside every record, and the
        # first version discarded all three dreams over a field it had already
        # been given as a sibling.  A dream graded on the keys it did use is a
        # reading; a dream discarded over a stray key is a lost reading.
        for key in unknown:
            record.pop(key, None)
        if not record:
            refused.append("%s carried no ISA key at all" % entry.get("id"))
            continue
        result = microcode.evaluate(record)
        dreams.append({"id": str(entry.get("id") or "dream-%d" % len(dreams)),
                       "at_stake": str(entry.get("at_stake") or ""),
                       "why": str(entry.get("why") or ""), "record": record,
                       "dropped_keys": unknown,
                       "rung": result["rung"],
                       "op_states": {code: state for code, state in result["states"].items()
                                     if state not in (microcode.PASS, microcode.SKIP)},
                       "reasons": result["reasons"]})
    return dreams, refused


def replay(dreams: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Re-grade stored dreams under the policy in force (they are not frozen)."""
    rows: List[Dict[str, Any]] = []
    for entry in dreams:
        current = is_current(entry)
        result = microcode.evaluate(entry["record"])
        rows.append({"id": entry.get("id"), "rung": result["rung"] if current else "STALE",
                     "was": entry.get("rung"), "stale": not current,
                     "moved": current and result["rung"] != entry.get("rung"),
                     "op_states": {code: state for code, state in result["states"].items()
                                   if state not in (microcode.PASS, microcode.SKIP)}})
    return rows


def discriminate(dreams: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Knob moves a dream set would separate -- hypotheses, stamped as such."""
    out: List[Dict[str, Any]] = []
    stale = [str(entry.get("id")) for entry in dreams if not is_current(entry)]
    graded = [entry for entry in dreams if is_current(entry)]
    if stale:
        # Named, not dropped silently: a stale dream is a lead that must be
        # re-dreamed against the live artefact, not a dream that never existed.
        out.append({"op": "-", "knob": "artifact", "old": "", "new": "",
                    "moved": stale, "verdict": "stale_dropped"})
    for code, knobs in microcode.POLICY_CHOICES.items():
        for knob, choices in knobs.items():
            current = microcode.POLICY.get(code, {}).get(knob)
            for value in choices:
                if value == current:
                    continue
                moved: List[str] = []
                for entry in graded:
                    now = microcode.evaluate(entry["record"])["rung"]
                    other = microcode.evaluate(
                        entry["record"],
                        policy={**{k: dict(v) for k, v in microcode.POLICY.items()},
                                code: {**microcode.POLICY.get(code, {}), knob: value}})["rung"]
                    if now != other:
                        moved.append("%s:%s->%s" % (entry.get("id"), now, other))
                if moved:
                    out.append({"op": code, "knob": knob, "old": current, "new": value,
                                "moved": moved,
                                "verdict": "a_dream_is_not_evidence"})
    return out
