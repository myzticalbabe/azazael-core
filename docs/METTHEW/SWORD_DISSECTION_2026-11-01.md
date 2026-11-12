# The FLAMING SWORD dissects game code into Shards — goals and tasks (2026-11-01)

Recorded at the user's instruction, 2026-11-01. The machine-readable version of this
document is **Track E in [`tasks.json`](tasks.json)** (`M-13` … `M-18`); this page is the
reasoning copy, and every task's acceptance in `tasks.json` is phrased in terms of something
a command prints, not in terms of this page being read.

## The goal, in one sentence

Give the FLAMING SWORD the ability to **dissect game code into captured units** — features,
functions, effects, controllers, configs — **mint each unit as a Shard** under the M-01
contract, and **compose** those shards into a player's own game, including **3D render
shards**, straight out of the sword.

Three things follow from that sentence, and the whole track is those three things:

1. **DISSECT** — a unit is only real if the sword can bound it (what it touches, what it
   needs, what it changes). Anything it cannot bound is refused, not guessed.
2. **MINT** — a unit becomes a Shard with a capability id from
   [`highend_families.json`](highend_families.json), an exact golden run, and a provenance
   chain. The Shard contract already exists; the sword writes into it, it does not invent
   a second one.
3. **COMPOSE** — combining units is where a game appears, and it is where the sword must be
   allowed to say **no**: two controllers claiming the same tick channel is a refusal, not a
   merge.

## The unit taxonomy (what "dissect" means precisely)

| kind | what it is | capture test (must all hold) |
| --- | --- | --- |
| `feature` | a named behaviour spanning several functions ("double jump", "loot table", "pause menu") | every symbol it reaches is inside the capture; it has one entrypoint; it has an observable effect |
| `function` | one callable with no hidden state of its own | no unbound imports; no module-level reads; pure or explicitly state-passing |
| `effect` | something that changes what the player perceives (camera shake, particle burst, bloom pulse) | touches presentation only, and says so (`tick_affects: presentation`) |
| `controller` | input → intent → world mutation (movement, aiming, AI steering) | declares the tick channel it owns; declares its intent set; no float in the state it writes |
| `config` | a table of tunables (speeds, damage, spawn rates, tier budgets) | every key has a type and a range; no value is read from the environment |

A capture that fails one test is **refused with the failing test named** — not captured
"approximately". The reason is in the M-14 risk line and worth repeating: a half-captured
effect is a bug the player cannot see, and a bug the player cannot see is the only kind this
project must never ship.

## The pipeline, and what each stage refuses

```
DISSECT   sword_dissect.py --repo <tree> --emit units.jsonl
          refuses: unbounded closure, hidden import, effect that reaches the sim through a global
BIND      assign each unit a capability_id from the catalogue vocabulary
          refuses: an id not defined in highend_families.json
PROVE     licence + source_commit + evidence, or no unit
          refuses: unknown licence, missing commit, GPL unit into a permissive shard
MINT      write shard.json + files + a golden run produced by EXECUTION
          refuses: a unit set that cannot produce a deterministic golden run
COMPOSE   resolve interfaces across units from different source trees
          refuses: two owners for one tick channel or one config key (no last-write-wins)
VERIFY    metthew_validate.py --manifest, plus the family's own gate
          refuses: V7 (approximate in the sim), V9 (approximate presentation with a
          non-headless golden run), unknown capability, drifted bundle id
```

## 3D render shards out of the sword

A render shard is not "a shard with pretty pictures". It is **dual-layer**, and the sword
must emit it that way every time:

- an **exact integer simulation** — the part whose hash a peer replays;
- a **declared presentation layer** — the part whose bytes are recipes, latents, tiers and
  budgets, allowed to be approximate, marked as such, and never inside the hash.

The five presentation families in the catalogue are the sword's render vocabulary:
`glassland` (virtualised geometry), `spectra` (neural materials), `fogcut` (path-traced, no
bake), `splatfield` (captured playfields), `neuralframe` (interpolation), and `ladder`
(declared intensity tiers). The sword emits the *decomposition, the recipe and the ladder*;
the host does the drawing.

**What is provable on this box today:** the ladder half. `reference-shards/highend_ladder`
is a real shard whose `--verify` prints **one state hash across four tiers** while the
presentation counters differ — 600 ticks, hash `c83f217d…42aa3`, p50 0.00071 ms/tick. That
is the contract a render shard has to satisfy, executed rather than asserted. The splat,
path-traced and neural halves have **no** checkable artefact here and are recorded as
unverified until a native host exists; M-18's risk line says exactly that.

## The rights rule (the load-bearing one)

Dissecting game code is lawful in proportion to what the extractor can show. So the sword is
a **rights-aware** extractor, and M-15 lands **before** minting, never after:

- **Allowed:** your own games; permissively licensed code (MIT / Apache-2.0 / BSD / public
  domain) with the licence file and commit recorded; a unit you re-implemented from a
  description.
- **Refused:** a unit whose licence is unknown, whose `source_commit` is absent, or whose
  licence is incompatible with the shard it would enter (a GPL unit cannot enter an MIT
  shard — the refusal names both licences).
- **Never:** ripping assets or code out of a commercial game and shipping it as a shard,
  stripping attribution, or minting a shard that hides where a unit came from.

This is not a disclaimer bolted onto the plan; it is the gate that decides whether the
dissector is a tool or a liability, and it is why `provenance` is already a field in
`shard.schema.json` rather than something to add later.

## Worked example — one unit, end to end

Dissecting a "double jump with coyote time" controller out of a tree the author owns:

```json
{
  "id": "unit:coyote-doublejump@sha256:1f0c…",
  "kind": "controller",
  "capability_id": "fixed-point-steering",
  "provenance": {
    "source_repo": "github.com/<owner>/<their-game>",
    "source_path": "src/player/jump.c",
    "source_commit": "9a1c4f2",
    "licence": "MIT",
    "licence_evidence": "LICENSE@9a1c4f2"
  },
  "bounds": {"files": ["src/player/jump.c"], "bytes": 4211},
  "interface": {
    "inputs": ["grounded:bool", "jump_pressed:bool", "ticks_since_ground:int"],
    "outputs": ["vy_delta_mm:int", "jumps_used:int"],
    "tick_affects": "sim"
  },
  "evidence": {"entrypoints": ["update_jump"], "tests": ["tests/jump_test.c"]}
}
```

`tick_affects: sim` forces integer outputs — which is why the captured signature is
`vy_delta_mm:int` and not a float velocity. That single rule is what lets a captured
controller sit inside a shard whose hash a friend can replay.

## What this turn actually landed, and what it only recorded

**Landed (executed, on disk):**

- `docs/METTHEW/highend_families.json` — 9 shared primitives + 8 presentation families, each
  with dated lineage, capability objects, payload shape, host requirement, gate and negative
  control.
- `docs/METTHEW/reference-shards/highend_ladder/` — a runnable shard, `--verify` proves one
  hash across four tiers; manifest passes `metthew_validate.py`.
- `docs/METTHEW/tasks.json` — **Track E recorded**: M-13 … M-18, with dependencies resolved
  and acceptance phrased as a command.
- Both reference shards re-sealed and passing validation after the edits.

**Recorded, not built:** `tools/sword_dissect.py` does not exist. M-14 … M-18 have no code
yet. Nothing in this document should be read as a working dissector; the honest status is
"six tasks recorded, one executable proof of the contract they must satisfy".

## Gate table

| task | gate command | negative control |
| --- | --- | --- |
| M-13 | `sword_dissect.py --check units.jsonl` | 4 invalid units must produce 4 named refusals |
| M-14 | `sword_dissect.py --repo fixture --emit` | 2 decoys must be refused, and an empty tree must emit 0 |
| M-15 | `--prove` on 4 licence fixtures | unknown licence and GPL-into-MIT must both be refused |
| M-16 | `metthew_validate.py --manifest <minted>` | a unit set with no deterministic run must be refused |
| M-17 | `--compose` on 3 units from 2 trees | conflicting tick channels must be refused, both ids named |
| M-18 | `<render shard> --verify` | a tier allowed to touch the sim must break hash equality |
