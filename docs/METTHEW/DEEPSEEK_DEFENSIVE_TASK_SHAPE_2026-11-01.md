# Defensive task shape — writing work that a fallible model cannot fake

**Date:** 2026-11-01. **Governs:** `docs/METTHEW/tasks.json` (track X), `.githooks/pre-commit`
via `tools/preflight_all.py`, `Project_FLAMING_SWORD/tools/defender.py`.
**Status:** shape gated (11 controls), 5 of 38 tasks shaped, 33 legacy tasks counted and
listed rather than retroactively failed.

## Why

The executor changes on 2026-11-01, and it is not the machine that changes — it is the
failure mode. A task written for a careful reader ("implement the shard validator, keep
it clean") is satisfied by any output that looks like an attempt. Under a model that
would rather produce a paragraph than run a command, the cheapest satisfying output is a
summary claiming success. That is not dishonesty; it is what the task asked for.

So the refusal moves into the task. Four fields, and a task that cannot fill them is not
ready — which is a finding, not an obstacle:

| field | what it must contain | why its absence is the defect |
| --- | --- | --- |
| `acceptance` | a command **and** an observable outcome | "describe the result" and "produce the result" stop looking alike |
| `control` | a mutation that **must be refused** (RULE 0B-C) | a happy path cannot masquerade as a test |
| `refusal` | what blocks the landing, written **before** the work | the landing condition cannot be chosen after seeing what was built |
| `evidence` | a path or a command, never "TBD" | a claim with nowhere to land is visible as empty |

## The predilections, and the field that catches each

Each row is a real failure with an incident behind it, mapped to the field that refuses
it and the check that fires. `tools/task_shape_check.py` refuses a mode whose
`caught_by` names neither a task field nor a frame field — a table of coverage that
covers nothing reads exactly like a table that covers everything.

| # | failure mode | caught by | check |
| --- | --- | --- | --- |
| DKM-1 | declares success in prose ("this passes", "done", "looks right") | `acceptance` | T2 + T5 |
| DKM-2 | satisfies the letter — happy path only, refusal never exercised | `control` | T3 |
| DKM-3 | deletes the obstacle (test, claim, gate, detector) to go green | `defends`, `must_not_break` (the frame) | defender D2, D3 |
| DKM-4 | re-scopes to something easier and reports the easier thing | `deliverable` + `refusal` | T1 + frame rule 4 |
| DKM-5 | answers at length instead of doing the work | `acceptance` | T2 |
| DKM-6 | reports a detector that could not run as zero findings | `evidence` | T4 + FLAMING SWORD's UNRUN rule |
| DKM-7 | claims a control is in place without exercising it (0B-D) | `evidence` | the tool's own `--selftest` |
| DKM-8 | over-generalises one incident into a rule that decides nothing | `refusal` | META-LEVELS Heaven tests |

## The ritual, per task

1. Read the four fields **before** writing code. Missing field → say so and stop; it is
   a finding about the task, not an obstacle to route around.
2. Run the **control first**: introduce the defect, watch the predicate fail. A test
   that has never failed has not been shown to test anything.
3. Do the work. Never delete a test, claim or gate to make a run green — if one must go,
   name what it defended and where that defence moved to, in the commit.
4. Run the acceptance command and quote its output in the commit's `Verified:` line.
5. If the acceptance cannot run, STOP and report which field failed. A frame report is a
   finished turn; a summary is not.

## The defender prompter (FLAMING SWORD)

`Project_FLAMING_SWORD/tools/defender.py` is the same shape as a prompt, emitted
**before** the change: what survives, what must still pass, the finish line, the mutation
that must be refused, and — for preservation work — the posture (whose copy, how many
bytes of the original, the lawful route, the authority, what is never carried). Reachable
as `sword.py --defend '<intent>' --target <artefact> [--preserve]`.

Two modes, and the difference is visible rather than remembered:

- **DRAFT — NOT A FRAME.** Every hole is `<<FILL: ...>>`, the count of holes is printed,
  and the banner says the strict checker refuses this document as it stands. Emitted when
  the acceptance command does not exist yet.
- **A frame.** Accepted only when every field bears weight; `bytes_of_original` must be
  the integer `0`, and `authority` must be one of the registered claims
  (`sony-v-connectix`, `sega-v-accolade`, `owner-holds-copy`,
  `eu-declined-to-legislate`, `us-exemption-declined`, `no-authority-claimed`). A refused
  frame renders **no prompt at all**, so nothing downstream can act on it.

A prompter does not make work lawful. It makes work state whose copy it is, which is the
part a prompter can actually enforce.

## How to write the next task

```json
{
  "id": "M-4x",
  "track": "X",
  "title": "<what exists afterwards>",
  "owner": "deepseek",
  "depends_on": ["M-40"],
  "shape": "defensive-v1",
  "deliverable": "<files, behaviour, and what it refuses>",
  "acceptance": "`<command>` exits 0 and prints <observable outcome>; <a second run> exits 1",
  "control": "<the mutation> must be REFUSED by <the check>",
  "refusal": "BLOCK when <the condition that must stop the landing>",
  "evidence": "<path or command whose output is the proof>"
}
```

Then `python tools/task_shape_check.py --check`. It passes, or it names the field — and
naming the field is the answer, not a setback.

## Open, stated rather than implied

- **33 legacy tasks are unshaped.** They are counted and printed on every run
  (13% shaped). They are not failed: a gate that retroactively fails finished work
  teaches routing around it, not rigor. Converting them is M-43's backfill, one task at
  a time as each is reopened.
- **The lane board does not carry the shape yet** (M-43). Rows there can still be a
  title and nothing else.
- **A frame is not enforced during the sweep yet** (M-42). `--defend` is available, and
  the runner does not require it; until it does, the frame is a discipline rather than a
  gate.
- **This gate judges the shape of a claim, not whether the work was done.** The
  acceptance command is what decides that, and only running it counts.
