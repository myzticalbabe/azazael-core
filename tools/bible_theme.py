#!/usr/bin/env python3
# coding: utf-8
"""bible_theme.py -- themed editions of the canon, validated against the corpus.

Why this exists
---------------
A themed scripture is a *register*, not a second source. Once the canon in
``docs/bible/bible.json`` is the doctrine, a theme may restate it -- in the
register of Vulcan logic, or of the Jedi Code -- but it may not add a claim of
its own. That distinction is the whole tool:

  **every themed verse must cite at least one verse of the base corpus.**

A theme that drops the citation has invented doctrine, and the validator refuses
it rather than printing it. The rule is cheap, falsifiable, and it survives
re-readings: a future session can add a theme without re-deciding what a theme
is allowed to say.

What else is checked
--------------------
  * ``schema`` is ``azazael/bible-theme@1``, and the register is named.
  * chapters number 1..n per book and verses 1..n per chapter, contiguous, so a
    reference in a theme is as stable as one in the corpus.
  * a ``section.heading`` names a verse that exists, is ascending, and carries
    **no digit** -- the same rule the corpus enforces, because a heading is a
    label and not a claim.
  * ``cites`` resolves against the base corpus; a ``statute`` names a RULE that
    is present in ``MANDATES.md`` and a ``check`` path that exists, so a law with
    nothing executing it is refused here too.
  * an ``annotation.source``, when present, names a repository path that exists.
  * a glossary ``see`` resolves inside the theme; terms are unique.
  * no email address and no phone number: this is a scripture, not a directory.

Everything above is a *structural* claim. None of it can tell you whether the
prose is good, which is why the register is a required field: the reader is told
which voice they are reading, and the theme is judged on the citations it keeps.

Usage
-----
  python tools/bible_theme.py --list                    # themes on disk
  python tools/bible_theme.py --all --check             # the gate: exit 1 on any defect
  python tools/bible_theme.py --theme star-trek         # counts, if it is clean
  python tools/bible_theme.py --all --render --write     # regenerate <theme>.md
  python tools/bible_theme.py --all --json              # machine-readable summary

Exit codes: 0 pass, 1 a theme has a defect, 2 the theme set or the base corpus
is unusable. 2 is not a pass: a validator that cannot read its input has not
judged anything.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

#: Shared with `bible_check.py`: a rule exists when canon gives it a heading and a
#: body. The regex this replaces matched `RULE 0B-D` inside a `Related:` pointer,
#: so a theme could cite a repealed statute and read clean.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import canon_rules  # noqa: E402  (after the path insertion, by necessity)

REPO = Path(__file__).resolve().parents[1]
BIBLE_DIR = REPO / "docs" / "bible"
THEMES = BIBLE_DIR / "themes"
BASE = BIBLE_DIR / "bible.json"
MANDATES = REPO / "MANDATES.md"

SCHEMA = "azazael/bible-theme@1"
ANNOTATION_KINDS = ("textual", "historical", "translation", "editorial", "cross")
GENRES = ("etiology", "law", "asceticism", "catholicity", "heresy", "creed",
          "formation", "governance", "lament", "record", "history")

DEFAULT_LIMITS = {
    "max_books": 10,
    "max_chapters_per_book": 8,
    "max_verses_per_chapter": 40,
}

#: ``genesis 1:1`` -- the same shape the base corpus cites with, so a themed
#: citation and a corpus citation are the same kind of object.
REF_RE = re.compile(r"^([a-z0-9_]+) (\d+):(\d+)$")
#: Book ids are slugs, because a reference is built out of them.
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)")
DIGIT_RE = re.compile(r"\d")


class ThemeError(Exception):
    """The theme set or the base corpus cannot be read at all."""


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def load_base(path: Path = BASE) -> Dict[str, Dict[int, Dict[int, Dict]]]:
    """The base corpus indexed ``book -> chapter -> verse -> verse``.

    Raised on an unreadable corpus rather than returning an empty index: an empty
    index makes every citation "unknown book", which reports 300 problems and
    hides the one that matters -- that nothing was read.
    """
    if not path.is_file():
        raise ThemeError("base corpus not found: %s" % path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ThemeError("base corpus %s is not readable JSON: %s" % (path, exc)) from exc
    index: Dict[str, Dict[int, Dict[int, Dict]]] = {}
    for book in data.get("books", []):
        chapters: Dict[int, Dict[int, Dict]] = {}
        for chapter in book.get("chapters", []):
            verses = {verse["number"]: verse for verse in chapter.get("verses", [])}
            chapters[int(chapter["number"])] = verses
        index[book["id"]] = chapters
    if not index:
        raise ThemeError("base corpus %s declares no books" % path)
    return index


def theme_names(directory: Optional[Path] = None) -> List[str]:
    """Theme names on disk, sorted. A missing directory is an empty set.

    ``directory`` defaults to the module constant at CALL time, not at definition
    time: a test that points THEMES at a temporary directory must be able to make
    the CLI read that directory, and a bound default would silently keep reading
    the shipped one -- the test would pass while exercising nothing.
    """
    directory = THEMES if directory is None else directory
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.json"))


def load_theme(name: str, directory: Optional[Path] = None) -> Tuple[Path, Dict]:
    directory = THEMES if directory is None else directory
    path = directory / ("%s.json" % name)
    if not path.is_file():
        raise ThemeError("no theme named %r in %s (found: %s)"
                         % (name, directory, ", ".join(theme_names(directory)) or "none"))
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ThemeError("%s is not readable JSON: %s" % (path, exc)) from exc


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def _canon_rules(repo: Path = REPO) -> Tuple[Optional[Dict[str, Dict]], Optional[str]]:
    """(rules `MANDATES.md` defines, its text), or (None, None) when unreadable."""
    path = Path(repo) / MANDATES.relative_to(REPO)
    if not path.is_file():
        return None, None
    text = path.read_text(encoding="utf-8", errors="replace")
    return canon_rules.rule_blocks(text), text


def _text_problems(value, where: str) -> List[str]:
    problems: List[str] = []
    if not isinstance(value, str) or not value.strip():
        return ["%s must be a non-empty string" % where]
    if EMAIL_RE.search(value):
        problems.append("%s contains an email address" % where)
    if PHONE_RE.search(value):
        problems.append("%s contains a phone number" % where)
    return problems


def validate(data: Dict, base: Dict[str, Dict[int, Dict[int, Dict]]],
             repo: Path = REPO) -> Dict:
    """Return ``{counts..., problems: [...]}``. An empty list means it may print."""
    problems: List[str] = []
    summary = {"theme": data.get("theme"), "books": 0, "chapters": 0, "verses": 0,
               "citations": 0, "problems": problems}

    if not isinstance(data, dict):
        problems.append("the corpus must be a JSON object")
        return summary
    if data.get("schema") != SCHEMA:
        problems.append("schema must be %r, found %r" % (SCHEMA, data.get("schema")))
    for field in ("title", "theme", "register", "source_corpus", "status", "updated"):
        if not isinstance(data.get(field), str) or not data.get(field, "").strip():
            problems.append("%s must be a non-empty string" % field)

    # The register is the theme's own claim about its voice. An unstated voice is
    # how a themed edition starts reading as a second source.
    for field in ("title", "register"):
        if isinstance(data.get(field), str):
            problems.extend(_text_problems(data[field], field))

    source = data.get("source_corpus")
    if isinstance(source, str) and not (Path(repo) / source).is_file():
        problems.append("source_corpus %r does not exist in this repository" % source)

    limits = dict(DEFAULT_LIMITS)
    declared = data.get("limits")
    if isinstance(declared, dict):
        for key in DEFAULT_LIMITS:
            value = declared.get(key)
            if isinstance(value, int) and value > 0:
                limits[key] = value
            else:
                problems.append("limits.%s must be a positive integer" % key)

    rules, canon_text = _canon_rules(repo)
    if rules is None:
        problems.append("MANDATES.md is unreadable, so a statute's RULE cannot be verified")

    books = data.get("books")
    if not isinstance(books, list) or not books:
        problems.append("books must be a non-empty list")
        return summary
    if len(books) > limits["max_books"]:
        problems.append("%d books exceeds the declared cap of %d"
                        % (len(books), limits["max_books"]))

    seen_books = set()
    theme_refs = set()
    for book_position, book in enumerate(books, start=1):
        where = "book %d" % book_position
        if not isinstance(book, dict):
            problems.append("%s is not an object" % where)
            continue
        book_id = book.get("id")
        if not isinstance(book_id, str) or not ID_RE.match(book_id):
            problems.append("%s id must be a lowercase slug, found %r" % (where, book_id))
            book_id = "book%d" % book_position
        elif book_id in seen_books:
            problems.append("book id %r is used twice" % book_id)
        seen_books.add(book_id)
        where = "book %r" % book_id
        for field in ("name", "subtitle", "genre"):
            if not isinstance(book.get(field), str) or not book.get(field, "").strip():
                problems.append("%s needs a non-empty %s" % (where, field))
        if book.get("genre") not in GENRES:
            problems.append("%s genre %r is not one of %s"
                            % (where, book.get("genre"), ", ".join(GENRES)))
        introduction = book.get("introduction")
        if not isinstance(introduction, dict):
            problems.append("%s needs an introduction object" % where)
        else:
            paragraphs = introduction.get("paragraphs")
            if not isinstance(paragraphs, list) or not paragraphs:
                problems.append("%s introduction needs paragraphs" % where)
            else:
                for position, paragraph in enumerate(paragraphs, start=1):
                    problems.extend(_text_problems(
                        paragraph, "%s introduction paragraph %d" % (where, position)))
            sources = introduction.get("sources")
            if not isinstance(sources, list) or not sources:
                problems.append("%s introduction must name its sources" % where)
            else:
                for entry in sources:
                    if isinstance(entry, str) and not (Path(repo) / entry).exists():
                        problems.append("%s introduction source %r does not exist"
                                        % (where, entry))

        chapters = book.get("chapters")
        if not isinstance(chapters, list) or not chapters:
            problems.append("%s needs at least one chapter" % where)
            continue
        if len(chapters) > limits["max_chapters_per_book"]:
            problems.append("%s has %d chapters, over the declared cap of %d"
                            % (where, len(chapters), limits["max_chapters_per_book"]))
        summary["books"] += 1
        for chapter_position, chapter in enumerate(chapters, start=1):
            chapter_where = "%s chapter %d" % (where, chapter_position)
            if not isinstance(chapter, dict):
                problems.append("%s is not an object" % chapter_where)
                continue
            number = chapter.get("number")
            if number != chapter_position:
                problems.append("%s is numbered %r; chapters must run 1..n in order"
                                % (chapter_where, number))
            verses = chapter.get("verses")
            if not isinstance(verses, list) or not verses:
                problems.append("%s has no verses" % chapter_where)
                continue
            summary["chapters"] += 1
            if len(verses) > limits["max_verses_per_chapter"]:
                problems.append("%s has %d verses, over the declared cap of %d"
                                % (chapter_where, len(verses),
                                   limits["max_verses_per_chapter"]))
            numbers = set()
            for verse_position, verse in enumerate(verses, start=1):
                verse_where = "%s verse %d" % (chapter_where, verse_position)
                if not isinstance(verse, dict):
                    problems.append("%s is not an object" % verse_where)
                    continue
                if verse.get("number") != verse_position:
                    problems.append("%s is numbered %r; verses must run 1..n in order"
                                    % (verse_where, verse.get("number")))
                numbers.add(verse.get("number"))
                summary["verses"] += 1
                theme_refs.add("%s %d:%d" % (book_id, chapter_position, verse_position))
                problems.extend(_verse_problems(verse, verse_where, base, rules,
                                                canon_text, Path(repo), summary))

            problems.extend(_section_problems(chapter.get("sections"), numbers,
                                              chapter_where))

    problems.extend(_glossary_problems(data.get("glossary"), theme_refs))
    return summary


def _verse_problems(verse: Dict, where: str,
                    base: Dict[str, Dict[int, Dict[int, Dict]]],
                    rules: Optional[Dict[str, Dict]], canon_text: Optional[str],
                    repo: Path, summary: Dict) -> List[str]:
    problems = _text_problems(verse.get("text"), "%s text" % where)

    cites = verse.get("cites")
    if not isinstance(cites, list) or not cites:
        # THE rule. A themed verse with nothing to cite is a new doctrine wearing
        # an old voice, and there is no other place this can be caught.
        problems.append("%s cites nothing: a themed verse must restate a verse of "
                        "the base corpus, not add one" % where)
    else:
        for citation in cites:
            if not isinstance(citation, str) or not REF_RE.match(citation):
                problems.append("%s citation %r must read \"<book> <chapter>:<verse>\""
                                % (where, citation))
                continue
            book_id, chapter_text, verse_text = REF_RE.match(citation).groups()
            chapters = base.get(book_id)
            verses = chapters.get(int(chapter_text)) if chapters else None
            if not verses or int(verse_text) not in verses:
                problems.append("%s cites %s, which is not a verse of the base corpus"
                                % (where, citation))
            else:
                summary["citations"] += 1

    statute = verse.get("statute")
    if statute is not None:
        if not isinstance(statute, dict):
            problems.append("%s statute must be an object" % where)
        else:
            rule = statute.get("rule")
            if not isinstance(rule, str) or not rule.strip():
                problems.append("%s statute must name a RULE" % where)
            elif rules is not None:
                defect = canon_rules.rule_problem(rule, rules, canon_text)
                if defect:
                    problems.append("%s statute %s" % (where, defect))
            check = statute.get("check")
            if not isinstance(check, str) or not check.strip():
                problems.append("%s statute names no check command" % where)
            elif not _command_target_exists(check, repo):
                problems.append("%s statute's check %r names no path in this repository"
                                % (where, check))

    annotations = verse.get("annotations")
    if annotations is not None:
        if not isinstance(annotations, list):
            problems.append("%s annotations must be a list" % where)
        else:
            for position, annotation in enumerate(annotations, start=1):
                note_where = "%s annotation %d" % (where, position)
                if not isinstance(annotation, dict):
                    problems.append("%s is not an object" % note_where)
                    continue
                if annotation.get("kind") not in ANNOTATION_KINDS:
                    problems.append("%s kind %r is not one of %s"
                                    % (note_where, annotation.get("kind"),
                                       ", ".join(ANNOTATION_KINDS)))
                problems.extend(_text_problems(annotation.get("note"),
                                               "%s note" % note_where))
                source = annotation.get("source")
                if source is not None:
                    if not isinstance(source, str) or not (repo / source).is_file():
                        problems.append("%s source %r does not exist in this repository"
                                        % (note_where, source))
    return problems


def _section_problems(sections, numbers: set, where: str) -> List[str]:
    problems: List[str] = []
    if sections is None:
        return problems
    if not isinstance(sections, list):
        return ["%s sections must be a list" % where]
    previous = 0
    for position, section in enumerate(sections, start=1):
        section_where = "%s section %d" % (where, position)
        if not isinstance(section, dict):
            problems.append("%s is not an object" % section_where)
            continue
        before = section.get("before_verse")
        if not isinstance(before, int) or before not in numbers:
            problems.append("%s names verse %r, which does not exist"
                            % (section_where, before))
        elif before <= previous:
            problems.append("%s is out of order: %d after %d"
                            % (section_where, before, previous))
        else:
            previous = before
        heading = section.get("heading")
        problems.extend(_text_problems(heading, "%s heading" % section_where))
        if isinstance(heading, str) and DIGIT_RE.search(heading):
            # A heading is a label. A figure in it is a claim with no command,
            # which is the exact defect the corpus refuses.
            problems.append("%s heading %r carries a figure; a heading is a label, "
                            "not a claim" % (section_where, heading))
    return problems


def _command_target_exists(command: str, repo: Path) -> bool:
    """True when a command line names a path in this repository that exists."""
    for token in command.split():
        cleaned = token.strip("`'\"();")
        if cleaned.endswith(".py") and (repo / cleaned).is_file():
            return True
    return False


def _glossary_problems(glossary, theme_refs: set) -> List[str]:
    problems: List[str] = []
    if glossary is None:
        return problems
    if not isinstance(glossary, list):
        return ["glossary must be a list"]
    seen = set()
    for position, entry in enumerate(glossary, start=1):
        where = "glossary entry %d" % position
        if not isinstance(entry, dict):
            problems.append("%s is not an object" % where)
            continue
        term = entry.get("term")
        if not isinstance(term, str) or not term.strip():
            problems.append("%s needs a term" % where)
        elif term.lower() in seen:
            problems.append("glossary term %r is defined twice" % term)
        else:
            seen.add(term.lower())
        problems.extend(_text_problems(entry.get("gloss"), "%s gloss" % where))
        see = entry.get("see")
        if see is not None and see not in theme_refs:
            problems.append("%s see %r is not a verse of this theme" % (where, see))
    return problems


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _reference_index(books: Sequence[Dict]) -> Dict[str, List[str]]:
    """Who cites whom, inside the theme only. Used for the marginal note."""
    cited_by: Dict[str, List[str]] = {}
    for book in books:
        for chapter in book.get("chapters", []) or []:
            for verse in chapter.get("verses", []) or []:
                reference = "%s %d:%d" % (book.get("id"), chapter.get("number"),
                                          verse.get("number"))
                for citation in verse.get("cites", []) or []:
                    cited_by.setdefault(citation, []).append(reference)
    return cited_by


def render_theme(data: Dict, corpus_name: str = "unknown") -> str:
    """Deterministic markdown: headings in place, notes beside their verse.

    Nothing here is timestamped or path-dependent, so a second run is
    byte-identical and the drift check cannot fail on a clock.
    """
    lines = ["# %s" % data.get("title", "Untitled")]
    register = data.get("register")
    if register:
        lines.extend(["", "*%s*" % register])
    source = data.get("source_corpus")
    if source:
        lines.extend(["", "A themed edition of `%s`: every verse below cites a verse "
                          "of the corpus, and adds none." % source])
    lines.extend(["", "Status: **%s** · updated %s." % (data.get("status"),
                                                       data.get("updated")), "",
                  "_Generated from `docs/bible/themes/%s.json` by "
                  "`python tools/bible_theme.py --all --render --write`. Do not edit "
                  "this file: edit the corpus._" % corpus_name])

    for book in data.get("books", []) or []:
        lines.extend(["", "## %s" % book.get("name", book.get("id")), ""])
        if book.get("subtitle"):
            lines.extend(["### %s" % book["subtitle"], ""])
        if book.get("genre"):
            lines.extend(["*%s*" % book["genre"], ""])
        introduction = book.get("introduction")
        if isinstance(introduction, dict):
            lines.append("")
            for paragraph in introduction.get("paragraphs", []) or []:
                lines.extend([paragraph, ""])
            sources = introduction.get("sources") or []
            if sources:
                lines.extend(["*Sources: %s*" % "; ".join(sources), ""])
        for chapter in book.get("chapters", []) or []:
            number = chapter.get("number")
            lines.extend(["", "#### %s %s" % (book.get("name", ""), number), ""])
            headings = {section.get("before_verse"): section.get("heading")
                        for section in chapter.get("sections", []) or []}
            for verse in chapter.get("verses", []) or []:
                verse_number = verse.get("number")
                if verse_number in headings:
                    lines.extend(["##### %s" % headings[verse_number], ""])
                trailing = []
                if verse.get("cites"):
                    trailing.append("corpus " + ", ".join(verse["cites"]))
                if isinstance(verse.get("statute"), dict):
                    statute = verse["statute"]
                    trailing.append("statute %s → `%s`" % (statute.get("rule"),
                                                           statute.get("check")))
                suffix = (" *(%s)*" % "; ".join(trailing)) if trailing else ""
                lines.append("**%s** %s%s" % (verse_number, verse.get("text", ""),
                                              suffix))
                annotations = verse.get("annotations") or []
                if annotations:
                    lines.append("")
                for annotation in annotations:
                    source_note = annotation.get("source")
                    lines.append("> **%s** — %s%s"
                                 % (annotation.get("kind"), annotation.get("note"),
                                    (" (`%s`)" % source_note) if source_note else ""))
                lines.append("")

    glossary = data.get("glossary") or []
    if glossary:
        lines.extend(["", "## Glossary", ""])
        for entry in glossary:
            see = (" (see %s)" % entry["see"]) if entry.get("see") else ""
            lines.append("- **%s** — %s%s" % (entry.get("term"), entry.get("gloss"), see))
    lines.append("")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


def rendered_path(name: str, directory: Optional[Path] = None) -> Path:
    return (THEMES if directory is None else directory) / ("%s.md" % name)


def drift_message(name: str, data: Dict,
                  directory: Optional[Path] = None) -> Optional[str]:
    """A one-line drift report, or None when the rendered file matches the corpus."""
    path = rendered_path(name, directory)
    expected = render_theme(data, name)
    if not path.is_file():
        return ("%s.md has never been rendered; run --all --render --write" % name)
    if path.read_text(encoding="utf-8", errors="replace") != expected:
        return ("%s.md does not match its corpus; run --all --render --write" % name)
    return None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate and render themed editions of the bible corpus.")
    parser.add_argument("--list", action="store_true", help="list themes on disk")
    parser.add_argument("--theme", action="append", default=[],
                        help="a theme name (repeatable); default is every theme")
    parser.add_argument("--all", action="store_true", help="every theme on disk")
    parser.add_argument("--check", action="store_true", help="validate; exit 1 on a defect")
    parser.add_argument("--render", action="store_true", help="print the rendered edition")
    parser.add_argument("--write", action="store_true", help="write the rendered edition")
    parser.add_argument("--json", action="store_true", help="machine-readable summary")
    parser.add_argument("--no-drift", action="store_true",
                        help="skip the rendered-file drift check")
    args = parser.parse_args(argv)

    if args.list:
        for name in theme_names():
            print(name)
        return 0

    try:
        base = load_base()
    except ThemeError as exc:
        print("UNUSABLE: %s" % exc, file=sys.stderr)
        return 2

    names = list(args.theme) or theme_names()
    if not names:
        print("UNUSABLE: no themes in %s" % THEMES, file=sys.stderr)
        return 2

    summaries: List[Dict] = []
    failures = 0
    for name in names:
        try:
            _path, data = load_theme(name)
        except ThemeError as exc:
            print("UNUSABLE: %s" % exc, file=sys.stderr)
            return 2
        summary = validate(data, base)
        problems = summary["problems"]
        if args.check and not args.no_drift:
            drift = drift_message(name, data)
            if drift:
                problems.append(drift)
        summary["name"] = name
        summaries.append(summary)
        if problems:
            failures += 1

        if args.json:
            continue
        # The theme's own field is its declared identity; the header shows the
        # title, because a reader opening the output wants the name of the book.
        print("== %s: %s" % (name, data.get("title") or "?"))
        if problems:
            for problem in problems:
                print("   FAIL %s" % problem)
        else:
            print("   OK   %d book(s), %d chapter(s), %d verse(s), %d citation(s)"
                  % (summary["books"], summary["chapters"], summary["verses"],
                     summary["citations"]))
        if args.render or args.write:
            text = render_theme(data, name)
            if args.write:
                rendered_path(name).write_text(text, encoding="utf-8", newline="\n")
                print("   wrote %s" % rendered_path(name).relative_to(REPO))
            else:
                print(text)

    if args.json:
        print(json.dumps({"themes": summaries}, indent=1, sort_keys=True))
    if failures:
        # A defect exits 1 whether or not --check was asked for. Printing FAIL and
        # returning 0 is the false-green this estate has spent sessions removing:
        # a caller reading the exit code would record a broken theme as valid.
        print("\n%d of %d theme(s) refused." % (failures, len(summaries)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
