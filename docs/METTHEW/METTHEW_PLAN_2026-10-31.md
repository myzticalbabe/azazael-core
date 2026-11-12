# METTHEW — community shards for gamers, on hardware people already own

**Status:** plan, not built. Nothing in this document has been implemented.
**Date:** 2026-10-31 · **Owner:** freebuff lane · **Scope:** a sibling dev project
that reuses this tree's instruments; it does not change the azazael runtime.

Metthew is the gamer-facing reading of an architecture that already exists here.
The question it answers: *can a group of friends, on the machines they already
own, make and play their own games without a storefront, a server, an account or
a publisher?* The tree has most of the parts. What it does not have is a shard
contract a gamer can satisfy, a validator a gamer can run, and a play loop that
survives not having a central authority.

---

## 1. What the architecture already is (measured, not paraphrased)

| organ | where | what it actually promises |
|---|---|---|
| Shard grammar | `tools/yggdrasil_census.py` (`SHARDS_PER_LIMB = 24`, `shard_plan`) | a shard is ≤24 modules of one language inside one limb; a limb-aligned shard never spans two trees. Measured 2026-10-31: **1,055 modules → 122 shards, 0 straddling** (the naive allocator would produce 22). |
| Liftability contract | `shard_liftability()` | a shard lifts out **iff no file outside it references a file inside it** — computed from file-level graphs, and a blocker names the graph and its ref count. Measured: **92 of 122 liftable, 30 blocked**, blocked by named references, not by opinion. |
| Instrumentation rule | `GRAPH_LANGUAGES` + `languages.uninstrumented` | a language is instrumented **iff a graph covers it**. This sentence was bought: `go` (2 files) and `java` (1) were counted and never analysed, and the ledger reported `uninstrumented: {}` anyway. It now derives from the table, so a counted-but-invisible language cannot pass. |
| Runtime contract | `src/SHARDS/polyglot.py` | `run_shard(lang)` returns a **JSON-serialisable result — "no weights, no handles across the boundary"** — and every language must print the same golden token (`AZAZAEL-GOLDEN-OK`). A missing toolchain is a **clean skip, never a failure**. |
| Clone-an-error pattern | `src/SHARDS/antibodies.py` | an observed failure becomes a **probe that seeks every recurrence**: `signature → probe → instances → status`, advancing `seeking → neutralized → immune` only on recorded seek evidence (three consecutive passes for immune), in a locked, atomic deed book. |
| Registry | `src/SHARDS/registry.py` | an entry names the capability, the paths that implement it, the languages in preference order, and the canonical implementation. Doc-only entries list `["doc"]`. |
| Portability of the instruments | measured this session | the same stdlib script classifies **57,071** files in the repo (100% declared), **216,182** under `C:/Azazael`, **8,463** on Ubuntu-26.04 and **6,484** on Kali, with no GPU and no daemon. The validator Metthew needs is this shape. |

The three properties that make a shard *trustworthy without a central authority*
are already stated here: **liftable** (runs alone), **instrumented** (every part
of it is seen by a graph), and **evidence-gated** (a state change requires a
recorded measurement).

## 2. The reading: what a game shard is

A Metthew shard is one small game — a level pack, a mechanic, a roster, a rule
set, a scoring module, a whole 1-hour game — that satisfies the same three
properties:

1. **It runs alone.** No import that reaches outside the shard. If it needs a
   shared thing, it *declares* it as a hub (the tree's "mycelium" idea) and the
   validator counts the coupling instead of hiding it.
2. **It proves itself.** Every shard ships a **golden run**: a fixed input, a
   deterministic tick count, a state hash, and a token on stdout. Any peer can
   re-run it in seconds and get the same hash, or reject the shard.
3. **It stays inside a budget.** Size, tick cost and memory are *measured on the
   reference low-end box* and published in the manifest — not promised.

What "gaming" them means, concretely — three loops, none needing a server:

- **Play**: lift a bundle, run the golden self-test, play. Multiplayer is LAN
  co-op (≤4) over a deterministic input log; no account, no matchmaking service.
- **Contest**: the same input log + same shard version must produce the same
  state hash on every host. A disagreement is a finding, not a desync bug report
  to a server. Leaderboards are files you exchange, not rows someone owns.
- **Audit**: anyone can run the validator. An exploit someone finds is *cloned*
  into a shard that hunts its own instances across every shard in the circle —
  the antibody pattern, pointed at cheats instead of at code defects. Cheating
  becomes content that makes the circle stronger.

## 3. Why this replaces a centralised industry, and where it does not

The industry's real advantages are three, and each has a local answer here:

| central advantage | local replacement | what it costs |
|---|---|---|
| content volume (thousands of titles) | anyone mints a shard; the platform's job is **verification**, not production | discovery is social, not algorithmic — a circle, not a market |
| anti-cheat + trust | **golden run + deterministic replay + public validator**; exploits become antibody shards | a cheat that is *inside* the golden run is undetectable without a second opinion; the plan's M-06 must say so |
| matchmaking/servers | **LAN co-op with an input log**; the "server" is one player's laptop | no drop-in with strangers; ≤4 players per shard by design |

Explicitly **not** in scope: storefront, DRM, accounts, cloud saves, live
services, monetisation, kernel-level anti-cheat, and any runtime that requires a
GPU compute path. Metthew is for the case where the circle is small and the
hardware is old.

## 4. The low-end budget (provisional — M-02 measures it)

These are the gates the plan is written against. They are provisional on
purpose: the project's own rule is that a control is not in place until it is
exercised and measured, so M-02 replaces every number below with a reading from
the reference box, and any number that cannot be met is a budget change, not a
shard exemption.

| gate | provisional | measured by |
|---|---|---|
| shard payload | ≤ 24 files, ≤ 2 MB packed | `metthew validate --budget` |
| validator wall clock | ≤ 5 s cold, no network | M-02 bench |
| play target | 720p / 30 fps, integrated GPU (UHD 620-class), 4 GB RAM | M-02 bench, 3 scenes |
| load time | ≤ 3 s cold, ≤ 1 s warm | M-02 |
| determinism | state hash identical across Windows / Ubuntu / Kali for the same input log | M-06 |
| no-network play | LAN only; zero outbound sockets during play | M-06 |
| tick budget p99 | < 33.3 ms (720p/30) | M-02 bench, per scene |
| env isolation | zero undeclared env reads | M-03 validator |

Reference low-end box (to be named in M-02 from what is actually available —
`PCMARK`-style claims are not measurements): one Windows laptop with integrated
graphics, the Ubuntu WSL host, and the Kali host. The three-environment parity
work in this tree already proves the same static instrument runs on all three.

## 5. Task separation

Four tracks that can proceed in parallel after M-01, and a critical path
**M-01 → M-03 → M-05 → M-06**. Owners are lanes, not people: `freebuff` writes
code, `kilo` does bulk edits/renders/verification, `opencode` dispatches pushes,
and the `windows` / `ubuntu` / `kali` environment owners run the portability
matrix. Full machine-readable ledger: `docs/METTHEW/tasks.json`.

### Track A — Contract (what a shard is)

| id | task | owner | depends on | acceptance |
|---|---|---|---|---|
| **M-01** | **Shard manifest spec v1** (`shard.json`): id, version, genre, files, entrypoint, loader, golden run (input, ticks, state hash, token), declared hubs, budget block, licence, author key. Written as a JSON Schema plus prose. | freebuff | — | schema validates the three reference shards from M-05 and rejects 5 named malformed fixtures (missing golden run, undeclared hub, oversize payload, entry outside shard, duplicate id) |
| **M-02** | **Low-end budget bench**: a script that measures load, tick cost, peak RSS and validator wall clock on the reference box and writes the numbers into a `budget.json` that M-01's schema references. | kilo + env owners | M-01 | three runs per scene, numbers reproducible within 10%, `budget.json` committed, every §4 provisional number replaced by a reading or a recorded waiver |
| **M-03** | **Validator** `tools/metthew_validate.py`: static liftability (no reference escapes the shard), hub declaration check, payload budget, golden run, determinism re-run, JSON report, exit code. **Ships its negative controls** (a shard that secretly imports outside; one that fakes its hash; one that passes). | freebuff | M-01, M-02 | pass/block on 3 fixtures in CI; a shard with one external reference is blocked *and the blocker is named with graph + count*; `--json` report is stable |
| **M-04** | **Minting CLI** `metthew mint <name>`: scaffolds the manifest, the golden run, the self-test, a placeholder scene, and runs the validator. No compiler, no toolchain beyond python3. | freebuff | M-03 | a new shard goes from `mint` to playing in ≤10 minutes on the reference box; the scaffolded shard validates green out of the box |

### Track B — Runtime (playing, offline)

| id | task | owner | depends on | acceptance |
|---|---|---|---|---|
| **M-05** | **Three reference shards, three genres**: (a) 2-player arena, (b) single-player puzzle, (c) rule-set/roster pack with no engine (doc+data only). Each ≤24 files. The set exists to prove the manifest is not shaped around one game. | freebuff | M-03 | all three validate green on Windows/Ubuntu/Kali; each plays at 720p/30 on the reference box; the doc-only shard loads with zero code |
| **M-06** | **Deterministic play ledger**: fixed-step sim, input log, per-tick state hash chain, replay verifier. The anti-cheat statement must be honest about its limit: a shard that lies *inside* its golden run is only caught by a peer re-running it or by an antibody shard. | freebuff | M-05 | same input log → identical hash on all three hosts; a single flipped input byte diverges at a named tick; replay of a 20-minute session ≤2 s |
| **M-07** | **Exploit-as-content**: an antibody shard for exploits — signature, probe, instances, status, with the `seeking → neutralized → immune` evidence rule from `src/SHARDS/antibodies.py` applied to *game* exploits (speed-up, state edit, input injection). | freebuff | M-06 | one real exploit demo is cloned into a probe that finds it in 3 fixture shards and none in 3 clean ones (positive *and* negative control) |

### Track C — Circle (no server, no accounts)

| id | task | owner | depends on | acceptance |
|---|---|---|---|---|
| **M-08** | **Bundle exchange**: a shard travels as one file (tar+manifest+hash) over USB, LAN or a chat message. Install is `metthew add bundle.mtw` → verify → play. No registry service; the circle's shard list is a file each player keeps. | freebuff | M-03 | install of a tampered bundle is refused with the hash mismatch named; a bundle >2 MB is refused by budget, not by luck |
| **M-09** | **Local reputation**: signed result files (shard id, version, hash, score, witness count) that travel with the bundle; a player's "ranking" is a file they own. No global truth, and the plan says so. | freebuff | M-08 | a forged score file fails signature check; a result for a shard version that does not match the installed hash is rejected |
| **M-10** | **Circle protocol docs**: how a group starts (one player mints, others add), how a shard is retired, how a disagreement about a score is settled by re-running the replay. Governance as a written procedure, not a feature. | kilo | M-06 | a 3-person play test completes a mint → share → play → contest → retire cycle with no central service, written up with the actual timings |

### Track D — Portability and gates

| id | task | owner | depends on | acceptance |
|---|---|---|---|---|
| **M-11** | **Portability matrix**: validator + one reference shard on Windows 10/11 integrated GPU, Ubuntu-26.04, Kali, and one 4 GB-RAM machine. Record what fails, where. | env owners | M-05 | matrix table with a row per host and a pass/fail per gate; every fail has the exact command that failed |
| **M-12** | **Project gates**: `metthew_validate` wired into the same style of gate this tree uses — a baseline of *unplayable* shards that may fall and never rise (the unresolved-ratchet pattern), plus a weekly cold run. | kilo | M-03, M-11 | ratchet fails on a new unplayable shard and passes on a fix; `--selftest` runs with its negative control |

### Not tasks (deliberately)

No store, no DRM, no accounts, no cloud, no matchmaking service, no kernel
anti-cheat, no monetisation, no GPU compute requirement, no rewrite of
`src/SHARDS/*` (Metthew consumes those contracts, it does not fork them).

## 6. Risks this plan refuses to hide

- **Discovery is the real casualty of decentralisation.** A circle works for 5
  friends and fails for 5,000 strangers. M-10 documents that boundary instead of
  pretending a file-exchange protocol scales.
- **Determinism across hosts is harder than it looks**: float math, iteration
  order, libm differences. M-06 must fix a numeric policy (Wasm 3.0 deterministic
  profile makes this a host property, not an author burden) or the replay
  verifier will disagree across the three environments in ways that look like
  cheating.
- **A cheat inside the golden run is invisible to the validator.** Only a peer
  re-running it, or an antibody shard, catches it. This is a limit of local
  trust, and it must be stated in the player-facing docs, not buried.
- **Budgets drift.** The provisional numbers in §4 are commitments to *measure*,
  not results; if the reference box cannot hit 720p/30, the plan's genre list
  changes rather than the number quietly moving.

## 7. Contract decisions (all five resolved before M-01 freeze)

| # | change | decision | rationale |
|---|---|---|---|
| 1 | **Wasm 3.0 host, deterministic profile, threads+SIMD off** | **ACCEPTED** | Replaces sandbox + determinism + numeric policy with one standard; Wasm 3.0 completed Sep 2025, spec numerics Oct 2026 |
| 2 | **One-download player** (browser/WASM target) | **ACCEPTED** | Install-Python-first is where players stop; browser target also gives sandbox for free |
| 3 | **Hubs as versioned libraries**, declared and counted | **ACCEPTED** | Banning external refs forces engine duplication inside 24 files; census already models hubs as "mycelium" |
| 4 | **`competitive` flag** on determinism | **ACCEPTED** | Hash chain is tax vs strangers; circle has none. Solo/puzzle/doc-only shards opt out. |
| 5 | **Content-addressed shard IDs** + host-version fallback | **ACCEPTED** | Version skew reads as "broken game"; `arena@sha256:...` allows coexistence |

Deferred (still real): federated index (R5, M-08), relay/stream transport (R7, M-08),
asset provenance (R9, M-01 field), visual identity or budget change (R10, M-02),
naming (R11, now or never).

## 8. Manifest spec additions (from deeper dive, added to M-01)

Three fields the contract cannot be validated without, all consequences of
measured defects:

| field | type | default | why |
|---|---|---|---|
| `execution_model` | `resident_host \| one_shot` | required | `resident_host` required for 720p/30; `one_shot` only fits Python/JS (call-per-tick exceeds tick budget) |
| `env_allowlist` | `string[]` | `[]` (empty) | Current polyglot path hands over player's entire environment (API tokens, PMB bearer tokens). Validator blocks undeclared reads. |
| `tick_budget` | `{p50_ms, p99_ms, scene}` | required | M-02 measurement; p99 < 33.3 ms for 720p/30. Rules out Rust/Go/PowerShell/Bash in call-per-tick model. |

## 9. First three things to do (in order, each shippable alone)

1. **M-01** — the manifest spec (with the three fields above and five contract decisions frozen). Without it, M-03 has nothing to validate and M-05 is three ad-hoc demos.
2. **M-03** — the validator, with its negative controls including two new ones: a fixture reading a secret from `os.environ` (blocked by `env_allowlist`), and a fixture exceeding its declared `tick_budget` (blocked on measurement). This is the whole trust story: liftable, budgeted, deterministic, or refused.
3. **M-05** — one genre, one shard, played by two people on one laptop. If that is not fun, no amount of contract work fixes it.
