# The emotional corpora

Two sibling bibles sit here, in the same schema as `docs/bible/bible.json` and
validated by the same tool. They are **corpora, not themed editions**: a theme may
only restate the canon and must cite it, while these books make claims of their
own about a subject the canon does not govern — the vocabulary a system is allowed
to use about a person's feeling.

| file | what it is | status |
|---|---|---|
| `emotional.json` | affect, appraisal, granularity, display, chronometry, witness, gate, attunement | drafting, 8 books / 58 verses |
| `female.json` | similarity, naming, self-silencing, labour, the double bind, safety, credibility, intersectionality, refusals | drafting, 9 books / 57 verses |
| `SOURCES.md` | the research register: every figure, its study, and its limit | maintained by hand |
| `EMOTIONAL_BIBLE.md` | generated from `emotional.json` | generated |
| `FEMALE_BIBLE.md` | generated from `female.json` | generated |

```bash
python tools/bible_check.py --check --corpus docs/bible/emotional/emotional.json
python tools/bible_check.py --check --corpus docs/bible/emotional/female.json
python tools/bible_check.py --render --write --corpus docs/bible/emotional/female.json
```

`--render --write` writes the edition **beside its corpus** (`FEMALE_BIBLE.md`,
`EMOTIONAL_BIBLE.md`). The canonical corpus keeps `BOOK_OF_THE_TREE.md`. One
generated file per corpus is what lets two corpora be checked in one tree without
each one reading the other's edition as staleness.

## Why two books rather than one

The emotional corpus is general: it describes the machinery any persona needs, and
it is answerable to `Project_ELIZA/eliza/psyche/`.

The female corpus is specialised because the general vocabulary is not neutral in
practice. A system that knows only "affect, appraisal, display" will still meet the
attribution that calls her emotional and him tired, the silence that was a
calculation, the labour nobody invoiced, and the credibility discount applied
before the sentence ended. Those are not extra feelings; they are the conditions
under which the general machinery is used. Keeping them in a second corpus means
each can be cited, refused, and corrected without pretending the first already
covered it.

## The rules this directory inherits

Everything the canon enforces applies here, unchanged:

- **No digit without a command.** A figure in a verse, an annotation, a gloss, or a
  heading is refused unless it names a repository path that exists. The research
  figures therefore live in `SOURCES.md`, where they are attached to their study
  and to the limit that study carries.
- **A citation resolves inside its own corpus.** `female.json` may not cite
  `emotional.json`; the two are read separately, and a cross-corpus reference is a
  broken reference. Where a book needs the other corpus, it says so in an
  annotation instead of pretending to cite it.
- **A statute needs a rule and a check.** `RULE 0B-E` appears in `MANDATES.md`, and
  `Project_ELIZA/tools/eliza_release_gate.py --check` exits green today. A law
  whose check has been deleted is refused.
- **The apparatus is honest.** `female.json` carries `variants` for the readings it
  corrects — the older claim that the sexes differ substantially, the attribution
  that women are the more emotional sex, the individualising reading of
  self-silencing, the symmetric account of partner violence. The wrong reading is
  kept and labelled, not deleted.
- **Growth is capped.** Twelve books each, eight chapters, forty verses.

## What these corpora refuse

Stated here as well as in `female.json` → `refusals`, because a reader may meet the
directory before the book:

1. Predicting a woman from a group mean.
2. Diagnosing, flattering, or explaining her to herself unasked.
3. Treating endurance as strength, or silence as peace.
4. Using prevalence as a verdict about a particular woman, in either direction.
5. Quoting a number from `SOURCES.md` without the limit printed beside it.

## Extending

1. Read the primary study; record effect size, sample, design, and limit in
   `SOURCES.md` first.
2. Write the verse without a digit. If it needs the number to make sense, it is not
   yet a verse.
3. Add it with a `cites` that resolves inside the same corpus, and an annotation
   whose `kind` says whether it is a reading, a claim about the world, or an
   editorial judgement.
4. Run both checks above, then `python tools/bible_check.py --check` for the canon:
   the shared tool must stay green for all three corpora.

## Relation to the rest of the tree

- `docs/bible/bible.json` — the canon of the tree, and the register these two
  inherit their form from.
- `docs/bible/themes/` — registers over the canon, which may not add doctrine.
  These files are not themes and are not checked by `bible_theme.py`.
- `Project_ELIZA/` — the implementation these books describe and are answerable to.
- `docs/WHITEPAPER_RESEARCH_CREATIVE_ADAPTATION_2026-11-02.md` — the method that
  produced them, written as a tutorial.
