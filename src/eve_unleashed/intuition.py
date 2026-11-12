"""Eve Unleashed -- native intuition (the supralogical rung).

Supralogical: the answer that arrives before the proof. intuit() never
touches the network -- it answers from Eve's own memory (the live
log): an exact memo of a past fetch, else a per-host prior, else
unknown. The ladder then proves or corrects the guess, and dream()
replays the stream to keep the priors honest.

Boundaries: intuition names a wall, never defeats one. A guess of
``blocked`` stops at rung zero and hands the URL to a human browser.
"""

from urllib.parse import urlparse

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass

from .live_log import read_events


def _host(url):
    try:
        return (urlparse(str(url)).hostname or "").lower()
    except Exception as e:
        note_failure("intuition:host", e)
        return ""


def _fetch_rows(log_path):
    return [r for r in read_events(log_path)["records"]
            if r.get("phase") == "fetch" and r.get("event") == "answer"]


def intuit(url, log_path=None):
    """Guess how a fetch will answer, from memory only. No network."""
    url = str(url or "").strip()
    if not url:
        raise ValueError("nothing to intuit: empty url")
    host = _host(url)
    if not host:
        return {"state": "unknown", "confidence": 0.0,
                "predicted_rung": None,
                "provenance": ["unparseable url"]}
    rows = _fetch_rows(log_path)
    memo = [r for r in rows if r.get("url") == url]
    if memo:
        last = memo[-1]
        if last.get("state") == "ok":
            return {"state": "guess", "confidence": 0.9,
                    "predicted_rung": last.get("rung"),
                    "provenance": ["memo:%s" % last.get("rung")]}
        return {"state": "blocked", "confidence": 0.7,
                "predicted_rung": last.get("rung"),
                "provenance": ["memo:wall %s" % last.get("kind")]}
    host_rows = [r for r in rows if _host(r.get("url", "")) == host]
    if not host_rows:
        return {"state": "unknown", "confidence": 0.0,
                "predicted_rung": None,
                "provenance": ["no memo, no host prior"]}
    ok = [r for r in host_rows if r.get("state") == "ok"]
    rate = len(ok) / len(host_rows)
    modal = None
    if ok:
        counts = {}
        for r in ok:
            counts[r.get("rung")] = counts.get(r.get("rung"), 0) + 1
        modal = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return {"state": "guess" if rate >= 0.5 else "blocked",
            "confidence": round(min(0.75, rate), 3),
            "predicted_rung": modal,
            "provenance": ["host:%s %d/%d answered" % (host, len(ok),
                                                       len(host_rows))]}


def dream(log_path=None):
    """Recalibrate: pair intuition guesses with the fetches that followed.

    Returns hit-rate per predicted rung plus a per-host answer table,
    all recomputed from the stream -- no stored weights to go stale.
    """
    recs = read_events(log_path)["records"]
    guesses = [r for r in recs if r.get("phase") == "fetch"
               and r.get("event") == "intuition" and r.get("url")]
    answers = [r for r in recs if r.get("phase") == "fetch"
               and r.get("event") == "answer" and r.get("url")]
    by_url = {}
    for a in answers:
        by_url.setdefault(a["url"], []).append(a)
    pairs, hits, by_rung = 0, 0, {}
    for g in guesses:
        later = [a for a in by_url.get(g["url"], [])
                 if a.get("ts", 0) >= g.get("ts", 0)]
        if not later:
            continue
        pairs += 1
        # Agreement, not just success (H-08): a blocked guess that meets a
        # blocked answer is a hit -- the lineage correctly predicted the
        # wall. Guesses without a recorded verdict fall back to the old
        # rule (ok + rung match) so old logs still read.
        if g.get("guess") is None:
            won = later[0].get("rung") == g.get("predicted") and \
                later[0].get("state") == "ok"
        else:
            agree = (g.get("guess") == "guess") == \
                (later[0].get("state") == "ok")
            won = agree and (g.get("predicted") is None or
                             later[0].get("rung") == g.get("predicted"))
        hits += 1 if won else 0
        key = str(g.get("predicted"))
        cell = by_rung.setdefault(key, {"pairs": 0, "hits": 0})
        cell["pairs"] += 1
        cell["hits"] += 1 if won else 0
    hosts = {}
    for a in answers:
        h = _host(a.get("url", ""))
        cell = hosts.setdefault(h, {"fetches": 0, "answered": 0,
                                    "salience": 0.0})
        cell["fetches"] += 1
        cell["answered"] += 1 if a.get("state") == "ok" else 0
        # Salience: weight outcomes by surprise, not recency. A blocked
        # answer carries more weight than an ok one, so the dream
        # prioritises learning from failures (L4-b).
        cell["salience"] += 2.0 if a.get("state") != "ok" else 1.0
    return {"pairs": pairs, "hits": hits,
            "hit_rate": round(hits / pairs, 3) if pairs else 0.0,
            "by_rung": by_rung, "hosts": hosts}


def inhibited_rungs(host, log_path=None, threshold=0.5):
    '''Rungs that keep failing for this host and should be skipped.

    Inhibition (L4-a): if a host's answer rate is below ``threshold``,
    every rung that has produced a blocked answer for that host is
    inhibited.  Returns a set of rung names.  Empty set means nothing
    to skip.
    '''
    host = str(host or "").strip()
    if not host:
        return set()
    rows = _fetch_rows(log_path)
    host_rows = [r for r in rows if _host(r.get("url", "")) == host]
    if not host_rows:
        return set()
    ok = [r for r in host_rows if r.get("state") == "ok"]
    rate = len(ok) / len(host_rows)
    if rate >= threshold:
        return set()
    # Inhibit rungs that have produced blocked answers for this host.
    blocked_rungs = {r.get("rung") for r in host_rows
                     if r.get("state") != "ok" and r.get("rung")}
    return blocked_rungs


def consolidate(log_path=None):
    """Merge repeated episodes into one durable rule while idle.

    Consolidation (L4-c): scan the fetch log for repeated (host, rung,
    state) triples and collapse them into a single prior statement.
    Returns a dict mapping host -> {rung: count, dominant_state}.
    """
    rows = _fetch_rows(log_path)
    groups = {}
    for r in rows:
        h = _host(r.get("url", ""))
        key = (h, r.get("rung"), r.get("state"))
        groups.setdefault(key, 0)
        groups[key] += 1
    # Merge: for each host, find the dominant rung+state.
    by_host = {}
    for (h, rung, state), count in groups.items():
        cell = by_host.setdefault(h, {})
        entry = cell.setdefault(rung, {"ok": 0, "blocked": 0, "total": 0})
        entry["total"] += count
        if state == "ok":
            entry["ok"] += count
        else:
            entry["blocked"] += count
    # Determine dominant state per rung.
    result = {}
    for h, rungs in by_host.items():
        merged = {}
        for rung, counts in rungs.items():
            dominant = "ok" if counts["ok"] >= counts["blocked"] else "blocked"
            merged[rung] = {
                "count": counts["total"],
                "dominant_state": dominant,
                "rate": round(counts["ok"] / counts["total"], 3) if counts["total"] else 0.0,
            }
        result[h] = merged
    return result


def incubate(url, log_path=None):
    """Leave a blocked problem and return with a different attack.

    Incubation: if the URL's host has a low answer rate, suggest an
    alternative rung to try instead of the one that keeps failing.
    Returns {"original_rung": ..., "alternative": ..., "reason": ...}.
    """
    url = str(url or "").strip()
    if not url:
        return {"original_rung": None, "alternative": None,
                "reason": "empty url"}
    host = _host(url)
    rows = _fetch_rows(log_path)
    host_rows = [r for r in rows if _host(r.get("url", "")) == host]
    if not host_rows:
        return {"original_rung": None, "alternative": "plain",
                "reason": "no history for host; try plain"}
    # Find the rung that failed most for this host.
    blocked_rungs = {}
    for r in host_rows:
        if r.get("state") != "ok":
            rung = r.get("rung")
            if rung:
                blocked_rungs[rung] = blocked_rungs.get(rung, 0) + 1
    if not blocked_rungs:
        return {"original_rung": None, "alternative": None,
                "reason": "no blocked rungs for host"}
    worst = max(blocked_rungs, key=blocked_rungs.get)
    # Suggest the first rung not in blocked_rungs.
    all_rungs = ["plain", "browser-headers", "tls-impersonation", "render"]
    alternative = None
    for r in all_rungs:
        if r not in blocked_rungs:
            alternative = r
            break
    if alternative is None:
        alternative = "plain"
    return {"original_rung": worst, "alternative": alternative,
            "reason": "rung %s blocked %d time(s); try %s" % (
                worst, blocked_rungs[worst], alternative)}


def prime(url, log_path=None):
    """Let the last task bias the next one's first guess.

    Priming: returns the last successful rung for this host, so the
    caller can try it first.  Returns {"primed_rung": ..., "source": ...}.
    """
    url = str(url or "").strip()
    if not url:
        return {"primed_rung": None, "source": "empty url"}
    host = _host(url)
    rows = _fetch_rows(log_path)
    host_ok = [r for r in rows if _host(r.get("url", "")) == host
               and r.get("state") == "ok"]
    if not host_ok:
        return {"primed_rung": None, "source": "no prior success for host"}
    last = host_ok[-1]
    return {"primed_rung": last.get("rung"),
            "source": "last success: %s" % last.get("rung")}


def prospective_memory(url, trigger, log_path=None):
    """Hold an intention for a future trigger.

    Prospective-memory: records that when ``trigger`` occurs for this
    host, the given url should be revisited.  Returns the intention
    dict (caller persists it).
    """
    url = str(url or "").strip()
    trigger = str(trigger or "").strip()
    if not url or not trigger:
        return {"error": "url and trigger required"}
    return {"intention": {"url": url, "trigger": trigger,
                          "host": _host(url)},
            "status": "held"}


def counterfactual_replay(url, log_path=None):
    """Re-run a past decision against what actually happened.

    Counterfactual-replay: for each fetch answer for this URL, compare
    the predicted rung (from intuition) with the actual rung.  Returns
    a list of {predicted, actual, match, counterfactual} dicts.
    """
    url = str(url or "").strip()
    if not url:
        return []
    recs = read_events(log_path)["records"]
    guesses = [r for r in recs if r.get("phase") == "fetch"
               and r.get("event") == "intuition" and r.get("url") == url]
    answers = [r for r in recs if r.get("phase") == "fetch"
               and r.get("event") == "answer" and r.get("url") == url]
    results = []
    for g in guesses:
        later = [a for a in answers if a.get("ts", 0) >= g.get("ts", 0)]
        if not later:
            continue
        a = later[0]
        predicted = g.get("predicted")
        actual = a.get("rung")
        match = predicted == actual
        # Counterfactual: what if we had tried the next rung instead?
        counterfactual = None
        if not match and actual:
            counterfactual = "if %s had been tried, outcome might differ" % actual
        results.append({"predicted": predicted, "actual": actual,
                        "match": match, "counterfactual": counterfactual})
    return results


def affect(url, log_path=None):
    """Carry a valence that changes the next choice.

    Affect: compute a valence (-1.0 to 1.0) for this host based on
    recent outcomes.  Positive = mostly ok, negative = mostly blocked.
    Returns {"valence": float, "label": str}.
    """
    url = str(url or "").strip()
    if not url:
        return {"valence": 0.0, "label": "neutral"}
    host = _host(url)
    rows = _fetch_rows(log_path)
    host_rows = [r for r in rows if _host(r.get("url", "")) == host]
    if not host_rows:
        return {"valence": 0.0, "label": "neutral"}
    # Weight recent outcomes more heavily.
    recent = host_rows[-5:]
    score = 0.0
    for r in recent:
        score += 1.0 if r.get("state") == "ok" else -1.0
    valence = round(score / len(recent), 3)
    if valence > 0.3:
        label = "positive"
    elif valence < -0.3:
        label = "negative"
    else:
        label = "neutral"
    return {"valence": valence, "label": label}
