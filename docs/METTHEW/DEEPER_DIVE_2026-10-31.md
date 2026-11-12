# METTHEW — deeper dive

**What this is:** a verification pass over the four Metthew documents, plus the
measurements they were missing. The plan's authority rests on one sentence — *measured,
not paraphrased* — so every number it leans on was re-read from the artefact rather
than trusted.
**Date:** 2026-10-31 · **Lane:** freebuff · **Verdict:** the plan is sound and its
figures are accurate; two of its load-bearing assumptions are understated, and the
one number that decides M-05's shape was never taken.

---

## 1. Claim-by-claim verdict

| claim | where | verdict | evidence |
|---|---|---|---|
| `SHARDS_PER_LIMB = 24` | `tools/yggdrasil_census.py:49` | **VERIFIED** | constant present |
| 1,055 modules → 122 shards, 0 straddling (naive would give 22) | plan §1 | **VERIFIED exactly** | `~/yggdrasil_forest/shard_plan.json` → `totals: modules 1055, shards 122, straddling_shards 0, continuous_straddling 22, wasted_slots 1873`; `mode: limb_aligned`, `shard_size: 24` |
| 92 of 122 liftable, 30 blocked by named references | plan §1 | **VERIFIED exactly** | `yggdrasil_census.json` → `contracts.liftability: {shards 122, liftable 92, blocked 30}` |
| `GRAPH_LANGUAGES` + `uninstrumented` derives from a table | plan §1 | **VERIFIED** | `yggdrasil_census.py:1441`, and the explanatory string at `:2418` |
| `polyglot.run_shard(lang)` returns JSON-serialisable, golden token, missing toolchain = clean skip | plan §1 | **VERIFIED** | `polyglot.py:8,10,28` (`GOLDEN_TOKEN = "AZAZAEL-GOLDEN-OK"`), `:70 run_shard`, `GOLDEN_SNIPPETS` for 6 languages |
| antibodies: `signature→probe→instances→status`, `seeking→neutralized→immune`, immune needs three consecutive passes | plan §1 | **VERIFIED in code**, base is thin (see §4) | `antibodies.py:10-12`, `_IMMUNE_STREAK = 3` (`:39`), `advance()` at `:220` |
| no machine-readable shard contract and no validator | README | **VERIFIED** | `src/SHARDS/` holds only `polyglot.py`, `registry.py`, `antibodies.py`, `antibodies.json`, `sweep.json`, `antibodies.lock`, `__init__.py`; a repo-wide search for `shard*.json`, `*shard*schema*`, `metthew*` returns nothing |
| `src/INTERPRETERS/*` run raw subprocesses with only cwd+timeout | rejection analysis R2 | **VERIFIED, and understated** | see §2 |

Nothing in the four documents is wrong. Two things are more dangerous than written. One
number was never taken.

---

## 2. Correction: the sandbox gap is a leak, not just an absence

The rejection analysis says `src/INTERPRETERS/*` run raw subprocesses with **only
cwd+timeout**. Reading the call sites, the argument list is *shorter than that* and the
consequence is *larger*:

```python
result = subprocess.run(
    cmd,
    cwd=str(module_root or source.parent),
    env=self._go_env(),        # go_runner.py:133
    capture_output=True,
    timeout=60,
)
```

and `_go_env()` is (`go_runner.py:87-94`):

```python
env = os.environ.copy()        # the player's whole environment
if self.goos:  env["GOOS"] = self.goos
if self.goarch: env["GOARCH"] = self.goarch
return env
```

A grep for `sandbox|rlimit|creationflags|CREATE_|job_object|seccomp|firejail|network`
across `src/INTERPRETERS/*.py` returns **nothing**. So a shard executed this way gets:

- the full filesystem, as the player's user;
- unrestricted network;
- **the player's entire environment**, including any API token exported in that shell.
  This tree hands lanes a PMB bearer token that way, and providers are configured from
  env. A shard is arbitrary code from a friend's USB stick.

"Not sandboxed" reads as *containment pending*. The accurate statement for M-01/M-03 is
*containment absent **and** secrets shared*. That is a contract field, not a footnote: a
manifest that cannot declare its execution model cannot be validated for one.

---

## 3. The measurement that was never taken: tick cost

Plan §4 gates load time, fps, determinism, payload and validator wall clock. It has **no
tick-cost gate**, and the only per-invocation timings that exist in the tree say that
model cannot host a game.

`src/SHARDS/sweep.json` (n=10, 2026-10-29) measures process start + golden run per
language. A 30 fps budget is **33.3 ms per tick**:

| language | median ms | vs 33.3 ms | verdict |
|---|---|---|---|
| python | 0.7 | 1/47th | FIT |
| javascript | 31.6 | 1.05× | FIT, no headroom |
| bash | 92.2 | 2.8× over | OVER |
| rust | 122.2 | 3.7× over | OVER |
| go | 146.1 | 4.4× over | OVER |
| powershell | 248.6 | 7.5× over | OVER |

`run_shard()` is **one subprocess per call**. A shard that ticks through it can therefore
reach 30 fps in Python and JavaScript only; PowerShell manages ~4 fps. This is not a
criticism of `polyglot.py` — its job is a golden whole-program run, which is exactly what
it does well — but the plan inherits its execution model without naming it, and the
number that rules four of six languages out is absent from the document.

**What it decides:** M-05's language choice is already made for it. A Metthew shard needs
a **resident host process** (one process per shard with a fixed-step loop and a message
boundary), not a call-per-tick. That is also the cheaper answer for M-06: a resident
process has one address space per tick chain, so the "no weights, no handles across the
boundary" property that makes the polyglot contract clean becomes a *runtime obligation*
rather than an achieved fact.

**Gate to add to M-02:** `tick_cost_median_ms` and `tick_cost_p99_ms` for the resident
host, measured on the reference box, with the pass condition `p99 < 33.3 ms at 720p` and
the scene named. Without it, "720p/30" is a resolution, not a budget.

---

## 4. Two smaller corrections

**The 24-file cap is borrowed from a rule that wastes 64% of its own capacity.**
`shard_plan.json` reports `wasted_slots: 1873` against 122 × 24 = 2,928 slots — limb
alignment costs this tree 64% of its packing capacity, and it does so for a reason that
does not transfer: a shard may not span two trees because the census instruments a tree
at a time. A game shard has no such constraint. Metthew should either justify 24 files on
game grounds or drop the file count and keep the packed size (2 MB), which is the gate a
player can actually feel. Carrying over a number whose original justification is absent
is how a budget becomes folklore.

**The antibody store proves the end state only.** `antibodies.json` generation 8 holds
**2 deeds, both `immune`** (`seq-range-mismatch`, `stale-table-routing`). There is no
live `seeking` or `neutralized` example, so the transitions M-07 depends on are asserted
by code and observed only at their terminus. M-07's acceptance already demands positive
*and* negative controls; it should also ship a **transition fixture** that walks
`seeking → neutralized → immune` and one that *refuses* to advance without a recorded
seek — otherwise the immune state is indistinguishable from a default that was never
earned.

**Measurement hygiene:** the sweep ran on Python **3.14.7**; the production interpreter
for this tree is the 3.15t free-threaded build. Re-measurement belongs in M-02 so the
tick budget is quoted from the interpreter a shard will actually run under.

---

## 5. What this changes for the two nearest deliverables

**M-01 (manifest spec)** — add three fields the contract cannot be validated without,
all of them consequences of §2–§4:

- `execution_model`: `resident_host` | `one_shot` — and, if `one_shot`, a declared
  `max_ticks` so the validator can compute the wall clock instead of discovering it.
- `env_allowlist`: the env keys the shard may see. Default **empty**, because the
  current path hands over everything (§2). A shard that needs one declares it and the
  validator checks the declaration, not the behaviour.
- `tick_budget`: ms p50/p99 from M-02, per scene.

**M-03 (validator)** — the negative controls it already promises plus two more, each
naming a defect this dive measured:

- a fixture that reads a secret from `os.environ` and must be **blocked by
  `env_allowlist`** (today it would succeed);
- a fixture whose tick cost exceeds its declared `tick_budget` and must be **blocked on
  measurement**, not on trust.

---

## 6. The single next action

**Do M-01, and put `execution_model` and `env_allowlist` in it.** They are cheap now and
expensive later: every reference shard in M-05 and every replay in M-06 inherits whatever
execution model M-01 freezes, and the two defects above are both fields rather than
features. The tick number (§3) says the frozen model must be `resident_host`, and that
decision costs nothing today and a rewrite of M-05 tomorrow.

Nothing here is accepted scope. §3 and §4 are measurements; §5 is a proposal; the
`execution_model`/`env_allowlist` fields and the two extra negative controls need a
decision before M-01 freezes, alongside the five already pending.
