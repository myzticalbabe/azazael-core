# METTHEW — bleeding-edge creative adaptations, 2024–2026

**Date:** 2026-10-31 · **Status:** research record; changes no task, proposes two
**Scope:** the newest work that Metthew could adopt, with dates, and what each one
would change in the plan. Companion to
[REJECTION_ANALYSIS_2026-10-31.md](REJECTION_ANALYSIS_2026-10-31.md): that document
found why players bounce, this one finds what already exists to catch them.

**Rule applied:** a strand is listed only if it changes a specific Metthew task. Research
that is merely interesting goes in §6 instead, labelled as such.

---

## 1. The legal route has already been tried and closed

| fact | date | source |
|---|---|---|
| *Stop Destroying Videogames* submitted with **1,294,188 verified statements of support** | 26 Jan 2026 | European Citizens' Initiative, official page |
| Public hearing at the European Parliament | 16 Apr 2026 | same |
| Parliament plenary debate | 21 May 2026 | same |
| Commission Communication: it **"cannot propose a legal obligation to keep video games playable"** at this stage; reporting points to a voluntary code of conduct instead | 16 Jun 2026 | Commission reply page; secondary coverage |

**Adaptation:** stop describing Metthew as a replacement *in law* and start describing it
as the thing a circle does **while the law does not exist**. That is not a rhetorical
downgrade — it is the only claim the evidence supports, and it changes the audience from
"the industry" to "the five people who want to play on Friday". The movement's failure
also warns about a specific trap: a campaign that asks a central authority to make
decentralisation happen is still a centralised strategy.

**Task:** M-10 (circle protocol) and the plan's framing. Cost: none.

## 2. Wasm 3.0's deterministic profile — the biggest single adaptation

| fact | date | source |
|---|---|---|
| **Wasm 3.0 completed**, and includes a **deterministic execution profile**: "between platforms choosing to implement this deterministic execution profile, Wasm thereby is fully deterministic, reproducible, and portable" | 17 Sep 2025 | webassembly.org news |
| Core spec 3.0 numerics: "in the deterministic profile, only positive canonical NaN outputs are produced" | spec, 27 Oct 2026 | W3C/WebAssembly spec |
| The remaining nondeterminism is *enumerable*: NaN bit patterns, plus threads and SIMD if enabled. DFINITY's runtime simply disables threads and SIMD to get determinism | 2021, still the pattern | dfinity forum; WebAssembly `design/Nondeterminism.md` |
| Wasmtime documents deterministic execution as a supported mode | current | docs.wasmtime.dev |

This collapses three separate Metthew problems into one decision. The rejection analysis
asked for (a) a sandbox, (b) cheap determinism, (c) a numeric policy. A Wasm 3.0 host with
the deterministic profile and threads/SIMD off is all three: a memory-safe sandbox with no
filesystem or network by default, and a float policy decided by a standard instead of by
each shard author.

**Adaptation:** pending contract change #1 is upgraded from "sandboxed loader (WASM or
restricted VM)" to **"Wasm 3.0 host, deterministic profile, threads+SIMD disabled"**, and
M-06's numeric policy becomes *a property of the host*, not a burden on authors.

**Task:** M-01, M-03, M-06. Cost: the authoring language must compile to Wasm (Rust, C/C++,
Zig, AssemblyScript, Go via TinyGo, Luau via a Wasm build — not vanilla Python).

## 3. WASM-4: the cartridge this contract is trying to invent

| fact | value | source |
|---|---|---|
| display / colours / refresh | 160×160, 4 customisable colours, 60 Hz | wasm4.org docs |
| memory / cart size limit / disk | 64 KB RAM, **64 KB cart**, 1024 bytes save | same |
| input / audio | keyboard, mouse, touch, up to 4 gamepads / 2 pulse + 1 triangle + noise | same |
| artifact | a **self-contained `.wasm` file**, built from *any* language that targets Wasm | same |
| where it runs | browser first, libretro core, native ports | wasm4.org, libretro |
| social machinery | game jams with prize pools, itch.io entries | itch.io jam pages |

A WASM-4 cart is a Metthew shard with a stricter budget, a real runtime, one-file
distribution, and five years of evidence that people finish games at this size. The
lesson is not "copy 64 KB" — it is that the *artifact format* is the product: a single
`.wasm` is hashable, verifiable, and droppable into a browser tab.

**Adaptation:** M-01's manifest should describe a **cart**, not a directory of 24 files;
M-04's "one download" becomes "a host page that takes a cart"; M-05's reference shards
should be built inside a budget that has been proven finishable by a jam scene.

**Task:** M-01, M-04, M-05. Cost: the 2 MB payload in the plan is 32× looser than a
proven format; keeping it may be right for art-heavy shards, but it should be a *declared
tier*, not the only tier.

## 4. Uxn / Varvara and permacomputing: permanence as a design goal

| fact | value | source |
|---|---|---|
| Uxn | a stack VM with **64 KB of RAM**, programmed in Uxntal; Varvara is the device layer | 100r.co, wiki.xxiivv.com |
| ports | dozens of hosts, including **UxnWasm** in the browser and handhelds | hundredrabbits/awesome-uxn |
| stated purpose | a **portability layer** for small tools and games; "the thin waist" | 100r.co, jsbarretto.com |
| culture | **permacomputing** — maximise hardware lifespan, minimise energy, use what exists | permacomputing.net; 100r's "Permacomputing 101" |

Metthew's low-end budget is currently an apology ("720p/30 on integrated graphics"). The
permacomputing scene treats the same constraint as an ethic, and Uxn shows the engineering
consequence: if the runtime is 100 lines and the artifact is 64 KB, nothing in the stack
can strand the player on a dead platform.

**Adaptation:** adopt the vocabulary deliberately. A shard that runs on a 4 GB machine in
2026 will run on a 4 GB machine in 2046 *if the runtime is small enough to reimplement* —
which is exactly the property Uxn is built for and which no engine-based shard has.

**Task:** M-11 (portability matrix gains a "reimplementable runtime" row), M-04, and the
player-facing framing in §1.

## 5. iroh 1.0: the transport M-08 was missing

| fact | value | source |
|---|---|---|
| **iroh 1.0 released** | 9 Jul 2026 | iroh.computer blog |
| addressing | endpoints identified by an **Ed25519 public key**; "dial keys instead of IP addresses" | blog, github |
| transport | QUIC (quinn), TLS with raw public keys | blog |
| blobs | **BLAKE3 verified streaming**, content-addressed, resumable | blog |
| NAT traversal | hole punching modelled on Tailscale's approach, **relay fallback** when impossible; ~90% success reported | blog; TechTimes 16 Jun 2026 (secondary) |
| discovery | "global content discovery" over the same endpoint layer | blog, 30 Sep 2026 |
| bindings | most major languages | blog |

This is the difference between "a file on a USB stick" and "a friend three towns away
pastes a code and gets the shard". It also solves the version-skew problem structurally:
content addressing *is* the id.

**Adaptation:** M-08 gets a substrate and a fallback ladder — LAN → hole punch → relay →
USB. The contract stays "a file you own"; iroh only decides how the bytes move. Magic
Wormhole-style short codes remain the human interface; the key is the machine interface.

**Task:** M-08, M-09. Cost: a Rust dependency in the host, and a relay someone must run —
which is a small, honest centralisation that should be named rather than hidden.

## 6. Deterministic simulation testing is now ordinary engineering

| fact | date | source |
|---|---|---|
| TigerBeetle's VOPR: a failure is reproduced from **the seed and the git commit hash** | current docs | tigerbeetle `docs/internals/vopr.md` |
| "Protocol-aware deterministic simulation testing" — invariants checked inside the simulation, not just at the edge | 20 Aug 2026 | tigerbeetle blog; Antithesis talk 13 May 2026 |
| Antithesis sells DST as a service; WarpStream runs its whole SaaS under it | 2024–2026 | warpstream, antithesis |
| The honest caveat: "DST only works for software that's been written in a deterministic way" | 2024 | materializedview.io |

Factorio's practice is the game-shaped version of the same discipline: a **desync report**
that ships both peers' save states, and a **heavy mode** that saves and reloads every tick
to catch a state change the moment it happens.

**Adaptation:** M-06 should stop inventing a verification story and copy the vocabulary:
`seed + commit + input log` reproduces a run exactly; a failure produces a **two-sided
report** (my state, your state, the first divergent tick); and a "heavy mode" validator
that reloads from the hash chain every N ticks is cheap to build and catches the class of
bug that only appears after 20 minutes.

**Task:** M-06, M-03 (the validator's `--heavy` mode). Cost: one extra code path.

## 7. Rollback netcode already ships as a library

| fact | value | source |
|---|---|---|
| GGRS / `bevy_ggrs` | peer-to-peer **rollback** networking; syncs *inputs only*, snapshots and rewinds state | crates.io, 26 Jun 2026 |
| the known hard part | physics + rollback + determinism is still unsolved enough that practitioners publish failures | johanhelsing "I failed to build multiplayer pong"; bevy_gaff experiment |
| the prep list | "preparing your game for deterministic netcode" — fixed timestep, no wall-clock, no float drift | yal.cc |

**Adaptation:** M-06's multiplayer half should be specified as *input-log lockstep with
optional rollback*, GGRS-shaped, and the plan should say out loud that physics shards are
the ones that will hurt. For a ≤4-player circle on a LAN, plain lockstep is enough; rollback
is the upgrade path, not the baseline.

**Task:** M-06. Cost: none if the baseline stays lockstep.

## 8. Verification is already a social role, not a server

| fact | value | source |
|---|---|---|
| TASVideos **console verification**: a recorded run can be replayed and checked on real hardware | guide, page dated 27 Jun 2026 | tasvideos.org |
| The **TAS Dump** file format (first published 2025) stores everything needed to replay a run | 2025–2026 | same |
| Speedrun verification in general: proofs are replays, hashes and video, adjudicated by humans with a stated ruleset | community practice | speedrun/TAS communities |

Metthew's "contest" loop is this, with a hash instead of a stopwatch. The important
finding is that the social form already exists and **does not require a ranking server**:
a trusted person replays the run and says so.

**Adaptation:** M-09 and M-10 gain a named role — **verifier** — with a written procedure
(replay, compare hashes, publish a signed note). This is also the answer to "who stops a
cheater": not an anti-cheat, a person with a command.

**Task:** M-09, M-10. Cost: a document and a key.

## 9. Aficionados are already rebuilding the industry's artifacts

| fact | date | source |
|---|---|---|
| **N64Recomp**: static recompilation of N64 binaries to C, then native ports for any platform | 2024, list refreshed Oct 2026 | readonlymemo, PCGamingWiki unofficial ports |
| *Ship of Harkinian* (Ocarina of Time decompilation port) at **v9.2.3** | Apr 2026 | readonlymemo list |
| The scene now has its own catalogues and handheld targets | 2026 | heldgames, PCGamingWiki |

This is the creative adaptation the plan is missing entirely: a circle does not only *mint*
new shards, it can **recover** old ones. Decompilation and recompilation produce something
a store cannot sell and a shutdown cannot kill — a native port that runs on hardware the
publisher never targeted.

**Adaptation (new capability, candidate task M-13):** a *recovery* track where a shard
wraps a legally-obtained, clean-room-recompiled game, with the provenance rules this repo
already applies to evidence (`raw + sha + url + tier`). The legal line is not invented here:
recompilation projects exist in a grey zone that depends on jurisdiction and on whether the
port ships the original assets. Metthew must state that it does not ship assets, only
build instructions and patches — or it should not do this at all.

**Task:** candidate M-13, not in the ledger. Cost: legal risk that must be written down
before any code.

## 10. The constrained-hardware audience already exists and already sideloads

| fact | date | source |
|---|---|---|
| Playdate ships **Catalog** (a curated on-device store) *and* sideloading over USB/Wi-Fi; itch.io is the other half of the scene | Catalog 7 Mar 2023, scene ongoing | play.date, playdate-wiki, r/PlaydateConsole |
| **Season 3** begins 8 Oct 2026 — the "here is a game every week" model still works | 9 Sep 2026 update | Playdate Update 10 |
| PICO-8 carts remain 32 KB (15,360 B compressed code); **Picotron 0.2.2 added Splore** (the in-tool cart browser) | Jan 2026 | Lexaloffle, Wikipedia |
| Picotron is 480×270 / 64 colours — the same author's answer to "the scene outgrew the console" | 2024–2026 | lexaloffle.com |

Two things follow. First, a low-end, tiny-artifact game scene is not hypothetical — it has
hardware, seasons, a storefront *and* a sideload path that people use. Second, the shop and
the sideload coexist: Catalog did not kill itch, and PICO-8's Splore is a *browser inside
the tool*, which is the cheapest possible catalogue and exactly the shape R5 needs.

**Adaptation:** M-11 adds handheld rows; M-05 should target a Playdate-class or PICO-8-class
budget for one of its three shards, to prove the contract survives a real constraint rather
than a generous one. And "Splore in the player" is the federated-catalogue idea with a
shipping precedent.

**Task:** M-05, M-08, M-11.

## 11. The centralised counter-move, and how it is going

| fact | date | source |
|---|---|---|
| **s&box** (Facepunch's UGC platform, Source 2) launched on Steam | 28 Apr 2026 | sbox.game news |
| Launch reception: heavily negative, with complaints about **AI-generated content flooding the platform** | 2026 (secondary) | PC Gamer/Facebook post, 2 weeks ago |
| *Dreams* (Media Molecule) ended live support | 1 Sep 2023 | Media Molecule, Polygon |

The industry's answer to "let players make games" is a paid platform on Steam that drowns
in generated content within months. Metthew's advantage is not that it is nicer — it is
that it has **no feed to flood**. Its weakness is the mirror image: no feed means no
discovery, which is R5 again, and a circle with no filter is a circle where the loudest
friend decides what gets played.

**Adaptation:** the plan should claim "no feed to flood" as a *feature with a cost*, and
M-10 should say how a circle decides what to play next (a rotation? a vote? whoever mints
it hosts it?). That is a governance question a platform would have answered with an
algorithm.

**Task:** M-10 (governance), positioning.

## 12. A federated catalogue substrate already exists

| fact | value | source |
|---|---|---|
| **NIP-94** — signed *file metadata* events, so relays can index and filter shared files | spec | nips.nostr.com/94 |
| **NIP-96** — HTTP file storage integration; after upload the client publishes a NIP-94 event | Jan 2024 | nips.nostr.com/96 |
| Relays are interchangeable and cheap to run; the same pattern is being used for peer discovery by other p2p projects | 2026 | syncthing forum discussion, Apr 2026 |

This is the R5 fix that costs almost nothing: each circle publishes a signed index as
events, anyone can follow a relay or mirror the index, and nobody owns the catalogue. It is
a store in the only sense that matters to a player (search finds things) and not a store in
the sense that matters to a publisher (nobody can delist you).

**Adaptation:** M-08 ships a `circle.index.json` **and** an optional Nostr publisher; the
index format is the contract, the relay is an implementation detail.

**Task:** M-08. Cost: a dependency on a protocol whose culture is not gaming — which is
also why it will not be flooded by game spam for a while.

## 13. AI has made minting cheap, so verification is now the scarce good

| fact | date | source |
|---|---|---|
| "Vibe coding" a game is now a mainstream activity with a tool ecosystem around it | 2026 | questera, ziva.sh, digitalbunker |
| An indie developer **stopped sharing** after AI clones of her game appeared | 28 Sep 2026 | Chosun (secondary) |
| s&box's launch complaints centre on generated content volume | 2026 | §11 |

If anyone can produce a playable shard in an afternoon, then *authorship* stops being the
bottleneck and *trust* becomes it. This inverts the plan's implicit assumption that shards
are scarce and precious: they will be plentiful and disposable. It also makes the
validator, the provenance field and the verifier role (§8) the load-bearing parts of
Metthew, and makes the 24-file budget look like a quaint constraint rather than a
guarantee of quality.

**Adaptation:** keep the validator-first ordering (it is now the *only* defensible ordering),
add the per-asset provenance of R9 as a v1 field, and expect M-05's reference shards to be
judged on whether a stranger can tell them from generated slop.

**Task:** M-03, M-01. Cost: none; it is already the critical path.

## 14. What to avoid: the word "decentralised" is poisoned

| fact | value | source |
|---|---|---|
| **93%** of ~3,600 tracked blockchain games were dead by 2024 | DappRadar via blockleaders | 2024 |
| More than **90%** of GameFi projects effectively dead after a **$15 billion** boom; token prices down ~95% | CoinDesk, 23 Apr 2026 | 2026 |
| **Abstract** (Igloo's game chain) shuts down **15 Dec 2026**; Immutable exits internal game development; IMX ~-99% from ATH | Oct 2026 / Jun 2026 | cryptoticker, blockchaingamer |
| Academics describe the play-to-earn structure as a Ponzi | Sep 2025 | Cornell |

Every one of these projects used the phrase "decentralised gaming". Whatever Metthew is, it
must not be findable by that search: no token, no wallet, no chain, and a name that a
parent can say. The failure mode to study is not the technology — it is that the token
became the product and the game became the marketing.

**Task:** positioning, naming (R11). Cost: a thesaurus.

---

## 15. Consolidated: what changes where

| # | strand | dates | changes | verdict |
|---|---|---|---|---|
| S1 | Stop Destroying Videogames closed | Jan–Jun 2026 | M-10, framing | **adopt**: practice, not policy |
| S2 | Wasm 3.0 deterministic profile | Sep 2025 – Oct 2026 | M-01, M-03, M-06 | **adopt**: replaces 3 open problems |
| S3 | WASM-4 cartridge | 2021–2026 | M-01, M-04, M-05 | **adopt**: the artifact is the product |
| S4 | Uxn / permacomputing | ongoing | M-04, M-11, framing | **adopt**: permanence as a goal |
| S5 | iroh 1.0 | Jul–Sep 2026 | M-08 | **adopt**: transport ladder |
| S6 | DST + Factorio desync practice | 2026 | M-03, M-06 | **adopt**: seed+commit+two-sided report |
| S7 | GGRS rollback | Jun 2026 | M-06 | **adapt**: lockstep first, rollback later |
| S8 | TAS console verification | 2025–2026 | M-09, M-10 | **adopt**: the verifier role |
| S9 | N64Recomp / decompilation | 2024–2026 | candidate M-13 | **evaluate**: legal line must be stated first |
| S10 | Playdate / Picotron / Splore | 2023–2026 | M-05, M-08, M-11 | **adopt**: proof the audience exists |
| S11 | s&box launch, Dreams sunset | 2023–2026 | M-10, positioning | **avoid their model**, claim the cost |
| S12 | Nostr NIP-94/96 catalogue | 2024–2026 | M-08 | **adopt**: catalogue without a store |
| S13 | AI minting boom | 2026 | M-01, M-03 | **absorb**: verification is the product |
| S14 | web3 gaming collapse | 2024–2026 | positioning, naming | **avoid**: the word is poisoned |

## 16. The two proposals this research adds

1. **Candidate task M-13 — recovery track.** A shard that wraps a clean-room recompiled or
   decompiled game, with provenance recorded as `raw + sha + url + tier` and an explicit
   statement that no original assets are shipped. Value: it is the one creative adaptation
   with a 30-year track record, it needs no store, and it is what aficionados already do.
   Gate: a written legal position *before* code, not after.
2. **Contract refinement — replace "sandboxed loader" with "Wasm 3.0 host, deterministic
   profile, threads and SIMD off".** This is pending change #1 in the rejection analysis,
   upgraded from an engineering wish to a standard.

## 17. What this document does not establish

- **Dates and numbers come from search results and three pages read in full** (the EU
  reply page, WASM-4 docs, the iroh 1.0 post). Everything else is snippet-level, including
  the s&box reception, the iroh hole-punch rate and the AI-clone story, all of which are
  secondary sources. Treat them as leads to verify, not as findings.
- **No strand was tested here.** Nothing in this document was built, benchmarked or played;
  in particular the Wasm determinism claim is a claim about a *profile* of a specification,
  and whether Wasmtime's implementation plus a given toolchain holds it under load is a
  measurement Metthew has not made.
- **"Adopt" means "should be evaluated against a task", not "accepted".** Acceptance is the
  user's, and none of these are in scope until M-01's freeze decides them.
