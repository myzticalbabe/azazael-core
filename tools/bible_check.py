#!/usr/bin/env python3
# coding: utf-8
"""Check a canon corpus written in biblical form; render it, index it, generate it.

WHY A CHECKER AT ALL
--------------------
A Bible-shaped document earns its authority from citation: a verse exists so
another verse can lean on it. That only works if a reference resolves, and it
only stays honest if the thing it cites has not been quietly changed. So the
corpus is data -- `docs/bible/bible.json` -- and this tool is its apparatus:
numbering that must be contiguous, citations that must land, statutes that must
name both the rule they come from and the check that proves they are kept, and a
hard refusal of any figure whose command is not written beside it.

WHAT IT REFUSES, AND WHY EACH REFUSAL IS A WAY THE TEXT WOULD LIE
----------------------------------------------------------------
* **A dangling citation.** "As it is written in Genesis 4:9" is a claim; if the
  verse is not there, the claim is unfalsifiable and the reader cannot tell a
  quotation from an invention. A citation may also name a document instead of a
  verse, and then the document must exist.
* **A number with no command.** Any digit in a verse body, an annotation, a gloss
  or an introduction paragraph demands a `source` naming a path in this
  repository. This is the rule `docs/index.md` already states for prose -- every
  number names the command that produced it -- made mechanical, because a
  scripture quoting an unmeasured figure is the most quotable possible form of a
  false one.
* **A statute with no check.** A law with nothing executing it is a proverb. It
  belongs in the wisdom book, and the validator says so instead of letting a
  commandment masquerade as a gate.
* **A statute whose rule is not in canon.** The corpus narrates canon; it does
  not legislate. A statute must cite a RULE that exists in `MANDATES.md`, so the
  Bible cannot become a second source of truth.
* **A heading carrying a figure.** A pericope heading is a label, not a claim,
  and it has no field to name a command with.
* **A name.** Refused substrings and contact shapes are rejected outright, on the
  same line `Project_Nurse_Ratchet/DISCLOSURE_POLICY.md` draws: methodology
  ships, people do not.
* **Growth without a cap.** Books, chapters per book, verses per chapter and
  annotations per verse are all capped. A canon of sixty-six books is a parody
  with a bibliography; an uncapped corpus is how a catalogue becomes a liability.
* **A `complete` corpus with a verse that does no work.** When the corpus
  declares itself complete, every verse must be cited, cite, or carry a statute,
  an annotation or a variant -- and every book must have an introduction. `seed`
  and `drafting` make no such claim, so nothing is enforced there.

TWO GENERATED FILES, BOTH DRIFT-CHECKED
--------------------------------------
`--genealogy` reads `src/` with `ast` and counts cross-package imports, because
"and A begat B" is an import edge and a genealogy a reader can re-run is the one
part of a scripture that cannot be embellished. `--render` writes the readable
edition: headings in place, notes and apparatus beside the verse they annotate,
a marginal column naming who cites whom, and the glossary at the back.

Both are committed, and `--check` compares each against a fresh rendering. A
generated file nobody re-checks is a second source of truth: the mesh ledger in
this tree went stale exactly that way, and a published number that no longer
matches its own data is worse than no number.

Usage:
  python tools/bible_check.py                     # counts and warnings
  python tools/bible_check.py --check              # exit 1 on any defect (the gate)
  python tools/bible_check.py --index              # who cites whom, and the orphans
  python tools/bible_check.py --render             # print the readable edition
  python tools/bible_check.py --render --write      # regenerate BOOK_OF_THE_TREE.md
  python tools/bible_check.py --genealogy           # print the generations
  python tools/bible_check.py --genealogy --write   # regenerate GENEALOGY.md
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

#: The rule-resolution predicate is shared with `bible_theme.py`, so the two
#: gates cannot disagree about whether a rule exists. A tool loaded by path has
#: no sibling on `sys.path` unless it puts its own directory there.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import canon_rules  # noqa: E402  (after the path insertion, by necessity)

REPO = Path(__file__).resolve().parents[1]
BIBLE_DIR = REPO / "docs" / "bible"
CORPUS = BIBLE_DIR / "bible.json"
GENEALOGY = BIBLE_DIR / "GENEALOGY.md"
EDITION = BIBLE_DIR / "BOOK_OF_THE_TREE.md"
MANDATES = REPO / "MANDATES.md"
SRC = REPO / "src"

SCHEMA = "azazael/bible@1"
STATUSES = ("seed", "drafting", "complete")

#: A genre per book, because the Bible is an anthology and a single register for
#: twelve books is pastiche. Each name here is a job, not a mood.
GENRES = (
    "etiology", "law", "history", "chronicle", "wisdom", "lament", "prophecy",
    "gospel", "epistle", "apocalypse", "genealogy", "domain",
)
#: Placement of a verse inside a domain book: the ``practice`` block is rendered
#: as a titled apparatus under the verse, never inline, so a reader can read the
#: narrative and then choose to read the instruction.

#: Declared ceilings. `limits` in the corpus may tighten these and may never
#: exceed them: a cap the author can raise is not a cap.
#: Raised 2026-11-01 when books became domains. There are 11 declared domains
#: today (sentinel, cain, eve_rani, hives, gosp, ml, shards, dev, edge0, wraith,
#: oxman) plus the four canonical registers, so the old ceiling of 24 left six
#: books of headroom for a corpus meant to absorb every environment. 32 is still
#: a retirement-forcing cap: the twelve domains of this fleet fit twice over.
HARD_MAX_BOOKS = 32
HARD_MAX_CHAPTERS = 60
HARD_MAX_VERSES = 200
HARD_MAX_ANNOTATIONS = 12

DEFAULT_LIMITS = {
    "max_books": 20,
    "max_chapters_per_book": 12,
    "max_verses_per_chapter": 60,
    "max_annotations_per_verse": 6,
}
CEILINGS = {
    "max_books": HARD_MAX_BOOKS,
    "max_chapters_per_book": HARD_MAX_CHAPTERS,
    "max_verses_per_chapter": HARD_MAX_VERSES,
    "max_annotations_per_verse": HARD_MAX_ANNOTATIONS,
}

#: The disclosure line, in the same terms the package's manifest refuses.
REFUSED_SUBSTRINGS = (
    "bcc_audit", "REINVESTIGATION", "FETCH_LOG", "edge_proof_ledger",
    "identity_tiebreak", "new_sources_log",
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\d-])\d{3}[-.\s]\d{3}[-.\s]\d{4}(?![\d-])")
DIGIT_RE = re.compile(r"[0-9]")
#: Any repository-relative path, not just the six directories this tree happens
#: to keep tools in: `Project_Nurse_Ratchet/README.md` is the provenance of a
#: defect recorded in that package, and a checker that could not name it would
#: push the author to cite the wrong file instead. The lookbehind refuses to
#: begin inside a URL, so `https://host/x.md` cannot masquerade as a local path.
PATH_RE = re.compile(r"(?<![\w/:.-])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"
                     r"\.(?:py|json|md|js|ps1|sh|yml|yaml)")
REF_RE = re.compile(r"^([a-z][a-z0-9_]*)\s+([0-9]+):([0-9]+)$")
RULE_RE = re.compile(r"\bRULE\s+[0-9][0-9A-Za-z-]*")

#: The annotated-edition layer. An Oxford-style Bible is not a bare text: it
#: carries a book introduction, pericope headings, footnotes sorted by kind, and a
#: glossary. The kinds matter because a textual note and a historical note fail
#: differently -- one is a reading, the other is a claim about the world -- and a
#: reader is entitled to know which they are reading.
ANNOTATION_KINDS = ("textual", "historical", "translation", "editorial", "cross")

TOP_KEYS = {"schema", "title", "status", "updated", "limits", "note", "books",
            "glossary", "edition", "configs"}
BOOK_KEYS = {"id", "name", "subtitle", "genre", "domain", "introduction",
             "chapters"}
#: A book may be *about* a declared domain, and then it must name one that
#: exists: a guide organised by domain is only organised if every book's subject
#: is a real, measurable tree rather than a heading someone liked.
DOMAINS_MODULE = "tools/domain_trees.py"
#: The practical apparatus. It is separate from `annotations` because a note is
#: commentary and a practice is instruction: symptom, cause, the command that
#: detects it, the remedy, and the harnessed records that support the claim.
PRACTICE_KEYS = {"symptom", "cause", "detection", "remedy", "evidence"}
MIN_EVIDENCE = 3
CHAPTER_KEYS = {"number", "sections", "verses"}
VERSE_KEYS = {"number", "text", "cites", "source", "statute", "practice", "variants",
              "annotations", "note"}
STATUTE_KEYS = {"rule", "check", "why"}
VARIANT_KEYS = {"reading", "text", "source"}
ANNOTATION_KEYS = {"kind", "note", "source"}
SECTION_KEYS = {"before_verse", "heading"}
INTRODUCTION_KEYS = {"paragraphs", "sources"}
GLOSSARY_KEYS = {"term", "gloss", "see", "source"}
EDITION_KEYS = {"name", "basis"}


class CorpusError(Exception):
    """Malformed corpus. Never downgraded to a warning."""


def load(path: Path = CORPUS) -> Dict:
    if not path.exists():
        raise CorpusError("%s is absent; there is no corpus to check" % path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CorpusError("%s is not valid JSON: %s" % (path.name, exc)) from exc


def _paths_in(text: str) -> List[str]:
    return PATH_RE.findall(text or "")


def _provenance_problems(source, where: str, repo: Path) -> List[str]:
    """A figure must name a command that exists. Verbs alone do not count.

    `python some_tool.py` with no path is a memory of a command; a path that
    resolves is a command someone can run. Paths under `logs/` are exempt from
    existence because reports are rotated and the corpus may cite one that has
    since been replaced -- but the token must still be written down.
    """
    if not isinstance(source, str) or not source.strip():
        return ["%s: a number needs `source`: the command or path that produced it"
                % where]
    tokens = _paths_in(source)
    if not tokens:
        return ["%s: source names no repository path (%r); a figure with no "
                "re-runnable command is a claim, not evidence" % (where, source)]
    problems = []
    for token in tokens:
        head = token.split("/", 1)[0]
        if head == "logs":
            continue
        if not (Path(repo) / token.split("#", 1)[0]).exists():
            problems.append("%s: source path does not exist here: %s" % (where, token))
    return problems


def _text_problems(text, where: str) -> List[str]:
    problems: List[str] = []
    if not isinstance(text, str) or not text.strip():
        return ["%s: text is empty" % where]
    if not re.search(r"[A-Za-z]", text):
        problems.append("%s: text carries no words" % where)
    lowered = text.lower()
    for token in REFUSED_SUBSTRINGS:
        if token.lower() in lowered:
            problems.append("%s: refused substring %r -- methodology ships, people "
                            "do not" % (where, token))
    if EMAIL_RE.search(text):
        problems.append("%s: an email address is refused" % where)
    if PHONE_RE.search(text):
        problems.append("%s: a phone number is refused" % where)
    return problems


def _prose_block_problems(paragraphs, sources, where: str, repo: Path) -> List[str]:
    """An introduction or a gloss is prose, but it still may not assert a figure
    for free: the moment it carries a digit, the block must name its sources."""
    problems: List[str] = []
    if not isinstance(paragraphs, list) or not paragraphs:
        return ["%s: paragraphs must be a non-empty list" % where]
    carries_digit = False
    for index, paragraph in enumerate(paragraphs):
        problems.extend(_text_problems(paragraph, "%s paragraph %d" % (where, index + 1)))
        if isinstance(paragraph, str) and DIGIT_RE.search(paragraph):
            carries_digit = True
    if sources is not None and not isinstance(sources, list):
        problems.append("%s: sources must be a list" % where)
        sources = []
    if carries_digit:
        if not sources:
            problems.append("%s: a paragraph carries a figure, so the block must name "
                            "`sources` -- or spell the number out" % where)
        for source in sources or []:
            problems.extend(_provenance_problems(source, where, repo))
    return problems


def _statute_problems(statute, where: str, repo: Path, canon: Optional[Dict[str, Dict]],
                      canon_text: Optional[str] = None) -> List[str]:
    if not isinstance(statute, dict):
        return ["%s: statute must be an object with `rule` and `check`" % where]
    problems: List[str] = []
    unknown = set(statute) - STATUTE_KEYS
    if unknown:
        problems.append("%s: unknown statute key(s) %s" % (where, sorted(unknown)))
    rule = statute.get("rule")
    if not isinstance(rule, str) or not RULE_RE.search(rule):
        problems.append("%s: statute.rule must name a RULE (e.g. \"RULE 0B\")" % where)
    elif canon is None:
        problems.append("%s: MANDATES.md is unreadable -- the rule %r cannot be "
                        "verified against canon" % (where, rule))
    else:
        # A heading with a body, not a mention anywhere in the file: `RULE 0B-D`
        # appears in two `Related:` lines, and that is what the substring test
        # was reading as a law.
        defect = canon_rules.rule_problem(rule, canon, canon_text)
        if defect:
            problems.append("%s: %s" % (where, defect))
    check = statute.get("check")
    if not isinstance(check, str) or not check.strip():
        problems.append("%s: statute.check is empty -- a law with nothing executing "
                        "it is a proverb, not a statute" % where)
    else:
        tokens = _paths_in(check)
        if not tokens:
            problems.append("%s: statute.check names no repository path (%r)"
                            % (where, check))
        for token in tokens:
            if not (Path(repo) / token.split("#", 1)[0]).exists():
                problems.append("%s: statute.check does not exist here: %s"
                                % (where, token))
    return problems


def _resolve_citation(citation: str, index: Dict[str, Dict[int, Dict[int, Dict]]],
                      repo: Path) -> Optional[str]:
    """Return a problem string, or None when the citation lands."""
    match = REF_RE.match(citation)
    if match:
        book_id, chapter_text, verse_text = match.groups()
        chapter, verse = int(chapter_text), int(verse_text)
        if book_id not in index:
            return "cites unknown book %r" % book_id
        if chapter not in index[book_id] or verse not in index[book_id][chapter]:
            return "cites %s, which does not exist" % citation
        return None
    if PATH_RE.fullmatch(citation):
        if (repo / citation).exists():
            return None
        return "cites %s, which does not exist in this repository" % citation
    return ("citation %r must read \"<book> <chapter>:<verse>\" or name a "
            "repository path" % citation)


def declared_domains(repo: Path = REPO) -> Optional[set]:
    """The domain ids declared in `tools/domain_trees.py`, read by AST.

    Read, not imported: importing the atlas tool drags its whole census in, and a
    checker that scans a filesystem to decide whether a *name* is legitimate is
    doing far more work than the question needs. Returns None when the file is
    unreadable, which is reported rather than treated as "no domains".
    """
    path = Path(repo) / DOMAINS_MODULE
    if not path.exists():
        return None
    # Unreadable is raised, not returned as a default: a caller that turns
    # "could not read" into "reads like nothing" is how a missing declaration
    # becomes a silent pass, and the scanner is right to refuse that shape.
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:
        raise CorpusError("%s could not be parsed: %s" % (DOMAINS_MODULE, exc)) from exc
    for node in tree.body:
        # Both forms are in use in this tree: a bare assignment and an annotated
        # one (`DOMAINS: Dict[str, ...] = {`). Reading only the first reported
        # every domain as unverifiable while the declaration sat in plain sight.
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign):
            names = [t for t in node.targets if isinstance(t, ast.Name)]
            target, value = (names[0] if names else None), node.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id != "DOMAINS":
            continue
        if isinstance(value, ast.Dict):
            keys = [key.value for key in value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)]
            return set(keys)
    return None


def harvest_ids(repo: Path = REPO) -> Optional[set]:
    """Ids available to cite. None when the ledger is absent, which is a defect."""
    path = Path(repo) / "docs" / "bible" / "harvest.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CorpusError("docs/bible/harvest.json could not be read: %s" % exc) from exc
    return {record.get("id") for record in data.get("records", []) if record.get("id")}


def _practice_problems(practice, where: str, repo: Path, ids: Optional[set]) -> List[str]:
    """A practice is instruction: an observation, its cause, its detection, its fix.

    The evidence rule is the one that makes the corpus a guide rather than a
    collection of opinions: a practice that cannot name three harnessed records
    is somebody's impression, and it belongs in a footnote, not in a canon.
    """
    if not isinstance(practice, dict):
        return ["%s: practice must be an object" % where]
    problems: List[str] = []
    unknown = set(practice) - PRACTICE_KEYS
    if unknown:
        problems.append("%s: unknown practice key(s) %s" % (where, sorted(unknown)))
    for key in ("symptom", "cause", "detection", "remedy"):
        value = practice.get(key)
        problems.extend(_text_problems(value, "%s practice.%s" % (where, key)))
        if isinstance(value, str) and DIGIT_RE.search(value):
            problems.append("%s: practice.%s carries a figure; a practice states the "
                            "shape of a failure, and the record that measured it is "
                            "cited in `evidence`" % (where, key))
    detection = practice.get("detection")
    if isinstance(detection, str) and detection.strip():
        tokens = _paths_in(detection)
        if not tokens:
            problems.append("%s: practice.detection names no repository path -- "
                            "what command would show this is happening?" % where)
        for token in tokens:
            if not (Path(repo) / token.split("#", 1)[0]).exists():
                problems.append("%s: practice.detection does not exist here: %s"
                                % (where, token))
    evidence = practice.get("evidence")
    if not isinstance(evidence, list) or len(set(evidence)) < MIN_EVIDENCE:
        problems.append("%s: practice.evidence must cite at least %d harvest records; "
                        "a rule distilled from fewer is an anecdote"
                        % (where, MIN_EVIDENCE))
        evidence = evidence if isinstance(evidence, list) else []
    if ids is None:
        problems.append("%s: docs/bible/harvest.json is absent, so no citation can "
                        "be checked -- run tools/bible_harvest.py --write" % where)
    else:
        for record_id in evidence:
            if record_id not in ids:
                problems.append("%s: practice.evidence cites %r, which the harvest "
                                "ledger does not contain" % (where, record_id))
    return problems


CONFIG_KINDS = ("text", "json", "path")
CONFIG_SCOPES = ("repo", "host")


def _config_problems(configs, repo: Path) -> Tuple[List[str], List[str]]:
    """The `configs` layer: the settings the corpus's instructions depend on.

    A canon of instruction with no configuration is a sermon. Every rule in this
    corpus that says "run the suite", "filter at the floor", "cap the books" is
    only true while a specific value holds somewhere in the tree, and those
    values drift silently: the floor threshold lives in two tools, the book cap
    lives in the corpus and in the checker, and a copied constant that disagrees
    with its twin produces two correct-looking answers.

    So each entry names a file, a value, and optionally the *other* files that
    must carry the same value (`mirrors`). A mirror that has drifted is a hard
    failure: that is the single-source-of-truth defect, caught where it starts
    rather than after two answers disagree.

    `scope: host` entries describe this machine (an interpreter path) and are
    reported as warnings elsewhere, the same distinction `box_profile.py` and
    `tools/platform_claims.py` make between unknown and absent.
    """
    problems: List[str] = []
    warnings: List[str] = []
    seen = set()
    for index, config in enumerate(configs):
        where = "configs[%d]" % index
        if not isinstance(config, dict):
            problems.append("%s: must be an object" % where)
            continue
        cid = config.get("id")
        if not isinstance(cid, str) or not re.match(r"^[a-z0-9][a-z0-9-]*$", cid or ""):
            problems.append("%s: id must be a lowercase slug" % where)
        elif cid in seen:
            problems.append("%s: duplicate id %r" % (where, cid))
        else:
            seen.add(cid)
        if isinstance(cid, str) and cid:
            where = "configs[%s]" % cid

        if not isinstance(config.get("name"), str) or not config.get("name", "").strip():
            problems.append("%s: name is empty -- say what the setting is for" % where)
        scope = config.get("scope", "repo")
        if scope not in CONFIG_SCOPES:
            problems.append("%s: scope must be one of %s" % (where, CONFIG_SCOPES))
        kind = config.get("kind")
        if kind not in CONFIG_KINDS:
            problems.append("%s: kind must be one of %s" % (where, CONFIG_KINDS))
            continue

        detail = config.get("detail", "")
        for text in (config.get("governs"), config.get("remedy")):
            if not isinstance(text, str) or not text.strip():
                problems.append("%s: `governs` and `remedy` are both required -- a "
                                "setting with no consequence and no repair is trivia"
                                % where)
                break
        remedy_problems = _provenance_problems(config.get("remedy"), where + ".remedy", repo)
        problems.extend(p for p in remedy_problems if "does not exist" in p)

        files = []
        if config.get("file"):
            files.append((config["file"], False))
        for mirror in config.get("mirrors") or []:
            files.append((mirror, True))
        if not files and kind != "path":
            problems.append("%s: names no `file`" % where)

        value = config.get("value")
        if kind in ("text", "json") and value is None:
            problems.append("%s: a %s config needs `value`" % (where, kind))

        if kind == "path":
            # A path config names a thing, not a line in a file: the interpreter,
            # a directory, a store. Checked once, outside the file loop.
            text = str(value)
            if not ((Path(repo) / text).exists() or Path(text).exists()):
                message = "%s: declared path does not exist here: %s" % (where, text)
                if scope == "host":
                    warnings.append("HOST-DIFFERS " + message)
                else:
                    problems.append(message)
            continue

        for rel, is_mirror in files:
            rel = str(rel)
            path = Path(repo) / rel
            label = "%s.mirrors[%s]" % (where, rel) if is_mirror else where
            if not path.exists():
                problems.append("%s: file does not exist: %s" % (label, rel))
                continue
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                problems.append("%s: unreadable: %s (%s)" % (label, rel, exc))
                continue

            if kind == "text":
                anchor = config.get("anchor")
                needle = str(value)
                if anchor:
                    hit = any(needle in line and anchor in line
                              for line in raw.splitlines())
                    if not hit:
                        message = ("%s: no line in %s carries %r with %r -- the value "
                                   "drifted from the corpus" % (label, rel, needle, anchor))
                    else:
                        continue
                elif needle in raw:
                    continue
                else:
                    message = "%s: %r not found in %s" % (label, needle, rel)
                if scope == "host":
                    warnings.append("HOST-DIFFERS " + message)
                else:
                    problems.append(message)
            elif kind == "json":
                try:
                    document = json.loads(raw)
                except json.JSONDecodeError as exc:
                    problems.append("%s: %s is not JSON (%s)" % (label, rel, exc))
                    continue
                key = config.get("key")
                if not isinstance(key, str) or not key.strip():
                    problems.append("%s: a json config needs `key`" % label)
                    continue
                node = document
                for part in key.split("."):
                    if isinstance(node, dict) and part in node:
                        node = node[part]
                    else:
                        node = None
                        break
                if node is None:
                    problems.append("%s: %s has no key %r" % (label, rel, key))
                elif not _same_scalar(node, value):
                    problems.append("%s: %s[%s] is %r, the corpus says %r"
                                    % (label, rel, key, node, value))

        if detail:
            _text_problems(detail, where + ".detail")
    return problems, warnings


def _same_scalar(left, right) -> bool:
    """Compare a declared value with what the file holds, numerically when both
    sides are numbers so that 20 and "20" are the same setting and 0.70 vs 0.7 is
    the same threshold."""
    try:
        return float(left) == float(right)          # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(left).strip() == str(right).strip()


def validate(data: Dict, repo: Path = REPO) -> Dict:
    """Validate, and RETURN the diagnostics. Never mutate the corpus.

    A validator with a side effect is not a predicate: two runs over the same
    dict would disagree about what it says, and a test that reuses a fixture
    would inherit the first reading's counts.
    """
    problems: List[str] = []
    warnings: List[str] = []
    canon_text = MANDATES.read_text(encoding="utf-8", errors="replace") \
        if MANDATES.exists() else None
    canon = canon_rules.rule_blocks(canon_text) if canon_text is not None else None
    try:
        domains = declared_domains(repo)
    except CorpusError as exc:
        problems.append(str(exc))
        domains = None
    try:
        evidence_ids = harvest_ids(repo)
    except CorpusError as exc:
        problems.append(str(exc))
        evidence_ids = None

    unknown = set(data) - TOP_KEYS
    if unknown:
        problems.append("unknown top-level key(s) %s" % sorted(unknown))
    if data.get("schema") != SCHEMA:
        problems.append("schema must be %r, found %r" % (SCHEMA, data.get("schema")))
    if not isinstance(data.get("title"), str) or not data.get("title", "").strip():
        problems.append("title is empty")
    status = data.get("status")
    if status not in STATUSES:
        problems.append("status must be one of %s" % (STATUSES,))
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(data.get("updated", ""))):
        problems.append("updated must be YYYY-MM-DD")

    # A COPY: filling in a default must not edit the corpus it is judging.
    limits = dict(data.get("limits") or {})
    if not isinstance(data.get("limits", {}), dict):
        problems.append("limits must be an object")
    unknown = set(limits) - set(CEILINGS)
    if unknown:
        problems.append("unknown limits key(s) %s" % sorted(unknown))
    for name, ceiling in sorted(CEILINGS.items()):
        value = limits.get(name, DEFAULT_LIMITS[name])
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            problems.append("limits.%s must be a positive integer" % name)
            limits[name] = DEFAULT_LIMITS[name]
        elif value > ceiling:
            problems.append("limits.%s may not exceed %d; a cap the author can raise "
                            "is not a cap" % (name, ceiling))
            limits[name] = ceiling

    # The configuration layer. Absent is a warning rather than a failure, because
    # a corpus can be written before its settings are pinned; present and drifted
    # is a failure, because the corpus's own instructions then describe a tree
    # that does not exist.
    configs = data.get("configs")
    if configs is None:
        warnings.append("no `configs` section: this corpus states rules without "
                        "pinning the settings that make them true")
        configs = []
    elif not isinstance(configs, list):
        problems.append("configs must be a list")
        configs = []
    else:
        config_problems, config_warnings = _config_problems(configs, repo)
        problems.extend(config_problems)
        warnings.extend(config_warnings)

    edition = data.get("edition")
    if edition is not None:
        if not isinstance(edition, dict):
            problems.append("edition must be an object")
        else:
            unknown = set(edition) - EDITION_KEYS
            if unknown:
                problems.append("unknown edition key(s) %s" % sorted(unknown))
            for key in sorted(EDITION_KEYS):
                if not isinstance(edition.get(key), str) or not edition.get(key, "").strip():
                    problems.append("edition.%s is empty" % key)

    glossary = data.get("glossary", [])
    if not isinstance(glossary, list):
        problems.append("glossary must be a list")
        glossary = []
    for index, entry in enumerate(glossary):
        where = "glossary[%d]" % index
        if not isinstance(entry, dict):
            problems.append("%s: must be an object" % where)
            continue
        unknown = set(entry) - GLOSSARY_KEYS
        if unknown:
            problems.append("%s: unknown key(s) %s" % (where, sorted(unknown)))
        for key in ("term", "gloss"):
            if not isinstance(entry.get(key), str) or not entry.get(key, "").strip():
                problems.append("%s: %s is empty" % (where, key))
        problems.extend(_text_problems(entry.get("gloss"), "%s gloss" % where))
        if isinstance(entry.get("gloss"), str) and DIGIT_RE.search(entry["gloss"]):
            problems.extend(_provenance_problems(entry.get("source"), where, repo))

    books = data.get("books")
    if not isinstance(books, list) or not books:
        problems.append("books must be a non-empty list")
        books = []
    if isinstance(limits.get("max_books"), int) and len(books) > limits["max_books"]:
        problems.append("%d books declared, limit is %d -- retire one to add one"
                        % (len(books), limits["max_books"]))

    index: Dict[str, Dict[int, Dict[int, Dict]]] = {}
    citations: List[Tuple[str, str]] = []          # (verse ref, citation)
    seen_text: Dict[str, List[str]] = {}

    for position, book in enumerate(books):
        where = "books[%d]" % position
        if not isinstance(book, dict):
            problems.append("%s: must be an object" % where)
            continue
        unknown = set(book) - BOOK_KEYS
        if unknown:
            problems.append("%s: unknown key(s) %s" % (where, sorted(unknown)))
        identifier = book.get("id")
        if not isinstance(identifier, str) or not re.match(r"^[a-z][a-z0-9_]*$", identifier):
            problems.append("%s: id must be lower_snake_case" % where)
            continue
        where = "book %r" % identifier
        if identifier in index:
            problems.append("%s: duplicate book id" % where)
            continue
        for key in ("name", "subtitle"):
            if not isinstance(book.get(key), str) or not book.get(key, "").strip():
                problems.append("%s: %s is empty" % (where, key))
        if book.get("genre") not in GENRES:
            problems.append("%s: genre must be one of %s" % (where, GENRES))
        if "domain" in book:
            domain = book["domain"]
            if domains is None:
                problems.append("%s: %s is unreadable, so the declared domain %r "
                                "cannot be verified"
                                % (where, DOMAINS_MODULE, domain))
            elif domain not in domains:
                problems.append("%s: domain %r is not declared in %s (declared: %s)"
                                % (where, domain, DOMAINS_MODULE,
                                   ", ".join(sorted(domains))))
        if status == "complete" and "introduction" not in book:
            problems.append("%s: a complete canon gives every book an introduction"
                            % where)
        introduction = book.get("introduction")
        if introduction is not None:
            if not isinstance(introduction, dict):
                problems.append("%s: introduction must be an object" % where)
            else:
                unknown = set(introduction) - INTRODUCTION_KEYS
                if unknown:
                    problems.append("%s: unknown introduction key(s) %s"
                                    % (where, sorted(unknown)))
                problems.extend(_prose_block_problems(introduction.get("paragraphs"),
                                                     introduction.get("sources", []),
                                                     "%s introduction" % where, repo))
        chapters = book.get("chapters")
        if not isinstance(chapters, list) or not chapters:
            problems.append("%s: chapters must be a non-empty list" % where)
            continue
        if isinstance(limits.get("max_chapters_per_book"), int) and \
                len(chapters) > limits["max_chapters_per_book"]:
            problems.append("%s: %d chapters, limit is %d"
                            % (where, len(chapters), limits["max_chapters_per_book"]))
        index[identifier] = {}
        for chapter_position, chapter in enumerate(chapters):
            cwhere = "%s chapter %d" % (where, chapter_position + 1)
            if not isinstance(chapter, dict):
                problems.append("%s: must be an object" % cwhere)
                continue
            unknown = set(chapter) - CHAPTER_KEYS
            if unknown:
                problems.append("%s: unknown key(s) %s" % (cwhere, sorted(unknown)))
            number = chapter.get("number")
            if number != chapter_position + 1:
                problems.append("%s: chapter numbering must be contiguous from 1; "
                                "found %r at position %d"
                                % (cwhere, number, chapter_position + 1))
                continue
            verses = chapter.get("verses")
            if not isinstance(verses, list) or not verses:
                problems.append("%s: verses must be a non-empty list" % cwhere)
                continue
            if isinstance(limits.get("max_verses_per_chapter"), int) and \
                    len(verses) > limits["max_verses_per_chapter"]:
                problems.append("%s: %d verses, limit is %d"
                                % (cwhere, len(verses), limits["max_verses_per_chapter"]))
            sections = chapter.get("sections", [])
            if not isinstance(sections, list):
                problems.append("%s: sections must be a list" % cwhere)
                sections = []
            previous = 0
            for section_position, section in enumerate(sections):
                swhere = "%s section %d" % (cwhere, section_position + 1)
                if not isinstance(section, dict):
                    problems.append("%s: must be an object" % swhere)
                    continue
                unknown = set(section) - SECTION_KEYS
                if unknown:
                    problems.append("%s: unknown key(s) %s" % (swhere, sorted(unknown)))
                start = section.get("before_verse")
                if not isinstance(start, int) or isinstance(start, bool) or \
                        not 1 <= start <= len(verses):
                    problems.append("%s: before_verse must name a verse in this chapter "
                                    "(1..%d)" % (swhere, len(verses)))
                elif start <= previous:
                    problems.append("%s: sections must ascend; %d follows %d"
                                    % (swhere, start, previous))
                else:
                    previous = start
                heading = section.get("heading")
                problems.extend(_text_problems(heading, "%s heading" % swhere))
                if isinstance(heading, str) and DIGIT_RE.search(heading):
                    problems.append("%s: a heading is a label, not a claim -- a figure "
                                    "belongs in a verse, where it must name its "
                                    "command" % swhere)
            if status == "complete" and (not sections or
                                         sections[0].get("before_verse") != 1):
                problems.append("%s: a complete canon opens every chapter with a "
                                "section heading at verse 1" % cwhere)
            index[identifier][number] = {}
            for verse_position, verse in enumerate(verses):
                vwhere = "%s chapter %d verse %d" % (where, number, verse_position + 1)
                if not isinstance(verse, dict):
                    problems.append("%s: must be an object" % vwhere)
                    continue
                unknown = set(verse) - VERSE_KEYS
                if unknown:
                    problems.append("%s: unknown key(s) %s" % (vwhere, sorted(unknown)))
                if verse.get("number") != verse_position + 1:
                    problems.append("%s: verse numbering must be contiguous from 1; "
                                    "found %r" % (vwhere, verse.get("number")))
                    continue
                text = verse.get("text")
                problems.extend(_text_problems(text, vwhere))
                index[identifier][number][verse_position + 1] = verse
                if isinstance(text, str):
                    key = " ".join(text.split()).lower()
                    seen_text.setdefault(key, []).append(
                        "%s %d:%d" % (identifier, number, verse_position + 1))
                if isinstance(text, str) and DIGIT_RE.search(text):
                    problems.extend(_provenance_problems(verse.get("source"),
                                                         vwhere, repo))
                citations_list = verse.get("cites", [])
                if not isinstance(citations_list, list):
                    problems.append("%s: cites must be a list" % vwhere)
                    citations_list = []
                for citation in citations_list:
                    if not isinstance(citation, str):
                        problems.append("%s: citation %r is not a string"
                                        % (vwhere, citation))
                        continue
                    citations.append(("%s %d:%d" % (identifier, number,
                                                    verse_position + 1), citation))
                if "statute" in verse:
                    problems.extend(_statute_problems(verse["statute"], vwhere, repo,
                                                      canon, canon_text))
                if "practice" in verse:
                    problems.extend(_practice_problems(verse["practice"], vwhere, repo,
                                                       evidence_ids))
                variants = verse.get("variants", [])
                if not isinstance(variants, list):
                    problems.append("%s: variants must be a list" % vwhere)
                    variants = []
                for variant_position, variant in enumerate(variants):
                    xwhere = "%s variant %d" % (vwhere, variant_position + 1)
                    if not isinstance(variant, dict):
                        problems.append("%s: must be an object" % xwhere)
                        continue
                    unknown = set(variant) - VARIANT_KEYS
                    if unknown:
                        problems.append("%s: unknown key(s) %s" % (xwhere, sorted(unknown)))
                    if not isinstance(variant.get("reading"), str) or \
                            not variant.get("reading", "").strip():
                        problems.append("%s: reading is empty (name the manuscript "
                                        "or the source of the variant)" % xwhere)
                    problems.extend(_text_problems(variant.get("text"), xwhere))
                    if not isinstance(variant.get("source"), str) or \
                            not variant.get("source", "").strip():
                        problems.append("%s: a variant must say where its reading comes "
                                        "from; that is the whole point of an apparatus"
                                        % xwhere)
                annotations = verse.get("annotations", [])
                if not isinstance(annotations, list):
                    problems.append("%s: annotations must be a list" % vwhere)
                    annotations = []
                if isinstance(limits.get("max_annotations_per_verse"), int) and \
                        len(annotations) > limits["max_annotations_per_verse"]:
                    problems.append("%s: %d annotations, limit is %d -- a verse with a "
                                    "page of notes has stopped being annotated"
                                    % (vwhere, len(annotations),
                                       limits["max_annotations_per_verse"]))
                for note_position, annotation in enumerate(annotations):
                    nwhere = "%s annotation %d" % (vwhere, note_position + 1)
                    if not isinstance(annotation, dict):
                        problems.append("%s: must be an object" % nwhere)
                        continue
                    unknown = set(annotation) - ANNOTATION_KEYS
                    if unknown:
                        problems.append("%s: unknown key(s) %s"
                                        % (nwhere, sorted(unknown)))
                    if annotation.get("kind") not in ANNOTATION_KINDS:
                        problems.append("%s: kind must be one of %s -- a reader is "
                                        "entitled to know whether a note is a reading "
                                        "or a claim" % (nwhere, ANNOTATION_KINDS))
                    problems.extend(_text_problems(annotation.get("note"),
                                                  "%s note" % nwhere))
                    if isinstance(annotation.get("note"), str) and \
                            DIGIT_RE.search(annotation["note"]):
                        problems.extend(_provenance_problems(annotation.get("source"),
                                                             nwhere, repo))

    # Citations and glossary pointers resolve after the whole index exists, so a
    # verse may cite forward: the canon is read as a whole, not streamed.
    for source_ref, citation in citations:
        problem = _resolve_citation(citation, index, repo)
        if problem:
            problems.append("%s %s" % (source_ref, problem))
    for position, entry in enumerate(glossary):
        reference = entry.get("see") if isinstance(entry, dict) else None
        if not isinstance(reference, str):
            continue
        problem = _resolve_citation(reference, index, repo)
        if problem:
            problems.append("glossary[%d]: see %s"
                            % (position, problem.replace("cites ", "", 1)))

    for key, refs in seen_text.items():
        if len(refs) > 1:
            warnings.append("the same verse text appears %d times (%s) -- usually a "
                            "paste, occasionally a refrain" % (len(refs), ", ".join(refs)))

    cited_by = reference_index(books)
    if status == "complete":
        for identifier, chapters in index.items():
            for number, verses in chapters.items():
                for verse_number, verse in verses.items():
                    ref = "%s %d:%d" % (identifier, number, verse_number)
                    if cited_by.get(ref) or verse.get("cites") or "statute" in verse \
                            or verse.get("annotations") or verse.get("variants"):
                        continue
                    problems.append("%s: a complete canon has no verse that does no "
                                    "work -- cite it, or give it a note, a statute or "
                                    "an apparatus" % ref)

    if problems:
        raise CorpusError("%s failed %d check(s):\n  - %s"
                          % (CORPUS.name, len(problems), "\n  - ".join(problems)))

    verses_flat = [verse for book in books for chapter in book.get("chapters", [])
                   for verse in chapter.get("verses", [])]
    return {
        "books": len(books),
        "chapters": sum(len(book.get("chapters", [])) for book in books),
        "verses": len(verses_flat),
        "citations": len(citations),
        "statutes": sum(1 for verse in verses_flat if "statute" in verse),
        "variants": sum(len(verse.get("variants", []) or []) for verse in verses_flat),
        "annotations": sum(len(verse.get("annotations", []) or [])
                           for verse in verses_flat),
        "sections": sum(len(chapter.get("sections", []) or [])
                        for book in books for chapter in book.get("chapters", [])),
        "glossary": len(glossary),
        "introductions": sum(1 for book in books if "introduction" in book),
        "configs": len(configs),
        "orphans": sorted(ref for ref, refs in cited_by.items() if not refs),
        "warnings": warnings,
    }


def iter_verses(books: Sequence[Dict]):
    """Yield (ref, verse) in corpus order."""
    for book in books:
        for chapter in book.get("chapters", []):
            for verse in chapter.get("verses", []):
                yield ("%s %d:%d" % (book["id"], chapter["number"], verse["number"]),
                       verse)


def reference_index(books: Sequence[Dict]) -> Dict[str, List[str]]:
    """Who cites each verse: the marginal column of a study Bible.

    Also the honest source for the orphan question -- a verse nothing points at
    is doing no work in a canon whose whole purpose is to be cited.
    """
    cited_by: Dict[str, List[str]] = {}
    for ref, verse in iter_verses(books):
        cited_by.setdefault(ref, [])
        for citation in verse.get("cites", []) or []:
            if isinstance(citation, str) and REF_RE.match(citation):
                normalized = " ".join(citation.split())
                if normalized != ref:
                    cited_by.setdefault(normalized, []).append(ref)
    return cited_by


# --------------------------------------------------------------------------- #
# The readable edition                                                        #
# --------------------------------------------------------------------------- #

def render_edition(data: Dict, corpus_label: str = "docs/bible/bible.json") -> str:
    """Deterministic markdown: headings in place, notes beside their verse.

    Inline notes rather than markdown footnote ids, because ids collide across
    chapters and a section-scoped marker is what a reader wants. Nothing here is
    timestamped: the edition must be byte-identical on a re-run, or the drift
    check would fail on a clock.

    ``corpus_label`` is the path the edition claims to have been generated from.
    It is a parameter because the header was hard-coded to the canon, and an
    edition of a second corpus that names the first corpus as its source is a
    false provenance line -- the kind of defect a reader cannot detect from the
    file itself.
    """
    cited_by = reference_index(data.get("books", []))
    lines = ["# %s" % data.get("title", "Untitled")]
    edition = data.get("edition")
    if isinstance(edition, dict):
        lines.extend(["", "*%s*" % edition.get("name", ""), "",
                      edition.get("basis", "")])
    lines.extend(["", "Status: **%s** · updated %s." % (data.get("status"),
                                                       data.get("updated")),
                  "",
                  "_Generated from `%s` by "
                  "`python tools/bible_check.py --render --write`. Do not edit this "
                  "file: edit the corpus._" % corpus_label])
    for book in data.get("books", []):
        lines.extend(["", "## %s" % book.get("name", book.get("id")), ""])
        # An unset subtitle would print a bare "### ", which reads as a defect
        # in the edition rather than as an omission in the corpus.
        if book.get("subtitle"):
            lines.extend(["### %s" % book["subtitle"], ""])
        register = book.get("genre") or ""
        if book.get("domain"):
            register = "%s · domain `%s`" % (register, book["domain"]) \
                if register else "domain `%s`" % book["domain"]
        if register:
            lines.extend(["*%s*" % register, ""])
        introduction = book.get("introduction")
        if isinstance(introduction, dict):
            lines.append("")
            for paragraph in introduction.get("paragraphs", []):
                lines.extend([paragraph, ""])
            sources = introduction.get("sources") or []
            if sources:
                lines.append("*Sources: %s*" % "; ".join(sources))
                lines.append("")
        for chapter in book.get("chapters", []):
            number = chapter.get("number")
            lines.extend(["", "#### %s %d" % (book.get("name", ""), number), ""])
            headings = {section.get("before_verse"): section.get("heading")
                        for section in chapter.get("sections", []) or []}
            for verse in chapter.get("verses", []):
                verse_number = verse.get("number")
                if verse_number in headings:
                    lines.extend(["##### %s" % headings[verse_number], ""])
                trailing = []
                if verse.get("cites"):
                    trailing.append("see " + ", ".join(verse["cites"]))
                if isinstance(verse.get("statute"), dict):
                    statute = verse["statute"]
                    trailing.append("statute %s → `%s`" % (statute.get("rule"),
                                                           statute.get("check")))
                suffix = (" *(%s)*" % "; ".join(trailing)) if trailing else ""
                lines.append("**%d** %s%s" % (verse_number, verse.get("text", ""),
                                              suffix))
                notes = (verse.get("annotations") or []) + (verse.get("variants") or [])
                if notes:
                    lines.append("")       # a blockquote after a bare line is a trap
                for annotation in verse.get("annotations", []) or []:
                    source = annotation.get("source")
                    lines.append("> **%s** — %s%s"
                                 % (annotation.get("kind"), annotation.get("note"),
                                    (" (`%s`)" % source) if source else ""))
                for variant in verse.get("variants", []) or []:
                    lines.append("> **Apparatus** — *%s*: %s (%s)"
                                 % (variant.get("reading"), variant.get("text"),
                                    variant.get("source")))
                practice = verse.get("practice")
                if isinstance(practice, dict):
                    lines.extend([
                        "",
                        "**Practice** — *%s*" % practice.get("symptom"),
                        "",
                        "- *cause*: %s" % practice.get("cause"),
                        "- *detect*: `%s`" % practice.get("detection"),
                        "- *remedy*: %s" % practice.get("remedy"),
                        "- *evidence*: %s" % ", ".join(practice.get("evidence", [])),
                    ])
                lines.append("")
            marginal = ["%d — cited by %s" % (int(ref.split(":")[1]), ", ".join(refs))
                        for ref, refs in sorted(
                            ((ref, refs) for ref, refs in cited_by.items()
                             if ref.startswith("%s %d:" % (book.get("id"), number))
                             and refs), key=lambda pair: int(pair[0].split(":")[1]))]
            if marginal:
                lines.extend(["_Marginal: %s._" % "; ".join(marginal), ""])
    glossary = data.get("glossary") or []
    if glossary:
        lines.extend(["", "## Glossary", ""])
        for entry in glossary:
            see = (" (see %s)" % entry["see"]) if entry.get("see") else ""
            lines.append("- **%s** — %s%s" % (entry.get("term"), entry.get("gloss"), see))
    lines.append("")
    # One blank line between blocks, never three: the gap is generated by
    # composition, so it must not depend on which branch wrote the last line.
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


def render_index(data: Dict) -> str:
    """Who cites whom, and which verses nothing points at."""
    books = data.get("books", [])
    cited_by = reference_index(books)
    lines = ["# Index of references", ""]
    for ref, verse in iter_verses(books):
        refs = cited_by.get(ref, [])
        cites = verse.get("cites", []) or []
        if not refs and not cites:
            continue
        lines.append("%-18s %s" % (ref, ("cited by " + ", ".join(refs)) if refs
                                   else "cited by nothing"))
    orphans = [ref for ref, _verse in iter_verses(books)
               if not cited_by.get(ref) and not _verse.get("cites")
               and "statute" not in _verse and not _verse.get("annotations")
               and not _verse.get("variants")]
    lines.extend(["", "## Verses that do no work", ""])
    lines.extend(["- %s" % ref for ref in orphans] or ["none"])
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Generations: the import graph, which cannot be embellished                   #
# --------------------------------------------------------------------------- #

def import_graph(root: Path = SRC) -> Tuple[Dict[str, Dict[str, int]], List[str]]:
    """Top-level packages under `root`, and how often one imports another."""
    if not root.is_dir():
        return {}, []
    packages = sorted({path.parts[0] for path in
                       (p.relative_to(root) for p in root.rglob("*.py")
                        if "__pycache__" not in p.parts)
                       if len(path.parts) > 1})
    known = set(packages)
    edges: Dict[str, Dict[str, int]] = {package: {} for package in packages}
    unreadable: List[str] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(root)
        if len(relative.parts) < 2:
            continue
        source = relative.parts[0]
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            unreadable.append(str(relative).replace("\\", "/"))
            continue
        targets: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:              # relative: stays inside the package
                    continue
                if node.module:
                    targets.append(node.module)
        for target in targets:
            head = target.split(".", 1)[0]
            if head in known and head != source:
                edges[source][head] = edges[source].get(head, 0) + 1
    return edges, unreadable


def render_genealogy(edges: Dict[str, Dict[str, int]], unreadable: Sequence[str]) -> str:
    total_edges = sum(sum(level.values()) for level in edges.values())
    houses = len(edges)
    lines = [
        "# The generations of the tree",
        "",
        "Generated, never composed: each \"begat\" below is an import edge found by",
        "reading `src/` with `ast`. Re-run it and the numbers must be the same.",
        "",
        "```bash",
        "python tools/bible_check.py --genealogy --write",
        "```",
        "",
        "%d houses, %d edges between them." % (houses, total_edges),
        "",
    ]
    for house in sorted(edges, key=lambda name: (-len(edges[name]), name)):
        children = edges[house]
        if not children:
            lines.append("And **%s** begat no other house." % house)
            continue
        ordered = sorted(children, key=lambda name: (-children[name], name))
        named = ["**%s**" % child for child in ordered[:3]]
        if len(ordered) > 3:
            rest = len(ordered) - 3
            lines.append("And **%s** begat %s, and of the rest there were %d."
                         % (house, ", ".join(named), rest))
        elif len(ordered) == 1:
            lines.append("And **%s** begat %s." % (house, named[0]))
        else:
            lines.append("And **%s** begat %s and %s."
                         % (house, ", ".join(named[:-1]), named[-1]))
    lines.extend(["", "| house | begat | edges out |", "|---|---|---|"])
    for house in sorted(edges):
        children = ", ".join(sorted(edges[house], key=lambda name: (-edges[house][name],
                                                                   name))) or "--"
        lines.append("| %s | %s | %d |"
                     % (house, children, sum(edges[house].values())))
    if unreadable:
        lines.extend(["", "Unreadable on this run (not counted): %s."
                      % ", ".join(unreadable)])
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The gate                                                                    #
# --------------------------------------------------------------------------- #

def _drift(path: Path, expected: str) -> Optional[str]:
    if not path.exists():
        return ("%s is absent; run the command in its header to create it"
                % path.name)
    if path.read_text(encoding="utf-8", errors="replace") != expected:
        return ("%s is stale relative to this run; regenerate it (the file is "
                "generated, so editing it is not a fix)" % path.name)
    return None


def _label(path: Path) -> str:
    """The corpus path as a reader would type it, for a provenance line."""
    try:
        return Path(path).resolve().relative_to(REPO).as_posix()
    except ValueError:
        return Path(path).as_posix()


def edition_path(corpus_path: Path) -> Path:
    """The generated edition that belongs to a corpus.

    A second corpus generated into the first corpus's edition would fail every
    drift check by construction, and a `--write` would overwrite the canon with
    it. Deriving the name from the corpus keeps one generated file per corpus,
    and keeps the canonical path byte-identical to the one every earlier
    reference names.
    """
    corpus_path = Path(corpus_path)
    if corpus_path.resolve() == Path(CORPUS).resolve():
        return EDITION
    return corpus_path.parent / (corpus_path.stem.upper() + "_BIBLE.md")


def generated_drift(root: Path, data: Dict,
                    corpus_path: Optional[Path] = None) -> List[str]:
    """Every generated file compared against what this run would write now.

    Split out of check() so the comparison can be exercised without a real
    corpus on disk: a drift check that only runs against HEAD cannot be shown
    to fire, and one that has never fired is unknown rather than working.

    ``corpus_path`` defaults to the canon, because that is the corpus the
    genealogy and the book edition belong to. When it names a different corpus,
    only that corpus's own edition is compared: holding an alternate corpus to
    the canon's generated file compares two different documents and calls the
    difference staleness.
    """
    problems: List[str] = []
    canonical = corpus_path is None or \
        Path(corpus_path).resolve() == Path(CORPUS).resolve()
    expected_files = [(Path(root) / "docs" / "bible" / "GENEALOGY.md",
                       render_genealogy(*import_graph(Path(root) / "src")))]
    if canonical:
        expected_files.append((Path(root) / "docs" / "bible" / "BOOK_OF_THE_TREE.md",
                               render_edition(data)))
    else:
        label = _label(Path(corpus_path))
        expected_files.append((edition_path(Path(corpus_path)),
                               render_edition(data, label)))
    for path, expected in expected_files:
        problem = _drift(path, expected)
        if problem:
            problems.append(problem)
    return problems


def check(root: Path = REPO, corpus_path: Path = CORPUS) -> Tuple[bool, List[str]]:
    """Validate the corpus AND both generated files. Messages, not a bare bool."""
    try:
        data = load(corpus_path)
        diagnostics = validate(data, root)
    except CorpusError as exc:
        return False, [str(exc)]
    messages: List[str] = []
    problems = generated_drift(root, data, corpus_path)
    # The harvest ledger is generated too. A corpus that cites evidence from a
    # ledger nobody regenerated is the same defect as a stale genealogy: the
    # citations still parse and no longer describe the sources.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import bible_harvest                       # noqa: PLC0415 (lazy: cycle)
        stale_paths, newer = bible_harvest.stale(root)
        problems.extend("harvest: " + message for message in stale_paths)
        if newer:
            messages.append("harvest: %d observation(s) recorded since the snapshot "
                            "(re-run tools/bible_harvest.py --write to fold them in)"
                            % newer)
    except ImportError as exc:                     # pragma: no cover - wiring
        messages.append("harvest: ledger not checked (%s)" % exc)
    for warning in diagnostics["warnings"]:
        messages.append("warning: " + warning)
    if problems:
        return False, problems + messages
    messages.insert(0, "%s -- %s: %d book(s), %d chapter(s), %d verse(s), %d "
                       "citation(s), %d statute(s), %d variant(s), %d annotation(s), "
                       "%d section(s), %d gloss(es), %d introduction(s), %d "
                       "config(s); generated files current"
                       % (data.get("title"), data.get("status"),
                          diagnostics["books"], diagnostics["chapters"],
                          diagnostics["verses"], diagnostics["citations"],
                          diagnostics["statutes"], diagnostics["variants"],
                          diagnostics["annotations"], diagnostics["sections"],
                          diagnostics["glossary"], diagnostics["introductions"],
                          diagnostics["configs"]))
    return True, messages


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true",
                       help="exit 1 on any defect (the gate)")
    group.add_argument("--genealogy", action="store_true",
                       help="derive the generations from the import graph")
    group.add_argument("--render", action="store_true",
                       help="print the readable edition")
    group.add_argument("--index", action="store_true",
                       help="who cites whom, and the verses that do no work")
    parser.add_argument("--write", action="store_true",
                        help="write the generated file instead of printing it")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--corpus", default=str(CORPUS))
    args = parser.parse_args(argv)

    corpus_path = Path(args.corpus).resolve()
    if args.genealogy:
        edges, unreadable = import_graph()
        text = render_genealogy(edges, unreadable)
        if args.write:
            GENEALOGY.parent.mkdir(parents=True, exist_ok=True)
            GENEALOGY.write_text(text, encoding="utf-8", newline="\n")
            print("wrote %s (%d houses, %d edges)"
                  % (GENEALOGY.relative_to(REPO), len(edges),
                     sum(sum(level.values()) for level in edges.values())))
            return 0
        sys.stdout.write(text)
        return 0

    try:
        data = load(corpus_path)
        diagnostics = validate(data, REPO)
    except CorpusError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.check:
        ok, messages = check(REPO, corpus_path)
        for message in messages:
            print(message)
        print("clean" if ok else "FAILED")
        return 0 if ok else 1
    if args.render:
        text = render_edition(data, _label(corpus_path))
        if args.write:
            target = edition_path(corpus_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
            print("wrote %s (%d lines)" % (target.relative_to(REPO),
                                           len(text.splitlines())))
            return 0
        sys.stdout.write(text)
        return 0
    if args.index:
        sys.stdout.write(render_index(data))
        return 0
    if args.json:
        print(json.dumps({"title": data.get("title"), "status": data.get("status"),
                          "summary": diagnostics}, indent=1))
        return 0

    print("%s -- %s, %d book(s), %d chapter(s), %d verse(s), %d citation(s), "
          "%d statute(s), %d variant(s), %d annotation(s), %d section(s), "
          "%d gloss(es), %d introduction(s)"
          % (data.get("title"), data.get("status"), diagnostics["books"],
             diagnostics["chapters"], diagnostics["verses"],
             diagnostics["citations"], diagnostics["statutes"],
             diagnostics["variants"], diagnostics["annotations"],
             diagnostics["sections"], diagnostics["glossary"],
             diagnostics["introductions"]))
    for warning in diagnostics["warnings"]:
        print("warning: " + warning)
    return 0


if __name__ == "__main__":
    sys.exit(main())
