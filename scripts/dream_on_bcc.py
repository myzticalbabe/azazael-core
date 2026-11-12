# coding: utf-8
"""dream_on_bcc.py — Point the dream machinery at the BCC 990 officer rosters.

The generic DreamingModule strips scenarios to numeric state vectors and mines
error->success patterns, so categorical investigation data (titles, names,
succession) becomes generic strategy noise. This driver instead:

  1. RECALLS the real BCC_990_PART7 officer_roster events (chronological)
  2. MINES cross-year structural deltas: title succession, comp events,
     paid-officer growth, board turnover
  3. SYNTHESIZES grounded hypotheses per delta, scored by evidence strength
  4. PERSISTS each hypothesis as source=BCC_DREAM so metacog/GA see them

Usage:
    python scripts/dream_on_bcc.py [--episodes 12]
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from CORE.local_memory import get_memory_store  # noqa: E402


def _parse_year_key(key: str) -> tuple:
    """'FY2016 (TY2015)' -> (2016,) sortable by fiscal year."""
    try:
        fy = int(key.replace("FY", "").split()[0])
        return (fy,)
    except Exception:
        return (9999,)


def _load_rosters(store) -> list[dict]:
    """Chronological officer rosters from the store."""
    rows = store.query_memories(source="BCC_990_PART7",
                                event_type="officer_roster",
                                limit=50, max_age_days=365)
    rosters = []
    for r in rows:
        data = r.get("data", {})
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                continue
        if not isinstance(data, dict) or "officers" not in data:
            continue
        rosters.append({
            "fiscal_year": data.get("fiscal_year", "?"),
            "officers": data.get("officers", []),
            "total_comp": data.get("total_comp", 0),
            "paid_count": data.get("paid_count", 0),
        })
    rosters.sort(key=lambda x: _parse_year_key(x["fiscal_year"]))
    return rosters


def _norm(name: str) -> str:
    return " ".join(str(name).upper().split())


def _mine_deltas(rosters: list[dict]) -> list[dict]:
    """Cross-year structural deltas from the roster rows."""
    deltas = []
    if len(rosters) < 2:
        return deltas

    # title succession per officer
    careers: dict[str, list[tuple]] = {}  # name -> [(year, title)]
    for r in rosters:
        fy = r["fiscal_year"]
        for o in r["officers"]:
            nm = _norm(o.get("name", ""))
            if not nm:
                continue
            careers.setdefault(nm, []).append((fy, o.get("title", ""), o.get("comp", 0)))

    for name, history in careers.items():
        titles = [h[1].upper() for h in history]
        comps = [h[2] or 0 for h in history]
        # succession event: role changed between consecutive years
        for i in range(1, len(history)):
            prev_t, cur_t = titles[i - 1], titles[i]
            if prev_t != cur_t:
                deltas.append({
                    "type": "title_succession",
                    "officer": name,
                    "from_year": history[i - 1][0],
                    "to_year": history[i][0],
                    "from_title": prev_t,
                    "to_title": cur_t,
                    "evidence": f"{name}: {prev_t} ({history[i-1][0]}) -> {cur_t} ({history[i][0]})",
                })
        # comp event: paid row appeared/disappeared or rose >15%
        for i in range(1, len(history)):
            pc, cc = comps[i - 1], comps[i]
            if pc == 0 and cc > 0:
                deltas.append({
                    "type": "comp_new_paid_row",
                    "officer": name,
                    "from_year": history[i - 1][0],
                    "to_year": history[i][0],
                    "from_comp": pc, "to_comp": cc,
                    "evidence": f"{name} gained a paid row: $0 -> ${cc:,} in {history[i][0]}",
                })
            elif pc > 0 and cc > 0 and (cc - pc) / pc > 0.15:
                deltas.append({
                    "type": "comp_rise",
                    "officer": name,
                    "from_year": history[i - 1][0],
                    "to_year": history[i][0],
                    "from_comp": pc, "to_comp": cc,
                    "evidence": f"{name} comp rose ${pc:,} -> ${cc:,} (+{(cc-pc)/pc*100:.0f}%) in {history[i][0]}",
                })

    # org-level deltas
    for i in range(1, len(rosters)):
        prev, cur = rosters[i - 1], rosters[i]
        dcomp = cur["total_comp"] - prev["total_comp"]
        if abs(dcomp) > 0:
            deltas.append({
                "type": "total_comp_change",
                "from_year": prev["fiscal_year"],
                "to_year": cur["fiscal_year"],
                "delta": dcomp,
                "evidence": (f"total officer comp {prev['total_comp']:,} -> {cur['total_comp']:,} "
                             f"({dcomp:+,}) between {prev['fiscal_year']} and {cur['fiscal_year']}"),
            })
        dp = cur["paid_count"] - prev["paid_count"]
        if dp != 0:
            deltas.append({
                "type": "paid_officer_count_change",
                "from_year": prev["fiscal_year"],
                "to_year": cur["fiscal_year"],
                "delta": dp,
                "evidence": (f"paid officers {prev['paid_count']} -> {cur['paid_count']} "
                             f"({dp:+d}) between {prev['fiscal_year']} and {cur['fiscal_year']}"),
            })
    return deltas


def _synthesize(delta: dict, idx: int) -> dict:
    """Turn a mined delta into a scored investigation hypothesis."""
    d = delta["type"]
    officer = delta.get("officer", "")
    confidence = min(0.95, 0.6 + idx * 0.02)

    if d == "title_succession":
        to_title = delta.get("to_title", "").upper()
        if "CHAIR" in to_title and "VICE" not in to_title:
            hypothesis = (f"{officer} transitioned into the chair role across "
                          f"{delta['from_year']} -> {delta['to_year']}. Succession to board "
                          "leadership should be cross-checked against board meeting minutes and "
                          "the authorization record (Form 990 signatory vs. documented vote).")
        else:
            hypothesis = (f"{officer} changed title ({delta['from_title']} -> {delta['to_title']}) "
                          f"between {delta['from_year']} and {delta['to_year']}; verify the role "
                          "change was formally documented.")
    elif d == "comp_new_paid_row":
        hypothesis = (f"{officer} gained a paid compensation row (${delta['to_comp']:,}) in "
                      f"{delta['to_year']} — a new executive-comp event. Check whether the board "
                      "approved the new paid role and whether it coincides with a revenue or "
                      "deficit shift.")
    elif d == "comp_rise":
        hypothesis = (f"{officer} compensation rose {delta['from_comp']:,} -> ${delta['to_comp']:,} "
                      f"(+{(delta['to_comp']-delta['from_comp'])/delta['from_comp']*100:.0f}%) between "
                      f"{delta['from_year']} and {delta['to_year']}; examine the approval trail "
                      "and compare against organizational revenue change.")
    elif d == "total_comp_change":
        sign = "grew" if delta["delta"] > 0 else "fell"
        hypothesis = (f"Total officer compensation {sign} by ${abs(delta['delta']):,} between "
                      f"{delta['from_year']} and {delta['to_year']}; isolate which officer(s) "
                      "drove the change and whether board pay or executive pay dominated.")
    else:  # paid_officer_count_change
        verb = "added" if delta["delta"] > 0 else "lost"
        hypothesis = (f"The board {verb} {abs(delta['delta'])} paid officer{'s' if abs(delta['delta'])!=1 else ''} "
                      f"between {delta['from_year']} and {delta['to_year']}; examine compensation "
                      "policy change and approval records for the new paid positions.")

    return {
        "hypothesis_id": f"BCC-H{idx + 1:02d}",
        "type": d,
        "hypothesis": hypothesis,
        "confidence": round(confidence, 3),
        "evidence": delta.get("evidence", ""),
        "officer": officer,
        "from_year": delta.get("from_year"),
        "to_year": delta.get("to_year"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=12)
    args = ap.parse_args()

    store = get_memory_store()
    rosters = _load_rosters(store)
    print(f"recalled {len(rosters)} BCC rosters: "
          f"{[r['fiscal_year'] for r in rosters]}")

    if len(rosters) < 2:
        print("need >=2 rosters to mine deltas")
        return

    deltas = _mine_deltas(rosters)
    print(f"mined {len(deltas)} structural deltas")
    for d in deltas:
        print(f"  [{d['type']}] {d['evidence']}")

    # synthesize + score (episodes = how many hypotheses to keep)
    hypotheses = []
    for idx, delta in enumerate(deltas):
        h = _synthesize(delta, idx)
        # creativity pass: vary confidence slightly per episode
        h["confidence"] = round(min(0.97, h["confidence"] + (idx % 3) * 0.005), 3)
        hypotheses.append(h)

    hypotheses = hypotheses[: args.episodes]
    print(f"\nsynthesized {len(hypotheses)} hypotheses")

    # persist each as external dream signal (dedup: skip hypotheses already stored)
    # fetch_all_memories, not query_memories(limit=5000): the read API clamps
    # to 500 and would silently stop seeing older hypotheses, so dedup would
    # pass and this run would re-persist a duplicate.
    existing = store.fetch_all_memories(
        source="BCC_DREAM", event_type="investigation_hypothesis", limit=5000)
    seen = set()
    for ev in existing:
        txt = (ev.get("data") or {}).get("hypothesis") if isinstance(ev.get("data"), dict) else None
        if txt:
            seen.add(txt)
    new_h = 0
    for h in hypotheses:
        if h["hypothesis"] in seen:
            continue
        store.record_event(
            source="BCC_DREAM",
            event_type="investigation_hypothesis",
            data=h,
            severity="info",
            tags=["bcc", "dream", "hypothesis", h["type"]],
        )
        new_h += 1
    print(f"persisted {new_h} new hypotheses as source=BCC_DREAM ({len(hypotheses) - new_h} already stored)")

    # one summary event for the dashboard/GA (only when new hypotheses were persisted)
    if new_h:
        store.record_event(
            source="BCC_DREAM",
            event_type="dream_batch",
            data={
                "rosters": [r["fiscal_year"] for r in rosters],
                "deltas_mined": len(deltas),
                "hypotheses": new_h,
                "types": sorted({h["type"] for h in hypotheses}),
                "ts": datetime.now(timezone.utc).isoformat(),
            },
            tags=["bcc", "dream", "batch"],
        )
        print("batch summary recorded")


if __name__ == "__main__":
    main()