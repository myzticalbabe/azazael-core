#!/usr/bin/env python3
"""arena.py — swarm-arena orchestrator: blind pairwise judging + Elo board.

Pipeline:
  1. Parse pairs_for_judgment.txt into [{pair, a:{id,text}, b:{id,text}}].
  2. Blind: randomize which candidate is X/Y per pair (seeded; map kept here,
     never shown to shards) to kill position bias.
  3. Fan out to 3 shards in parallel threads: Rust (evidence), Go
     (structure), Python (cross-refs). Each returns winner X/Y + margins.
  4. Majority vote (3 voters: no ties possible); record dissent.
  5. Elo update per pair (K=16, init 1500) keyed by observation id.
  6. Write arena_results.json + elo_board.json; post wave to LaneMessageBoard.

Usage: arena.py [pairs_file] [out_dir]
"""
import json
import os
import random
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
RS = os.path.join(HERE, "judge_rs", "target", "release", "judge_rs.exe")
GO = os.path.join(HERE, "judge_go", "judge_go.exe")
PY = [sys.executable, os.path.join(HERE, "judge_py.py")]
K = 16.0
INIT_ELO = 1500.0


def parse_pairs(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    pairs = []
    for block in text.split("======"):
        cands = []
        for chunk in block.split("--- "):
            chunk = chunk.strip()
            if not chunk or chunk.startswith("rel ") and "\n" not in chunk:
                continue
            lines = chunk.split("\n", 1)
            head, body = lines[0].strip(), (lines[1] if len(lines) > 1 else "")
            # head: "rel 4168 obs_int_id=1768 obs-51ed.. :: Title"
            oid = None
            for tok in head.split():
                if tok.startswith("obs-"):
                    oid = tok
            title = head.split("::", 1)[1].strip() if "::" in head else head[:80]
            full = (title + "\n" + body.strip()).strip()
            if len(full) > 50:
                cands.append({"id": oid or head[:40], "text": full})
        if len(cands) >= 2:
            pairs.append({"pair": "rel-%d" % len(pairs), "a": cands[0], "b": cands[1]})
    return pairs


def run_shard(name, cmd, payload):
    try:
        r = subprocess.run(cmd, input=payload, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=300)
        if r.returncode != 0:
            return name, None, r.stderr[:300]
        return name, json.loads(r.stdout), ""
    except Exception as e:  # noqa: BLE001
        return name, None, str(e)[:300]


def main():
    pairs_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.expanduser("~"), ".pams", "logs", "pairs_for_judgment.txt")
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "out")
    os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(20261029)

    pairs = parse_pairs(pairs_path)
    print("pairs: %d" % len(pairs), flush=True)

    # Blind mapping: (x_id, y_id) per pair; shards only see X/Y texts.
    blinded, xmap = [], {}
    for p in pairs:
        flip = rng.random() < 0.5
        x, y = (p["b"], p["a"]) if flip else (p["a"], p["b"])
        xmap[p["pair"]] = {"X": x["id"], "Y": y["id"]}
        blinded.append({"pair": p["pair"], "x": x["text"], "y": y["text"]})
    payload = json.dumps(blinded)

    results, errors = {}, {}
    lock = threading.Lock()

    def work(name, cmd):
        n, v, e = run_shard(name, cmd, payload)
        with lock:
            if v is None:
                errors[n] = e
            else:
                results[n] = {r["pair"]: r for r in v}

    threads = [threading.Thread(target=work, args=("rust", [RS])),
               threading.Thread(target=work, args=("go", [GO])),
               threading.Thread(target=work, args=("py", PY))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for n, v in sorted(results.items()):
        print("shard %s: %d verdicts" % (n, len(v)), flush=True)
    for n, e in sorted(errors.items()):
        print("shard %s FAILED: %s" % (n, e), flush=True)
    if len(results) < 2:
        print("FATAL: fewer than 2 shards returned; aborting (no quorum).")
        sys.exit(2)

    # Majority vote in blind space, then unblind; Elo in candidate space.
    elo = {}
    def rating(cid):
        return elo.setdefault(cid, INIT_ELO)

    verdicts = []
    for p in pairs:
        pid = p["pair"]
        votes = {}
        for n, v in results.items():
            if pid in v:
                votes[n] = v[pid]["winner"]
        xc, yc = xmap[pid]["X"], xmap[pid]["Y"]
        nx = sum(1 for w in votes.values() if w == "X")
        ny = sum(1 for w in votes.values() if w == "Y")
        win_blind = "X" if nx >= ny else "Y"
        winner = xc if win_blind == "X" else yc
        loser = yc if win_blind == "X" else xc
        # Elo
        rw, rl = rating(winner), rating(loser)
        ew = 1.0 / (1.0 + 10.0 ** ((rl - rw) / 400.0))
        rw2, rl2 = rw + K * (1 - ew), rl + K * (0 - (1 - ew))
        elo[winner], elo[loser] = rw2, rl2
        verdicts.append({"pair": pid, "x": xc, "y": yc, "votes": votes,
                         "winner": winner,
                         "unanimous": len(set(votes.values())) == 1,
                         "elo_w": round(rw2, 1), "elo_l": round(rl2, 1)})

    board = sorted(elo.items(), key=lambda kv: kv[1], reverse=True)
    json.dump(verdicts, open(os.path.join(out_dir, "arena_results.json"), "w",
                             encoding="utf-8"), indent=1)
    json.dump([{"id": cid, "elo": round(r, 1)} for cid, r in board],
              open(os.path.join(out_dir, "elo_board.json"), "w",
                    encoding="utf-8"), indent=1)

    una = sum(1 for v in verdicts if v["unanimous"])
    print("verdicts: %d unanimous %d/%d" % (len(verdicts), una, len(verdicts)))
    print("TOP 5:")
    for cid, r in board[:5]:
        print("  %.1f  %s" % (r, cid))
    print("BOTTOM 5:")
    for cid, r in board[-5:]:
        print("  %.1f  %s" % (r, cid))

    # Post the wave to the swarm board for pickup/record.
    try:
        sys.path.insert(0, "C:/Azazael/azazael/src")
        from CORE.LaneMessageBoard import LaneMessageBoard
        b = LaneMessageBoard(lane="freebuff")
        print(b.post_task(lane="freebuff", task_id="ARENA-SHARD-JUDGE",
                          action="ARENA-SHARD-JUDGE",
                          detail=("arena: %d pairs x %d shards, %d unanimous; "
                                  "elo top %s (%.1f). out=%s" % (
                                      len(verdicts), len(results), una,
                                      board[0][0][:24], board[0][1], out_dir)),
                          source_lane="windows"))
    except Exception as e:  # noqa: BLE001
        print("board post skipped: %s" % str(e)[:200])


if __name__ == "__main__":
    main()
