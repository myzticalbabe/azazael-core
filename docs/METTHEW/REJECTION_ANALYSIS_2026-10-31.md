# METTHEW — rejection analysis against existing mod and co-op frameworks

**Date:** 2026-10-31 · **Status:** analysis, changes no contract yet
**Question:** where does the Metthew shard contract lose to things gamers already use,
and what would make them bounce off it?
**Method:** compare on the six things that decide whether a community framework is
adopted — *authoring cost, dependency handling, trust, discovery, transport, and
first-run friction*. Every claim below is from the framework's own docs, wiki or issue
tracker, dated; nothing here was played first-hand, and that limit is real (see §6).

---

## 1. The comparators, and the column that matters

| framework | artifact | who guarantees the code is intact | how you find one | transport |
|---|---|---|---|---|
| **PICO-8 / TIC-80** (fantasy consoles) | 32 KB cart, 15,360 B compressed code, 8,192 tokens; `.p8.png` is a *picture* of the cart | nothing — no integrity check at all | Lexaloffle BBS, itch.io | download a file |
| **Doom WAD / PK3** | one self-contained file, engine separate | nothing (source ports trust the WAD) | Doomworld, idgames | download, or co-op with `-file` |
| **Luanti** (ex-Minetest) | server-side Lua mods; the **server sends media to the client**, the client does not install the mod | the **server is authoritative**; Lua sandbox on by default restricts filesystem and exec | ContentDB | server pushes content |
| **Factorio** | mods + scenario Lua, versioned on a portal | **deterministic lockstep**: every peer simulates every tick; a mismatch is a *desync*, reported with both save states and isolated with heavy mode | official Mod Portal with dependency resolution | server, or peer-to-peer with map transfer |
| **OpenTTD** | NewGRF files + a game version | server sends periodic **checksums of game state** (the RNG state); a mismatched player is ejected | BaNaNaS in-game downloader | server with join-by-IP |
| **SMAPI** (Stardew) | C#/Lua mods against a versioned modding API | **a maintained compatibility list**: every known mod vs the latest game and API version | Nexus, with SMAPI's list as the arbiter | manual install |
| **Minecraft** (datapacks, Fabric) | datapacks are data-only; Fabric mods are code | server-authoritative for play; Modrinth/CurseForge for identity and versions | Modrinth / CurseForge / CurseForge app | server, or LAN open-to-LAN |
| **Steam Workshop / Remote Play Together** | mods / a *streamed session* | Valve: identity, versioning, and for Remote Play the host's machine *is* the authority — the friend needs **no account and no copy** | built into the client | Valve's relays |

The elephant: **every one of these re-centralises somewhere** — a portal, a server, a
store, or Valve's relay. The decentralised-game-scene fantasy is 30 years old and the
surviving examples (Doom, PICO-8) are small, file-based, and *do not try to be
trustless*: they trust the file, and the community's remedy for a bad file is a forum
post, not a hash chain.

## 2. What Metthew is already better at (so the criticism is not one-sided)

- **Liftability is a guarantee none of them make.** A Factorio mod, a Fabric mod, a
  Luanti mod all may reference the whole game and arbitrary APIs. Metthew's "no
  reference escapes the shard" is what makes the 4 GB / 720p target credible at all.
  The closest existing property is a Doom WAD being self-contained — and the WAD scene
  survived 30 years on exactly that.
- **The hash chain is stronger than OpenTTD's periodic RNG checksum** and vastly
  stronger than Luanti CSM, which is client-side code with no verification.
- **No account is a genuine advantage** over Workshop and over Remote Play (both need
  Steam), *if* the tool is one download — which today it is not (§3 R1).
- **Exploit-as-content has no equivalent anywhere.** Mod scenes patch by version bump;
  none of them hunts instances of a known cheat across every shard in a circle.

## 3. Where it gets rejected, ranked by how many players it loses

### R1 — The setup tax kills it before the first game (highest)

PICO-8's whole scene exists because the *tool is one download and a cart is
double-clickable*. Metthew's first-run story is: install Python 3, get the validator,
get a bundle, get a hash to match. On a fresh Windows laptop with no Python that is
where it ends, and "no toolchain beyond python3" is a claim about the author's laptop,
not the player's.

**Fix:** the distribution unit must be one self-contained thing — a browser/WASM build
or a single packaged binary that *is* the loader, validator and player. Note the repo
reality: Nuitka standalone builds are currently blocked here (transformers plugin), so
the browser target is the honest path, and it also gives the sandbox in R2 for free.

### R2 — A shard is arbitrary code from a friend's USB stick, with no sandbox at all

This is measured in this tree, not assumed: `src/SHARDS/polyglot.py` dispatches through
the `src/INTERPRETERS/*` runners, which `subprocess.run` a file with a `cwd` and a
timeout — **no sandbox, no filesystem restriction, no network restriction**
(`node_runner.run_code` writes a temp file and runs it; the others are the same shape).

The ecosystems Metthew would replace are *ahead* here:

- Luanti's Lua sandbox is on by default and restricts filesystem and exec — and it
  **still** had a sandbox escape: `GHSA-g596-mf82-w8c3` / CVE-2026-41196 (April 2026),
  "a malicious mod can trivially escape the sandboxed Lua environment".
- Luanti's client-side mods were exploited to **cheat on servers** (issue #6662, 2017)
  — the exact failure mode of putting game logic on the peer's machine.

So the contract's trust story ("golden run + hash chain") protects *fairness* and does
nothing for *safety*. A parent reading "add this file from Discord and it runs" declines,
correctly.

**Fix:** a sandboxed loader is part of the contract, not a hardening pass — WASM (the
one plugin sandbox the community actually recommends) or a restricted VM with no
clock, no RNG and no I/O. That single change also makes determinism cheap, because the
nondeterminism sources are gone by construction.

### R3 — The anti-cheat machinery is aimed at a threat a circle does not have

Factorio and OpenTTD pay the determinism tax because they host **ranked strangers** and
the disagreement is worth money or ladder position. Five friends exchanging USB sticks
do not cheat each other for rank; a griefer is handled by not inviting them again. Yet
Metthew makes every author satisfy a per-tick hash chain, which is a large authoring
tax paid against a threat the audience mostly does not face. Factorio's own wiki says
desyncs are *errors*, and its forums carry **eight years** of recurring desync reports
from mods alone — that is the maintenance burden being imported.

**Fix:** determinism becomes a **declared property** (`"competitive": true`), not a
universal requirement. Solo, puzzle and doc-only shards skip it. A circle that wants
fair play opts in and pays the tax knowingly.

### R4 — "No external references" means no shared engine and no maintenance

Every comparator has a shared layer precisely because duplication kills ecosystems:
Factorio's portal resolves versioned dependencies, SMAPI exists as *the* versioned API
plus a compatibility list, Luanti has a mod API, Fabric has a loader API. Metthew bans
external references outright, so a movement bug in the arena engine is fixed by
re-shipping every shard that copied it — and with a 24-file budget, the engine eats a
big share of the 24.

**Fix:** declared **hubs as versioned shared libraries** — the census already models
this ("mycelium": a hub touched by many trees). Declaring a hub and counting the
coupling is strictly better than forbidding it and getting silent duplication.

### R5 — Discovery is the actual product, and the plan declines to build it

Modrinth, CurseForge, ContentDB, BaNaNaS, Workshop, itch, the Lexaloffle BBS: the
reason these exist is not hosting, it is *finding*. A file-exchange protocol with no
index gives a circle N bundles and no list. This is the failure the plan already admits
("discovery is social"), but admitting it does not remove it — it removes the players
who are not already in a group of five.

**Fix:** a **federated signed index** — each circle publishes an index file that others
can merge, like an OPML/RSS feed. Not a store, no ownership, but searchable and
shareable. It belongs in M-08 or the circle has no catalogue at all.

### R6 — Version skew reads as "your game is broken"

One friend on v1.2, another on v1.3, hashes disagree, and the shard refuses to start.
OpenTTD's multiplayer support pages and forums are full of exactly this ("version
mismatch"), and Factorio's answer is a desync loop. Players do not read "content
addressing" — they read "it doesn't work".

**Fix:** content-addressed ids (`arena@sha256:…`) so two versions can coexist, plus a
"play the version the host has" fallback. Never a bare refusal with a version number.

### R7 — "LAN only" assumes friends are in the same building

They are not. The existing answers are Tailscale/ZeroTier (a virtual LAN), Hamachi
before that, and Steam Remote Play Together — which proves players will accept
*streaming* a session from the host's machine, no second copy, no second account, if
that is what it takes to play tonight.

**Fix:** document a relay/virtual-LAN path in M-08 and allow the "one machine renders,
others stream" mode as a first-class transport. Streaming also sidesteps R2 for the
joiners, who never run the shard's code at all.

### R8 — There is no social graph, so Steam or Discord stays in the loop

Invites, voice, scheduling, "are you online" — Metthew has none, and a circle will use
Discord for all of it. That is fine, but it means the plan's framing ("replacement to
the centralised industry") oversells: Metthew can replace the **store and the
distribution**, not the **social layer**, and a sceptic's first sentence will be "I
still need Steam to invite you".

**Fix:** say so in the player-facing text, and design the bundle so it can travel
through whatever chat app the circle already uses — a file that a Discord attachment
can carry is the actual integration.

### R9 — No arbiter for asset provenance

A `licence` field is decoration without an enforcement story. Mod scenes die on asset
disputes, and every comparator has a platform to take things down. A circle has none.

**Fix:** per-asset provenance in the manifest (sha + declared origin), which is exactly
the discipline this tree already applies to evidence (`FETCH_LOG` raw+sha+url+tier).
It does not create an arbiter, but it makes "where did this sprite come from" answerable.

### R10 — The budget has no aesthetic, so the games will look worse than free itch games

PICO-8's 32 KB works because the palette, resolution and sound chips *are* the style —
the constraint is visible and desirable. Metthew's "24 files / 2 MB" is a number with no
look attached, and without one the shards are simply uglier than a free itch.io game
made in Godot with no limits. Low-end hardware players are not asking for smaller; they
are asking for *runs*.

**Fix:** either adopt a hard visual identity (then the budget is a feature and a
recognisable one) or raise the payload budget and keep only the tick/memory gates, which
are what actually make 4 GB work.

### R11 — Naming collisions are free to fix and expensive to keep

"Shard" already means a Destiny currency, a Minecraft world slice, a database partition.
"Metthew" reads as a misspelling of Matthew and will be typed that way. Both are cheap
to change now and unfixable later.

### R12 — Mandatory golden runs exclude whole genres

Any simulation with float physics, time-seeded procedural generation, or audio-timing
gameplay cannot produce a stable per-tick hash without a numeric policy. Requiring one
everywhere quietly bans those genres rather than stating the ban.

**Fix:** the manifest declares *what* is deterministic (seed, tick, input) and the
validator asserts only that. Genres that cannot declare it are allowed to say so.

## 4. The five contract changes that must land before M-01 freezes

| # | change | why it cannot wait |
|---|---|---|
| 1 | **Sandboxed loader** — now specified in [BLEEDING_EDGE_ADAPTATIONS_2026-10-31.md](BLEEDING_EDGE_ADAPTATIONS_2026-10-31.md) §2 as a **Wasm 3.0 host on the deterministic execution profile, threads and SIMD disabled** | R2 — running a friend's bundle is currently running their code; it is also what makes determinism cheap |
| 2 | **One-download player** (browser/WASM target) | R1 — everything else is unreachable if step one is "install Python" |
| 3 | **Hubs as versioned libraries**, declared and counted | R4 — otherwise there is no engine to fix once, and 24 files is not enough to duplicate one |
| 4 | **`competitive` flag** on determinism | R3 — the hash chain is a tax against strangers, and the circle has none |
| 5 | **Content-addressed shard ids** + host-version fallback | R6 — version skew otherwise reads as a broken game |

Deferred (still real): federated index (R5, M-08), relay/stream transport (R7, M-08),
asset provenance (R9, M-01 field), visual identity or budget change (R10, M-02),
naming (R11, now or never).

## 5. The cheapest falsification test

The plan's M-10 play test should be run as a **rejection test**, not a success test:

1. Hand a bundle to a person who is not the author, on a Windows laptop **with no
   Python installed**, and time how long until they are playing. If it exceeds ten
   minutes or fails, R1 is confirmed and the contract is wrong — not the player.
2. Ask them to install it from a second person's copy and play together from two
   different houses. If that needs a virtual LAN the tester cannot set up alone, R7 is
   confirmed.
3. Ask them, unprompted, what they would change. Record the first sentence verbatim;
   that sentence is the real rejection reason, and it will not be any of the ones above.

## 6. What this analysis does not establish

- **No framework was played or measured here.** Claims are from documentation, wikis,
  issue trackers and advisories, all cited with dates in §1–§3. Numbers about play
  experience are the frameworks' own claims.
- **The "gamers will reject it" conclusion is a prediction, not a measurement.** The
  falsification test in §5 is the measurement, and it has not been run.
- **Metthew has no users to survey.** Every rejection cause above is inferred from how
  the comparators' communities behave, which is the best available evidence and still
  second-hand.
