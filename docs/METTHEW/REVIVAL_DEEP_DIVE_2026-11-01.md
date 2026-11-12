# Deep dive: shard conversion for discontinued games (2026-11-01)

Follow-up to the shallow pass. The user's claim is specific and strong: **people already own a
multitude of old, discontinued games; the FLAMING SWORD could convert them into shards and give
them new life.** This document tests that claim properly — size of the loss, the exact legal
weather in 2026, what conversion can and cannot lawfully ship, the five rights classes a shard
can have, the technical protocol that replaces decompilation, and the gates that make it real.
Recorded tasks are **Track G, `M-25` … `M-30`** in [`tasks.json`](tasks.json).

## 1. The size of the loss is not anecdotal

- **87% of classic games released in the US are out of print** and classified *critically
  endangered* (Video Game History Foundation, 2023-07-10, tier A — their own study).
- The loss is not only "old discs": digital storefronts and services are the volatile part —
  the same study names store shutdowns as a driver, not a footnote.
- **1,294,188 verified EU citizens** signed *Stop Destroying Videogames*, submitted to the
  Commission on 2026-01-26 (tier A). The Commission's reply **declined to propose new
  legislation** and gestured at a voluntary code instead (2026, tier A for the reply's
  existence; the June date is tier C).

So the demand is proven and the legislative route has just closed for now. That is the
strategic fact this whole document turns on: **waiting for a right to revive is not a plan.**

## 2. The legal weather, dated, with the parts that matter

| fact | date | tier | what it means for a shard |
| --- | --- | --- | --- |
| US Copyright Office **declined a new DMCA §1201 preservation exemption** | 2024-10-25 / final rule 2024-10-28 | A | the sword must never contain a circumvention step; a shard that needs a cracked DRM is out of scope, permanently |
| renewed exemptions run in three-year cycles; the next window is **Oct 2027 – Oct 2030** | (Copyright Office §1201 page) | A | the only lever with a date on it; argue there, ship something that does not depend on it |
| **NCSoft granted City of Heroes' Homecoming a limited licence** to run and develop the game | 2024-01-04 | B | licensed revival is a *real* path, not a hypothetical: it has happened once |
| **Yuzu settled for $2.4M and Ryujinx folded** | 2024 | C/D | the emulator-shaped route is chilled even where it is lawful; do not build the product on it |
| clean-room engine reimplementations ship **no original code** and **require the player's own data** (OpenMW, OpenRCT2; OpenTTD additionally offers replacement assets) | 2021-2026 | B/C | this is the pattern with a 20-year survival record, and it maps onto the shard contract almost exactly |

**Conclusion.** There are exactly three lawful shapes, and only one of them is available to a
circle of friends with no lawyer:

1. **You revive your own copy, for yourself** — no distribution, no shareable shard.
2. **You ship rules, never bytes** — a clean-room simulation plus an *asset lock* that requires
   the player's own copy. Distribution is of *your* code, which you own.
3. **The rights holder licenses it** — real, rare, and worth pursuing exactly once, with a
   portfolio of working revivals as the argument.

The shard contract already has the field shape for (2): content-addressed identity, a golden run
taken **headless over the simulation**, and `provenance` for the chain. What it lacks is a
declaration of *what the shard is allowed to contain* and *what it requires the player to own*.
That is `M-25` and `M-26`.

## 3. The five rights classes (the spine of the whole thing)

Every revival shard declares exactly one class, and the class decides what may be in the bundle:

| class | name | ships | shareable | evidence required |
| --- | --- | --- | --- | --- |
| **R0** | rights-clear | code **and** assets | yes | licence file or your own authorship, recorded in `provenance` |
| **R1** | asset-free clean room | rules, sim, config **derived by measurement**, presentation *recipes* | yes | asset lock + clean-room statement; **no original bytes** |
| **R2** | data transform | derived numeric tables from the original data files | **no — personal only** | the sword must mark it non-shareable and refuse to mint a public bundle |
| **R3** | licensed revival | whatever the licence permits | yes, within the licence | licence text + terms, hashed, in `provenance` |
| **R4** | no-go | nothing | — | reason recorded: DRM circumvention needed, still sold and maintained, or operator refusal |

Two invariants fall out, and both are testable:

- **No circumvention, ever.** If the game cannot be read without defeating a technical measure,
  the answer is R4. This is not caution; it is the one thing §1201 makes unambiguously illegal
  and the one thing that would take the whole project down with it.
- **R2 cannot be shared.** A table of every unit's stats lifted out of a commercial data file is
  a derived copy. It is defensible as personal use; it is not defensible as a gift. The sword
  must therefore have a *personal-only* mode that produces a shard it refuses to bundle.

## 4. Why "convert the game" is the wrong sentence

A discontinued game is not one thing. It is a stack, and each layer has a different fate:

| death mode | what actually died | what conversion can recover | class |
| --- | --- | --- | --- |
| live-service shutdown | the **server**, not the client | the rules, sometimes; never the other players' state | R1 at best |
| delisting / licence expiry | distribution rights (often music) | nothing that was licensed — the sword must drop those assets and say so | R1 with holes |
| DRM call-home | the activation service | nothing without circumvention | **R4** |
| platform decay | the runtime (32-bit, old DirectX, Flash) | everything: this is the *best* case, a pure re-host | R1 or R0 |
| media rot | the bytes themselves | only what is already archived elsewhere | R2 personal |
| unmaintained engine | the maintainer | the whole thing, if the rules are measurable | R1 |
| account/identity service | the login | local profiles; no entitlement claims | R1 |

The honest sentence is therefore: **the sword converts the *rules* of a discontinued game, and
lends its *presentation* back only as a recipe the player's own copy fills in.** That is a
narrower claim than "new life from old games" and it is the only one that survives contact with
a rights holder.

## 5. What the sword can extract, per unit kind

The Track E taxonomy already fits; revival just changes *where the evidence comes from*:

| unit kind | source evidence | conversion | confidence |
| --- | --- | --- | --- |
| `config` | data tables, INI/CSV/JSON blobs in the install | integerised tables, typed and ranged | exact if the file parses; R2 if derived from a proprietary binary |
| `controller` | observed input→motion over recorded play, plus the frame rate | integer steering/aim rules that reproduce the observed trace | high — this is what clean-room reimplementations do |
| `feature` | observed state machine (cooldowns, costs, win conditions) | explicit state machine with declared transitions | medium; needs a divergence count, not a vibe |
| `effect` | the visual/audio behaviour, never the bytes | a presentation recipe (shake amplitude, decay, blend) declared in the tier ladder | low-to-medium; approximate by definition |
| `function` | rarely separately visible | subsumed by a feature; the sword should refuse to mint a bare function | refused |

**Measure, do not decompile.** The protocol that makes a clean room defensible is also the
protocol that makes it *good*: record a fixed scenario, extract the state transitions you can
observe, reimplement, then diff. The gate is a **divergence count over a fixed scenario**, and
the number that matters is zero — the same discipline a TAS verifier uses. That is `M-27`.

This is also where most revival projects actually die: not in court, in **feel**. Tick order,
input latency, collision tolerance and RNG seeding are not in the manual; they are measured or
they are wrong. A shard that is right about damage numbers and wrong about jump arcs will be
rejected by the people who loved the game, which is the only audience it has.

## 6. The asset lock, in full

```json
"revival": {
  "class": "R1",
  "source_game": {"title": "<game>", "platform": "win32", "retail_year": 1999},
  "clean_room": true,
  "ships_original_bytes": false,
  "requires_assets": {
    "source": "player-supplied",
    "files": [
      {"path": "DATA/UNITS.DAT", "sha256": "<64 hex>", "size_bytes": 191488},
      {"path": "DATA/MAPS.PAK", "sha256": "<64 hex>", "size_bytes": 8126464}
    ],
    "on_mismatch": "refuse",
    "note": "the bundle contains no original bytes and will not start without the player's own copy"
  },
  "behaviour_evidence": {
    "scenario": "scenarios/duel_60s.json",
    "divergences": 0,
    "recorded_at": "2026-11-01"
  }
}
```

Three consequences, each of which is a gate:

1. **The bundle is legally yours to give away.** Every byte in it is either your code, your
   measured numbers, or a hash of someone else's file — and a hash is not a copy.
2. **CI must run without the game.** A shard that cannot be tested without the original data is
   untestable by anyone who does not own it, so the pipeline needs a **null-assets mode**: a
   synthetic stand-in satisfying the lock's *shape*, with the golden run proving the simulation
   and explicitly not proving the art. (`M-26`.)
3. **Two players with different regional copies may hold different bytes.** A hash lock that is
   too strict will refuse a legitimate owner, so the lock declares its granularity and the
   refusal message names the file and both hashes rather than "invalid assets".

## 7. A worked conversion, end to end

A discontinued 1999 top-down arena game, one circle member owns the CD:

1. **Identify** — `sword revive --game <dir> --identify` reads the install, finds 3 data files,
   a config INI, and no DRM. Class candidate: R1.
2. **Assets** — hashes the 3 files, writes the lock. Sizes total 8.3 MB; nothing is copied.
3. **Config** — parses the unit table (24 units × 9 stats) into a config shard, integerised;
   class R2 until proven otherwise, so the sword marks it *personal-only* and refuses to bundle
   it with the R1 shard.
4. **Behaviour** — 12 recorded 60-second matches, transitions extracted, divergence counter at 0
   for the movement/attack model over 43,200 ticks.
5. **Sim** — the clean-room integer sim, `sim_hz` 30, golden run over the fixed scenario.
6. **Presentation** — three tiers declared (handheld / laptop / desktop), each listing what it
   drops, because the original shipped for a 200 MHz machine and a modern laptop should not be
   forced to run a 1999 renderer.
7. **Mint** — two bundles: one R1 shard that is shareable, one R2 config bundle that is not.
8. **Compose** — the player combines the R1 sim with *their* new rules, producing a new game
   that owes the old one its movement and nothing else.

Step 8 is the actual promise of the user's sentence: not a museum, a **graft**. The old game's
feel becomes a capability a new game can declare.

## 8. What this does not solve, stated plainly

- **It does not revive MMOs.** The client is the small half; the world state, the other players
  and the item economy lived on the server. A shard can re-host the *systems*, never the *world*.
- **It does not recover licensed content** (music most often). The sword can only drop it and
  say so, which is what "delisting" actually removes.
- **It does not make assets shippable.** Anyone promising a one-click "convert and share the
  whole game" is promising piracy with a new file extension.
- **It does not survive a hostile rights holder alone.** City of Heroes shows licensing is
  possible; Yuzu shows the cost of being interesting to a litigious one. The mitigation is
  structural: ship rules, hash assets, publish your clean-room record.
- **It cannot outrun 2027.** The next §1201 window is the only place the rules could become
  kinder. Until then, every design decision here assumes today's law is permanent.

## 9. The recorded tasks (Track G)

| id | task | gate |
| --- | --- | --- |
| M-25 | revival classes R0-R4 in the manifest + refusal rules (no circumvention, R2 never shareable) | 5 class fixtures; R4 refused, R2-shareable refused |
| M-26 | asset lock + **null-assets CI mode** | mismatch refuses; null-assets runs the golden run headless |
| M-27 | behaviour-capture protocol and divergence counter (measure, don't decompile) | same scenario twice → 0 divergences; injected 1-tick offset → >0 |
| M-28 | config/data extraction into a config shard, class-gated | extraction reproduces recorded outcomes on N cases; R2 refused a public bundle |
| M-29 | `sword revive` end-to-end (identify → class → assets → behaviour → sim → mint) | synthetic dead game becomes an R1 shard that runs null-assets; the same fixture at R4 is refused |
| M-30 | revival ladder + compose so the old game's feel is a capability a new shard can declare | one hash across 3 tiers; a tier touching the sim refused |

**Where this argument is tested first:** [Track H](RESURRECT_2600_2026-11-01.md) applies all six
of those stages to one machine — the Atari 2600 — where the cartridge is small enough to hash, the
hardware small enough to reimplement and the state small enough to verify. Track H's intake stage
is already code (`tools/sword_2600.py`), and its rights reasoning is the strongest available:
implementing a machine is fair use (*Sony v. Connectix*, 2000) while unlocking a game is not (the
2024 §1201 refusal). If the rule "ship the rules, hash the assets" cannot be made to work where
the machine is 1.19 MHz and 128 bytes of RAM, it cannot be made to work anywhere.
