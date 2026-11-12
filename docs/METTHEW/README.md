# Metthew — index

Metthew is a **gaming** project: a group of friends (a circle) creating, exchanging and
playing small, portable game modules (**Shards**) on the hardware they already own,
instead of renting them from a centralised gaming industry.

It is not a fork of `src/SHARDS/*` and touches none of them. It borrows exactly one
idea from that tree — the own / validated / replaceable unit with a declared contract —
and applies it to games.

## Files

| file | what it is |
| --- | --- |
| [METTHEW_PLAN_2026-10-31.md](METTHEW_PLAN_2026-10-31.md) | the plan: why, what exists to reuse, what a Shard is, the low-end budget, the four tracks, the explicit non-goals |
| [tasks.json](tasks.json) | the same 12 tasks machine-readable, with owner, dependencies, deliverable, acceptance and risk. It is the scheduling copy; the markdown is the reasoning copy. |
| [shard.schema.json](shard.schema.json) | **M-01 DELIVERABLE** — JSON Schema for Shard Manifest v1 with 18 fields including execution_model, env_allowlist, tick_budget, competitive, provenance; validates 3 reference shards, rejects 5 malformed fixtures |
| [fixtures/](fixtures/) | **M-01 FIXTURES** — 13 validation fixtures: 3 that must PASS (`valid_arena`, `valid_highend_rollback_duel`, `valid_highend_path_traced_swarm`) and 10 that must be refused (`missing_golden_run`, `undeclared_hub`, `oversize_payload`, `entry_outside_shard`, `duplicate_id`, `highend_budget_over`, `highend_env_wildcard`, `highend_golden_not_headless`, `highend_no_capabilities`, `highend_presentation_in_sim`). `--fixtures` reports both directions; a fixture set where everything passes has stopped being a control. |
| [REJECTION_ANALYSIS_2026-10-31.md](REJECTION_ANALYSIS_2026-10-31.md) | Metthew's contract measured against PICO-8, Doom WADs, Luanti, Factorio, OpenTTD, SMAPI, Minecraft/Fabric, Workshop and Steam Remote Play: 12 ranked reasons players would bounce, and the five contract changes that should land before M-01 freezes. |
| [BLEEDING_EDGE_ADAPTATIONS_2026-10-31.md](BLEEDING_EDGE_ADAPTATIONS_2026-10-31.md) | what already exists to catch them: 14 dated strands (2024–2026) — Wasm 3.0's deterministic profile, WASM-4 carts, Uxn/permacomputing, iroh 1.0, deterministic simulation testing, GGRS rollback, TAS verification, N64 recompilation, Playdate/Picotron, s&box and Dreams, Nostr catalogues, AI minting, web3's collapse — each mapped to the task it changes. |
| [DEEPER_DIVE_2026-10-31.md](DEEPER_DIVE_2026-10-31.md) | the verification pass: every figure in the plan re-read from its artefact and confirmed, plus the three things written down nowhere — the executor hands a shard the player's whole environment, the measured tick cost rules four of six languages out of a 30 fps loop, and the 24-file cap is borrowed from a rule that wastes 64% of its own capacity. Proposes three M-01 fields and two M-03 controls. |
| [FORENSIC_DIVE_LOWEND_2026-10-31.md](FORENSIC_DIVE_LOWEND_2026-10-31.md) | the low-end forensic dive, split three ways for creatively adapted bleeding-edge dev. Lane A ran here (interpreter bench on the production 3.15t interpreter, tick-cost split, capacity arithmetic, five figures); lanes B and C were dispatched to the board as `LOWEND-ARTIFACT` to kilo and `LOWEND-CIRCLE` to opencode, each with the negative control it must ship. §5 is the dispatch record with every delivery row. |
| [highend_families.json](highend_families.json) | **the high-end vocabulary** — 9 shared primitives + 8 presentation families (glassland, spectra, fogcut, splatfield, mirrorcast, persona, neuralframe, ladder), each with dated lineage, capability objects, payload shape, host requirement, acceptance gate and negative control. A shard may only declare capability ids defined here. **Gated since 2026-11-01** by `tools/metthew_families.py` (F1..F7, wired as pre-commit Check 6d): ids defined once, `sim` implies exact, every lineage entry carries `source_tier` (A/B/C/D) and, above D, a url, and no payload byte figure survives without the command and date that measured it. The original eight `payload_bytes_estimate` values are gone — they were arithmetic about files nobody made. |
| [reference-shards/](reference-shards/) | runnable reference shards. `highend_duel` (integer-only duel, 120 Hz sim, fixed input transcript) and `highend_ladder` (four intensity tiers, `--verify` proves ONE state hash across all four). Both pass `tools/metthew_validate.py`, which since 2026-11-01 also recomputes each `id` address from the files it names (V14), hashes the files on disk against `files[]` (V15) and, under `--measure`, times the golden run and refuses a declared tick p50 more than 4x away from the clock (V13). The ladder family's `reference_shards` link is what makes F6 checkable rather than decorative. |
| [REVIVAL_DEEP_DIVE_2026-11-01.md](REVIVAL_DEEP_DIVE_2026-11-01.md) | **Track G** — discontinued games converted into shards. 87% of classic US releases are out of print (VGHF, tier A); the EU declined to legislate after 1,294,188 verified signatures and the US declined a §1201 preservation exemption, so the only lawful shape is **ship the rules, hash the assets**: five rights classes (R0-R4), an asset lock the player's own copy satisfies, a null-assets CI mode, and a divergence counter that replaces decompilation. |
| [SWORD_DISSECTION_2026-11-01.md](SWORD_DISSECTION_2026-11-01.md) | **Track E** — the FLAMING SWORD dissects game code into captured units (feature · function · effect · controller · config), mints them as Shards, composes them into a player's own game, and emits 3D render shards dual-layer. Records the rights rule (licence + source commit before minting) and the gate for each task. |
| [RESURRECT_2600_2026-11-01.md](RESURRECT_2600_2026-11-01.md) | **Track H** — the right-to-repair framing, engineered: the owner already owns the cartridge, the machine is what died, so the repair is the emulator (*Sony v. Connectix*, 2000: fair use). Why the Atari 2600 is the best first case in numbers (76×262 fixed frame, 128 bytes of RAM, integer-only — every 2600 game is already a deterministic sim), the raster-recipe insight that makes presentation convertible and lawful, what `tools/sword_2600.py` does today — intake (M-31) and bank switching measured, not inferred (M-32) — the illegal things it deliberately cannot do, and the five stages still missing. |

## M-01 Status: **COMPLETED** (2026-11-01)

- ✅ `shard.schema.json` — JSON Schema with 18 required fields
- ✅ 5 contract decisions frozen (all accepted): Wasm 3.0 det profile, one-download player, versioned hubs, competitive flag, content-addressed IDs
- ✅ 3 deeper-dive fields added: `execution_model`, `env_allowlist`, `tick_budget`
- ✅ 6 validation fixtures created (1 valid + 5 malformed)
- ✅ Structural validation: valid fixture passes, missing_golden_run rejected
- ✅ `tasks.json` updated: M-01 status = completed, contract_changes_decided

## Separation of tasks in one screen

## Separation of tasks in one screen

| track | name | tasks | who | first gate |
| --- | --- | --- | --- | --- |
| **A** | Contract | M-01 shard schema · M-02 low-end bench · M-03 validator · M-04 minting CLI | freebuff (bench shared with every host owner) | **M-01**, then M-02 → M-03 → M-04 |
| **B** | Runtime | M-05 three reference shards · M-06 deterministic ledger and replay · M-07 exploit-as-content | freebuff | after M-03 |
| **C** | Circle (no server, no accounts) | M-08 bundle exchange · M-09 signed result files · M-10 3-person play test | freebuff, M-10 owned by kilo | M-08 after M-03, M-10 after M-06 |
| **D** | Portability and gates | M-11 portability matrix · M-12 unplayable ratchet + weekly cold run | windows/ubuntu/kali owners, kilo | after M-05 / M-03 |
| **E** | Sword dissection and render shards | M-13 unit contract · M-14 dissector · M-15 rights gate · M-16 mint · M-17 compose · M-18 3D render shards | freebuff | **M-13**, then M-14 → M-15 → M-16 → M-17 → M-18 |
| **G** | Revival of discontinued games | M-25 revival classes R0-R4 · M-26 asset lock + null-assets · M-27 behaviour capture · M-28 config extraction · M-29 `sword revive` · M-30 revival ladder + compose | freebuff | **M-25**, then M-26 → M-27 → M-28 → M-29 → M-30 |
| **H** | Resurrection, right-to-repair framing (Atari 2600 first) | M-31 cartridge intake · M-32 bank-switching model · M-33 raster-kernel capture · M-34 integer sim extraction · M-35 divergence lab · M-36 bleeding-edge presentation · M-37 audio recipe · **M-38 Swordline orchestrator** · **M-39 cartridge-source decision** | freebuff | **M-31, M-32, M-38 and M-39 done**, then M-33 → M-34 → M-35 → M-36 ∥ M-37 |

**Critical path:** `M-01 → M-03 → M-05 → M-06`.

**Before M-01 freezes**, the five changes in the rejection analysis must each be decided
either way (sandboxed loader, one-download player, versioned hubs, a `competitive`
flag on determinism, content-addressed ids). They are proposals, not accepted scope; the
bleeding-edge research has already specified the first one as a Wasm 3.0 deterministic-profile
host and proposed one candidate task (M-13, the recovery track).

Nothing in tracks B, C or D may claim a task before M-03's validator exists, because
every other task's acceptance is phrased in terms of something the validator prints.
That is deliberate: it makes the validator the first deliverable rather than an
afterthought, and it stops "it works on my machine" from being an acceptance argument.

## Why the work is separated this way

- **A before B** — a reference shard only proves a contract if there is an instrument
  that can refuse it. Today the repository has a `shard.json` *concept* and 151 shard
  directories, but no machine-readable shard contract and no validator for one; M-01
  and M-03 create both.
- **B before C** — exchange and reputation are meaningless without a deterministic
  play record. Two players disagreeing about a score with no replay is a shouting
  match, and M-06 is what turns it into a diff.
- **C separately** — the circle features are the only part of Metthew with a social
  failure mode (Sybil, collusion, discovery). They are kept out of A and B so that a
  working single-player Shard is never blocked on a reputation argument.
- **D last and continuously** — portability has to be measured, not assumed, and the
  ratchet only makes sense once there is a validator whose verdict can be ratcheted.

## Running the plan

Claims go through the lane queue, not through this document:

```bash
python C:/Users/clint/_board.py claim freebuff 10
python C:/Users/clint/_board.py done <board_id> "<command + result>"
```

Status lines to `lane.freebuff.status`; announce to `lane.all.announce`. A task is
finished when a command proves it, not when a document says so.
