# swarm-arena — blind pairwise judging, 3-language shards + Elo

Arena-style judging for pair corpora (default: `pairs_for_judgment.txt`).
Three independent judge shards vote blind per pair; majority wins; Elo board.

## Shards (deliberately disjoint signals — independent voters, not echoes)

| shard | language | signal |
|---|---|---|
| `judge_rs` | Rust, stdlib only | evidence refs: Root cause/Fix/Evidence, task refs, PMB ids, dates, hashes |
| `judge_go` | Go, stdlib only | investigative structure: What/Why/Where/Learned, NPI/EIN, dollars, detail |
| `judge_py.py` | Python, stdlib only | cross-reference density: RULE cites, obs ids, file paths, measured-claims |

## Build

```bash
cd judge_rs && cargo build --release
cd ../judge_go && go mod init judgo 2>/dev/null; go build -o judge_go.exe .
```

(`target/` and `*.exe` are git-ignored build artifacts; rebuild after pull.)

## Run

```bash
python arena.py [pairs_file] [out_dir]
# default pairs: ~/.pams/logs/pairs_for_judgment.txt, default out: ./out
```

Outputs `out/arena_results.json` (per-pair votes, winner, Elo delta) and
`out/elo_board.json` (rating per observation id, K=16, init 1500).
Posts an `ARENA-SHARD-JUDGE` wave row to the LaneMessageBoard for the swarm.

## Blinding

The orchestrator randomizes X/Y per pair (seed 20261029); shards never see
candidate ids — position bias is structurally impossible. 3 voters ⇒
majority always exists; `unanimous: false` rows are the interesting ones
(12/41 on the first corpus) and mark genuine judgment calls.
