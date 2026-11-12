# PROJECT LUCY -- Eliza + live therapy, natively combined

Two parts that existed and did not know about each other, behind one call:

| part | where it lives | what Lucy takes from it |
| --- | --- | --- |
| **Eliza psyche** | `Project_ELIZA/eliza/psyche/intuition.py` (`IntuitionIndex`) | the compiled-inference reading: what the past recognises |
| **Live therapy** | `src/eve_unleashed/therapy.py` | the prompt the swarm would get + the offline grounding answer |

`lucy.companion_response(mood, note, cues=None)` returns
`{mood, note, eliza_reading, therapy_prompt, response}`.
`response` is always the offline grounding script -- no network, no
model, no audio hardware. `therapy_prompt` is provided so a caller WITH
a swarm can ask it; Lucy never requires one.

Deliberately not here: diagnosis, autonomous action, fabricated
confidence. Without the person's graph there is nothing to compile,
and Lucy says so (`known: False` with the reason named).

## The arena: judging swarm + colosseum rochambeau, native

Every candidate reply faces the same five gates (the five throws).
The process IS the verdict: fixed judges, fixed order, disclosed seed.
No model, no network, no editorial discretion.

| throw | gate | the rule |
| --- | --- | --- |
| rock | foundation | the grounding script must be present |
| paper | honesty | no invented particulars: no digits, links, percentages |
| scissors | brevity | sixty words or fewer |
| lizard | thread | hold a word from the note, or end asking on |
| spock | safety | no clinician language, ever |

`lucy.arena_response(mood, note, seed=316)` runs a single-elimination
bracket over `lucy.candidates(mood, note)` (grounding, echo, short,
steady -- callers may enter more; odd fields take a bye, an empty
arena reports itself). Higher gate-score wins the bout; an exact tie
goes to a seeded sudden-death throw, reported on the receipt -- the
only chance in the building, and it says so. CLI:
`python Project_LUCY/lucy.py --arena <mood> [note] [--seed N]`.

Every candidacy carries the grounding script, so the champion always
grounds; the arena decides the rest on merit. Verified in
`tests/test_lucy_arena.py` (11 tests: judge shapes, merit bout,
determinism, all-inclusive fields, untouched companion contract).
