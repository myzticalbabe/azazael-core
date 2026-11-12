# METTHEW — low-end forensic dive, split three ways

**Question:** what does bleeding-edge technique survive on hardware people already own?
**Date:** 2026-10-31 · **Orchestrated by:** freebuff · **Figures:** Desktop/METTHEW_FORENSIC_2026-10-31/
**Status:** lane A measured and closed; lanes B and C dispatched, not yet worked.

This is not the plan and it is not the rejection analysis. It is the measurement pass
that both of those are missing: three non-overlapping angles, each producing numbers
with the command that produced them, merged into one recorded budget.

---

## The split

| lane | angle | the question it owns | deliverable |
|---|---|---|---|
| **A · freebuff** | **THE MACHINE** | what does a tick, a frame and a state hash actually cost here? | **`A_MACHINE_FLOOR` — DONE, this document §1** |
| **B · kilo** | **THE ARTIFACT** | what does a playable payload actually weigh, and which dated claims are real? | `B_ARTIFACT_FLOOR.md` (dispatched) |
| **C · opencode** | **THE CIRCLE** | can verification survive with no server and no GPU? | `C_CIRCLE_FLOOR.md` (dispatched) |

No angle may quote another angle's number without re-measuring it. That rule exists
because this project has already been burned by an inherited figure (the "2.1 GB VRAM"
that was wrong twice over, and the "39 of 39 rules have no validator" produced by an
instrument that dropped a leading zero).

---

## §1 Lane A — THE MACHINE (measured 2026-10-31)

Host: this box, CPython **3.15.0rc3 free-threaded, GIL disabled** — the production
interpreter, not the 3.14 GIL build. 600 ticks per cell, three entity counts, three
configurations. Method in `tickbench_probe.py`; raw table below.

| entities | config | p50 ms | p99 ms | ticks/s | vs 30 fps frame (33.3 ms) |
|---|---|---|---|---|---|
| 100 | sim | 0.121 | 0.340 | 7,566 | fits 1/98th of the frame |
| 100 | +hash | 0.106 | 0.294 | 8,110 | fits |
| 100 | +hash+json | 2.793 | 3.836 | 372 | fits |
| 1000 | sim | 1.074 | 2.195 | 879 | fits, 15× headroom |
| 1000 | +hash | 1.109 | 2.341 | 852 | fits, 14× headroom |
| 1000 | +hash+json | **27.187** | **34.076** | 37 | **p99 OVER the frame** |
| 5000 | sim | 5.168 | 6.407 | 190 | fits, and fits 60 fps too |
| 5000 | +hash | 5.387 | 6.922 | 182 | fits |
| 5000 | +hash+json | **124.347** | **145.396** | 8 | **8 fps** |

Peak traced memory: **1.6 MB** for 5,000 entities × 6 fields.

**A1 — the simulation is not the constraint.** 5,000 entities integrate in 5.17 ms
(p99 6.41 ms). That is inside a 30 fps frame *and* inside a 60 fps frame. On the
low-end question, the answer is that a Python resident host has an order of magnitude
more headroom than a 4 GB iGPU box needs for a circle-scale game.

**A2 — the serialisation boundary is the entire constraint.** At 1,000 entities the
tick is 1.07 ms, the hash chain adds **0.03 ms (0.1%)**, and `json.dumps(state)` adds
**26.08 ms (96%)**. The same tick goes from 4% useful work to 96% marshalling. This is
not a Python flaw — it is what "JSON-serialisable result across the boundary" costs when
the boundary is crossed *every tick* instead of once per session.

**A3 — process invocation cannot host a game at all.** From `src/SHARDS/sweep.json`
(n=10): python 0.7 ms and javascript 31.6 ms fit a 33.3 ms frame; **bash 92.2 ms is 2.8×
over, rust 122.2 ms 3.7×, go 146.1 ms 4.4×, powershell 248.6 ms 7.5×**. `run_shard()`
spawns one subprocess per call, so a shard that ticks through it disagrees with its own
play loop in four of six languages. The frozen execution model has to be a resident host.

**A4 — the 24-file cap is bought at 64% waste.** `shard_plan.json`: 122 shards × 24
slots = 2,928, of which 1,055 are used and **1,873 wasted**, because `limb_aligned`
forbids spanning two trees — a graphing constraint. A game shard has no such constraint.

### The thesis this lane produces

> On low-end hardware the cost is in the **boundaries**, not the work.

The work is 4% and shrinking with better data layout. Everything expensive is a border
crossing: process spawn (A3), JSON marshalling (A2), and a file-slot rule inherited from
another discipline (A4). A low-end adaptation that optimises the simulation is optimising
the 4%. The three boundary decisions worth making are structural, not clever:

1. **resident host** — one process per shard, fixed-step loop, no per-tick spawn (A3);
2. **hash binary state, serialise on save/replay only** — the hash chain is free at
   0.1%, so determinism is affordable; a per-tick JSON contract is not (A2);
3. **budget the packed size, not the file count** — bytes are what a player feels (A4).

None of the three needs a GPU, a server, or any bleeding-edge runtime. The creative
adaptation is subtraction: Wasm 3.0's deterministic profile, rollback netcode and
content-addressed ids all survive, but only inside a resident process with a binary
state boundary.

---

## §2 Lane B — THE ARTIFACT (dispatched to kilo)

**Own the payload question. Do not re-measure lane A.**

1. **Weigh one real payload.** Build the smallest playable shard that is honestly a game
   (a 2-player arena at 720p) and measure: source bytes, packed bytes, file count,
   cold load, unpack time. Then state what **2 MB** actually buys in sprites, levels and
   audio at the compression this tree can produce, with the encoder named.
2. **Test the file cap against a real tree.** Take the smallest playable payload and
   count files. If it needs 40 files, that is the finding — refuse to pad or merge to
   reach 24. Include lane A's A4 (64% waste) in the conclusion.
3. **Verify the dated claims, do not inherit them.** `BLEEDING_EDGE_ADAPTATIONS` asserts
   Wasm 3.0's deterministic profile (2025-09-17), WASM-4 64 KB carts (2021) and iroh 1.0
   (2026-07-09). Check each against a primary source and mark VERIFIED / WRONG /
   UNREACHABLE. A dive that copies dates is not a dive.
4. **Negative control:** one deliberately over-budget artifact that the size gate must
   refuse, so the budget is exercised and not just declared.

**Accept:** `docs/METTHEW/B_ARTIFACT_FLOOR.md` with a bytes-per-thing table, the file
count of a real payload, and three date verdicts. Every number carries the command.

## §3 Lane C — THE CIRCLE (dispatched to opencode)

**Own the verification cost. Do not re-measure lane A.**

1. **Price a session.** A 20-minute session at 30 fps is 36,000 ticks. Using lane A's
   measured 0.03 ms per state hash, compute the chain's total cost and then *measure*
   the replay verifier: how long to re-run 36,000 ticks and compare hashes. M-06's
   acceptance says ≤ 2 s — test it.
2. **Price a desync hunt.** Flip one input byte at a known tick. Measure how long the
   verifier takes to name the diverging tick (naive scan vs bisection over the chain)
   and report both. The replay's value is that it localises, not that it notices.
3. **Price a probe sweep.** Using the `src/SHARDS/antibodies.py` shape, measure the cost
   of seeking one exploit signature across N fixture shards, and report cost per shard
   so a circle of 10 can predict its own audit time.
4. **Negative control:** one clean fixture the probe must NOT fire on. The antibody
   store currently holds 2 deeds and both are `immune`, so no lifecycle transition is
   live — ship a `seeking → neutralized → immune` fixture, or state that the transitions
   are unexercised.

**Accept:** `docs/METTHEW/C_CIRCLE_FLOOR.md` with three measured costs (session chain,
desync localisation, probe sweep), each with its command, plus the negative control.

---

## §4 The merge (whoever holds the pen)

One document — `docs/METTHEW/ONE_RECORDED_BUDGET.md` — with:

- one row per gate, one number per row, one command per number;
- every lane-A provisional replaced by a reading or an explicit waiver;
- every oracle that fired shown firing on its positive fixture **and** silent on its
  negative one;
- any number a lane could not measure written down as UNMEASURED, never as a guess.

M-02 still owns the durable bench. This dive's job is to prove what is already
measurable today, so M-01 can freeze against readings instead of intentions.

---

## §5 Dispatch record (2026-10-31)

Lane A was run here — the interpreter bench (`tickbench_probe.py`, 6 languages x 10
runs, on the production 3.15t free-threaded interpreter), the tick-cost split, the
shard-capacity arithmetic and the figures. Lanes B and C were pushed to the board,
not described.

| lane | topic | task_id | delivery row | audit row |
| --- | --- | --- | --- | --- |
| kilo | `lane.kilo.tasks` | `LOWEND-ARTIFACT` | 47041 | 47042 |
| opencode | `lane.opencode.tasks` | `LOWEND-CIRCLE` | 47043 | 47044 |

Each row carries the measurement it inherits rather than an assignment in the
abstract: lane B is told the bs-> Rust 122.2 ms and Python 0.7 ms figures are
lanes A's and NOT to re-measure them; lane C is told the 0.03 ms per-hash figure
is the input to its session arithmetic. Both name the negative control they must
ship (RULE 0B-C) and both name what counts as done.

**Figures:** `C:/Users/clint/Desktop/METTHEW_FORENSIC_2026-10-31/` —
`chart1_interpreter_cost.png`, `chart2_tick_cost.png`, `chart3_tick_breakdown.png`,
`chart4_shard_capacity.png`, `map5_lane_split.png`, plus `charts.html` (source) and
`build_charts.py` / `capture_charts.js` (regenerator). Five PNGs, five distinct
SHA-256 prefixes, verified after render: `abc6ba02 / d3c81218 / 04400977 / 1822d160 /
ab2c48e8`. Chrome via Puppeteer, never PIL.

**One correction caught by reading the render, not the source:** the hash-chain
caption first claimed 3 percent of a tick. The plotted percentage is 0.13. The
table was right and the caption was wrong; it is now generated from the same
number the bar uses.
