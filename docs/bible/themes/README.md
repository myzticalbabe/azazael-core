# Themes — the same canon, in another voice

A theme is a **register**, not a second scripture. `docs/bible/bible.json` is the
doctrine; a theme restates it in the idiom of another tradition — Vulcan logic,
the Jedi Code — and is allowed to say nothing the corpus does not already say.

That restriction is enforced by one rule, and it is the only rule here that
matters:

> **Every themed verse cites at least one verse of the base corpus.**

A verse with no citation is new doctrine wearing an old voice, and
`tools/bible_theme.py` refuses the whole theme rather than printing it. Everything
else the tool checks is bookkeeping around that rule — numbering, headings,
statutes, sources, the glossary — so that a themed citation is as stable as a
corpus citation.

## Files

```
docs/bible/themes/
  README.md            this contract
  star-trek.json       the corpus: The Book of Logic
  star-trek.md         generated; do not edit
  star-wars.json       the corpus: The Book of the Code
  star-wars.md         generated; do not edit
```

A theme is added by writing `<name>.json` and running the renderer. No code
change, the same way a domain is added to `tools/domain_trees.py` by declaration.

## Commands

```bash
python tools/bible_theme.py --list                  # themes on disk
python tools/bible_theme.py --all --check           # the gate: exit 1 on any defect
python tools/bible_theme.py --theme star-trek       # counts, if it is clean
python tools/bible_theme.py --all --render --write  # regenerate <theme>.md
python tools/bible_theme.py --all --json            # machine-readable summary
```

Exit codes: `0` pass, `1` a theme has a defect, `2` the theme set or the base
corpus could not be read. `2` is not a pass — a validator that cannot read its
input has not judged anything.

## The corpus format

```jsonc
{
  "schema": "azazael/bible-theme@1",
  "title": "The Book of Logic",
  "theme": "star-trek",              // must equal the file name
  "register": "…",                    // the voice, named. Required, not decoration
  "source_corpus": "docs/bible/bible.json",
  "status": "seed",
  "updated": "2026-11-02",
  "limits": { "max_books": 10, "max_chapters_per_book": 8, "max_verses_per_chapter": 40 },
  "glossary": [ { "term": "…", "gloss": "…", "see": "awakening 1:5" } ],
  "books": [{
    "id": "awakening",                // lowercase slug; references are built from it
    "name": "Awakening",
    "subtitle": "The Book of the First Reading",
    "genre": "etiology",              // one of GENRES in tools/bible_theme.py
    "introduction": { "paragraphs": ["…"], "sources": ["docs/bible/bible.json"] },
    "chapters": [{
      "number": 1,
      "sections": [ { "before_verse": 1, "heading": "…" } ],
      "verses": [{
        "number": 1,
        "text": "…",
        "cites": ["genesis 1:1"],     // REQUIRED, and must resolve in the corpus
        "statute": { "rule": "RULE 0B-D", "check": "python tools/preflight_all.py --self-test" },
        "annotations": [ { "kind": "editorial", "note": "…", "source": "docs/bible/README.md" } ]
      }]
    }]
  }]
}
```

## What the checker refuses

| defect | why it is refused |
|---|---|
| a verse with no `cites` | the theme would be adding doctrine, which is the whole thing a theme may not do |
| a `cites` that does not resolve in `docs/bible/bible.json` | a themed citation must land on a real verse, or it is a quotation from nowhere |
| chapter or verse numbers that skip | a reference in a theme must be as stable as one in the corpus |
| a heading containing a digit | a heading is a label, not a claim — the `RULE 0B-D` rule, carried over |
| an annotation `kind` outside the five | the kind is what tells the reader how the note can be settled |
| an annotation `source` that is not a path in this repository | a note that cites nothing that exists is an opinion |
| a `statute` naming a RULE absent from `MANDATES.md` | a law bound to no rule is a proverb |
| a `statute` whose `check` names no path in this repository | a law nothing executes, refused here exactly as in the corpus |
| a glossary `see` pointing at a verse of the theme that does not exist | the glossary is an index, and an index that misses is worse than none |
| a duplicate book id or glossary term | identity is the object, not the name |
| an email address or a phone number | a scripture is not a directory |
| a rendered `.md` that no longer matches its corpus | the readable file is generated; a stale one is a second, wrong source |

## Adding a theme

1. Copy the closest existing theme and change `theme` to the file name.
2. Write books whose **every verse** restates a verse you can point at. If you
   find yourself wanting a claim the corpus does not make, either add it to
   `docs/bible/bible.json` first (where it will be subject to the full corpus
   gates, including harvest evidence) or leave it out of the theme.
3. `python tools/bible_theme.py --theme <name>` until it passes.
4. `python tools/bible_theme.py --all --render --write` and commit both files.
5. `python -m pytest tests/test_bible_theme.py -q`.

Themes are not wired into the commit hook. They are a register, not a gate, and
`tests/test_bible_theme.py` holds them: it fails if a shipped theme stops
validating or if its rendered file drifts, which is the same protection with a
smaller blast radius.
