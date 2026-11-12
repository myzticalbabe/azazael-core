#!/usr/bin/env python
# coding: utf-8
"""Measure a public text's prose: the numbers an impression cannot argue with.

WHY A TOOL AND NOT A DESCRIPTION
================================
"The style is repetitive and conversational" is not an audit, it is a mood. This prints the
quantities that make the claim falsifiable -- sentence-length distribution, pronoun balance,
negation and intensifier rates, sentence-initial conjunctions, anaphora -- and writes them as
JSON so two texts can be compared and the comparison re-run by anyone.

WHAT IT REFUSES TO DO
=====================
It does not judge truth, character or intent. It counts surface features of a text it was given.
The attribution question (who wrote/delivered it) is *not* answered here and must be recorded by
the caller: a transcribed speech and a staff-drafted message are different corpora even when they
carry the same name.

USAGE
=====
    python tools/prose_forensics.py --url https://www.whitehouse.gov/remarks/2025/01/the-inaugural-address/ \
        --label inaugural_2025 --out .prose/inaugural_2025.json
    python tools/prose_forensics.py --file .prose/text.txt --label x --out .prose/x.json
    python tools/prose_forensics.py --compare .prose/a.json .prose/b.json   # all twelve metric families

Network access is urllib + stdlib only: this tree has been burned by a dependency before, and a
measurement tool that cannot run offline on saved text is not a tool, it is an outage.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Tuple

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) prose_forensics/1.0 "
              "(text measurement; contact: local)")

PRONOUNS = {
    "first_singular": {"i", "me", "my", "mine", "myself"},
    "first_plural": {"we", "us", "our", "ours", "ourselves"},
    "second": {"you", "your", "yours", "yourself", "yourselves"},
    "third_plural": {"they", "them", "their", "theirs"},
}
NEGATIONS = {"not", "no", "never", "nobody", "nothing", "none", "cannot", "can't", "won't",
             "don't", "doesn't", "didn't", "isn't", "aren't", "wasn't", "weren't", "nor"}
INTENSIFIERS = {"very", "really", "totally", "completely", "absolutely", "extremely", "so",
                "incredibly", "tremendously", "massively", "highly", "deeply"}
DEIXIS = {"now", "today", "tonight", "here", "this", "these", "tomorrow", "yesterday"}
# a closed list, not a parser: an imperative detector that guesses is worse than one that
# under-counts and says so
IMPERATIVES = {"let", "look", "listen", "remember", "think", "imagine", "consider", "believe",
               "mark", "watch", "see", "take", "make", "build", "stop", "keep", "stand", "come",
               "go", "give", "tell", "ask", "be", "do", "never", "don't"}
CONCLUSIVE = {"will", "must", "shall", "going", "gonna", "cannot", "can't"}


class _Text(HTMLParser):
    """Paragraph-aware extraction: a list item and a sentence are not the same sentence."""

    BLOCK = {"p", "br", "div", "h1", "h2", "h3", "h4", "li", "blockquote", "tr"}
    DROP = {"script", "style", "noscript", "nav", "footer", "header"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.drop = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        if tag in self.DROP:
            self.drop += 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.DROP and self.drop:
            self.drop -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.drop:
            self.parts.append(data)


def fetch_text(url: str, timeout: int = 40) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                  "Accept": "text/html,*/*"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    charset = response.headers.get_content_charset() or "utf-8"
    html = raw.decode(charset, errors="replace")
    parser = _Text()
    parser.feed(html)
    text = "".join(parser.parts)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _syllables(word: str) -> int:
    groups = re.findall(r"[aeiouy]+", word.lower())
    count = len(groups)
    if word.lower().endswith("e") and count > 1:
        count -= 1
    return max(1, count)


STAGE_DIRECTIONS = ("Applause", "Laughter", "Inaudible", "Crosstalk", "Boos", "Cheers",
                     "Chanting", "Chants", "Jeers", "Heckling")


def strip_stage(text: str) -> Tuple[str, int]:
    """Remove transcription cues such as (Applause.) -- they are the room, not the prose.

    Measured on the 2025 inaugural: 56 stage directions in a 179-sentence transcript. Left in,
    they are 56 one-word sentences and every sentence statistic is wrong in the same direction.

    Both bracket styles are real transcripts: the inaugural archive writes `(Applause.)`,
    the spoken-address archive writes `[applause]` and `[applause and cheers]`. Measured
    2026-11-02 on the 2015 announcement: a stripper that knew only parentheses left
    `applause` as the **eighth most frequent word** (x43) in a 6.5k-word corpus and added
    43 one-word sentences to the count. The reading looked plausible; the top-word list
    is what exposed it, which is why the tool prints one.
    """
    removed = 0
    cues = "|".join(STAGE_DIRECTIONS)
    # one cue, or several joined by "and" / a comma: [applause and cheers], (laughter, applause)
    cue_run = r"(?:{c})(?:[\s,&]+(?:and[\s,]+)?(?:{c}))*".format(c=cues)
    pattern = re.compile(r"\s*[(\[]\s*" + cue_run + r"[.!]?\s*[)\]]", re.IGNORECASE)
    text, removed = pattern.subn("", text)
    # a bare ALL-CAPS cue on its own line, e.g. "(APPLAUSE)" or "[APPLAUSE]"
    text, n = re.subn(r"\n\s*[(\[](?:[A-Z][A-Za-z ]{2,40})[.!]?[)\]]\s*\n", "\n", text)
    removed += n
    return text, removed


# ---------------------------------------------------------------------------
# Sentence splitting
#
# A regex cannot express the rule that matters here. `(?<=[.!?])\s+` splits
# "Dr. Smith", "U.S. officials" and "John F. Kennedy" into extra sentences, and
# every downstream statistic -- mean length, the <=8-word share, sentence-initial
# runs -- inherits the error in the same direction. So the guards are written as
# two named predicates that can be read and tested on their own.
# ---------------------------------------------------------------------------

# A closed list, not a language model: this tool measures, it does not guess.
# "no" is deliberately absent -- it is both an abbreviation for "number" and an
# ordinary word, and "said no. It rained" must stay two sentences.
ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "rev", "hon", "st", "jr", "sr", "vs", "etc",
    "eg", "ie", "cf", "al", "inc", "ltd", "co", "corp", "vol", "fig", "ch", "sec",
    "pp", "gen", "sen", "rep", "gov", "pres", "lt", "col", "sgt", "capt", "maj",
    "adm", "u.s", "u.k", "u.n", "a.m", "p.m", "d.c", "b.c", "a.d",
}
TERMINALS = ".!?"


def _token_before(text: str, index: int) -> str:
    """The word ending at `index` (exclusive of it), lowercased.

    Walks back over letters, digits, dots and ampersands so a dotted initialism
    such as "U.S." comes back as "u.s" and can be matched whole.
    """
    start = index
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] in ".&'"):
        start -= 1
    return text[start:index].lower()


def _is_abbreviation_break(text: str, index: int) -> bool:
    """Guard A: the terminal closes a declared abbreviation, not a sentence."""
    token = _token_before(text, index)
    if not token:
        return False
    if token in ABBREVIATIONS:
        return True
    # a single letter is an initial ("John F. Kennedy"), not a sentence end
    if len(token) == 1 and token.isalpha():
        return True
    return False


def _is_false_lowercase_join(text: str, index: int, next_index: int) -> bool:
    """Guard B: the prose resumes in lower case, so the split was a false terminal.

    A real sentence start in English prose is capitalised, so a lower-case
    continuation rejoins ("U.S. officials", "etc. and"). A line break is still
    honoured: a paragraph resumed in lower case is a new block, not a
    continuation of the old one.
    """
    if "\n" in text[index + 1:next_index]:
        return False
    for char in text[next_index:]:
        if char.isspace():
            continue
        return char.islower()
    return False


def split_sentences(text: str) -> List[str]:
    """Split prose into sentences, applying the abbreviation and lowercase-rejoin guards."""
    sentences: List[str] = []
    start = 0
    index = 0
    length = len(text)
    while index < length:
        if text[index] not in TERMINALS:
            index += 1
            continue
        # collapse a run of terminals ("?!", "...") into one boundary
        end = index
        while end + 1 < length and text[end + 1] in TERMINALS:
            end += 1
        # A terminal ends a sentence only when whitespace (or end of text)
        # follows it. Without this guard "(Applause.)" splits into
        # "(Applause." + ")" -- a close paren is not a sentence break, and
        # re.split(r"(?<=[.!?])\s+") never did that, so this restores the
        # behaviour the count assertions in the tests were written against.
        # `str.isspace()`, not a hand-written set of characters. Scraped prose is full of
        # U+00A0 from `&nbsp;` after a full stop: measured 2026-11-02 on whitehouse.gov's
        # 2025 inaugural, a `" \t\n"` set refused every one of those boundaries and merged
        # the speech into 94 sentences, the longest 125 words. The old regex splitter
        # (`\s+`) handled it; the hand-written port silently did not.
        if end + 1 < length and not text[end + 1].isspace():
            index = end + 1
            continue
        next_index = end + 1
        while next_index < length and text[next_index].isspace():
            next_index += 1
        if next_index >= length:
            break
        boundary = True
        if text[index] == ".":
            if _is_abbreviation_break(text, index):
                boundary = False
            elif _is_false_lowercase_join(text, index, next_index):
                boundary = False
        if boundary:
            chunk = text[start:end + 1].strip()
            if chunk:
                sentences.append(chunk)
            start = next_index
        index = end + 1
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def measure(text: str, label: str = "", strip_cues: bool = False) -> Dict[str, Any]:
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    cues = 0
    if strip_cues:
        text, cues = strip_stage(text)
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    sentences = split_sentences(text)
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
    lower = [w.lower() for w in words]
    per_1000 = 1000.0 / max(len(words), 1)

    def rate(bag: set) -> float:
        return round(sum(1 for w in lower if w in bag) * per_1000, 2)

    lengths = [len(re.findall(r"[A-Za-z][A-Za-z'-]*", s)) for s in sentences]
    lengths = [n for n in lengths if n > 0]
    ordered = sorted(lengths)
    median = ordered[len(ordered) // 2] if ordered else 0
    p90 = ordered[int(len(ordered) * 0.9)] if ordered else 0

    initials = [re.findall(r"[A-Za-z']+", s)[:1] for s in sentences]
    initials = [w[0].lower() for w in initials if w]
    runs: List[Tuple[str, int]] = []
    for token in initials:
        if runs and runs[-1][0] == token:
            runs[-1] = (token, runs[-1][1] + 1)
        else:
            runs.append((token, 1))
    longest_run = max((n for _t, n in runs), default=0)

    trigrams: Dict[str, int] = {}
    for i in range(len(lower) - 2):
        key = " ".join(lower[i:i + 3])
        trigrams[key] = trigrams.get(key, 0) + 1
    top_trigrams = sorted(trigrams.items(), key=lambda kv: (-kv[1], kv[0]))[:8]

    counts: Dict[str, int] = {}
    for w in lower:
        counts[w] = counts.get(w, 0) + 1
    stop = {"the", "a", "an", "to", "of", "and", "in", "that", "is", "it", "we", "i", "our",
            "will", "be", "for", "on", "as", "this", "are", "with", "you", "they", "not", "at",
            "have", "has", "was", "from", "by", "but", "or", "so", "all", "their"}
    content = [(w, c) for w, c in counts.items() if w not in stop and len(w) > 2]
    content.sort(key=lambda kv: (-kv[1], kv[0]))

    syllables = sum(_syllables(w) for w in lower)
    asl = (len(words) / len(sentences)) if sentences else 0.0
    asw = (syllables / len(words)) if words else 1.0
    flesch = 206.835 - 1.015 * asl - 84.6 * asw

    return {
        "label": label or "unnamed",
        "words": len(words),
        "sentences": len(sentences),
        "stage_directions_removed": cues,
        "unique_words": len(counts),
        "type_token_ratio": round(len(counts) / max(len(words), 1), 4),
        "hapax_ratio": round(sum(1 for c in counts.values() if c == 1) / max(len(counts), 1), 4),
        "sentence_len": {"mean": round(asl, 2), "median": median, "p90": p90,
                         "max": max(lengths) if lengths else 0,
                         "pct_under_9": round(100.0 * sum(1 for n in lengths if n <= 8)
                                              / max(len(lengths), 1), 1),
                         "pct_over_39": round(100.0 * sum(1 for n in lengths if n >= 40)
                                              / max(len(lengths), 1), 1)},
        "pronouns_per_1000": {group: rate(bag) for group, bag in PRONOUNS.items()},
        "negation_per_1000": rate(NEGATIONS),
        "intensifier_per_1000": rate(INTENSIFIERS),
        "deixis_per_1000": rate(DEIXIS),
        "modals_per_1000": rate(CONCLUSIVE),
        "very_count": counts.get("very", 0),
        "superlatives": {"most": counts.get("most", 0),
                         "greatest": counts.get("greatest", 0),
                         "best": counts.get("best", 0),
                         "ever": counts.get("ever", 0)},
        "sentence_initial": {
            "and": initials.count("and"), "but": initials.count("but"),
            "so": initials.count("so"), "but_pct": round(100.0 * initials.count("but")
                                                        / max(len(initials), 1), 1),
            "longest_same_starter_run": longest_run,
        },
        "imperatives": {"count": sum(1 for w in initials if w in IMPERATIVES),
                        "share_pct": round(100.0 * sum(1 for w in initials if w in IMPERATIVES)
                                           / max(len(initials), 1), 1)},
        "punctuation": {"em_dash": text.count("\u2014"), "hyphen_dash": text.count(" -- "),
                        "parentheses": text.count("("), "questions": text.count("?"),
                        "exclamations": text.count("!")},
        "flesch_reading_ease": round(flesch, 1),
        "top_content_words": content[:12],
        "top_trigrams": top_trigrams,
        "first_sentence": sentences[0] if sentences else "",
        "last_sentence": sentences[-1] if sentences else "",
    }


def show(result: Dict[str, Any]) -> None:
    print("%s: %d words, %d sentences, mean %.2f / median %d / p90 %d"
          % (result["label"], result["words"], result["sentences"],
             result["sentence_len"]["mean"], result["sentence_len"]["median"],
             result["sentence_len"]["p90"]))
    print("  %.1f%% of sentences <= 8 words, %.1f%% >= 40; TTR %.3f"
          % (result["sentence_len"]["pct_under_9"], result["sentence_len"]["pct_over_39"],
             result["type_token_ratio"]))
    pronouns = ", ".join("%s %s" % (k, v) for k, v in result["pronouns_per_1000"].items())
    print("  per 1000 words: %s" % pronouns)
    print("  negation %s, intensifier %s, deixis %s, modals %s, very x%d"
          % (result["negation_per_1000"], result["intensifier_per_1000"],
             result["deixis_per_1000"], result["modals_per_1000"], result["very_count"]))
    print("  sentence-initial: and %d, but %d (%s%%), longest same-starter run %d"
          % (result["sentence_initial"]["and"], result["sentence_initial"]["but"],
             result["sentence_initial"]["but_pct"],
             result["sentence_initial"]["longest_same_starter_run"]))
    print("  Flesch %.1f; top words %s"
          % (result["flesch_reading_ease"],
             ", ".join("%s(%d)" % (w, c) for w, c in result["top_content_words"][:8])))


# ---------------------------------------------------------------------------
# The comparison table
#
# The instrument computes twelve metric families. Printing five of them made the
# other seven invisible in the one view a reader actually looks at, and a metric
# that is computed and hidden is the same defect as one that is not measured.
# ---------------------------------------------------------------------------

METRIC_FAMILIES = ("lexical", "length", "person", "negation", "intensifier", "deixis",
                   "modals", "superlatives", "sentence_initial", "imperatives",
                   "punctuation", "readability")


def _g(mapping: Any, key: str, default: Any = 0) -> Any:
    """Read a nested key without assuming the JSON was written by this version."""
    if not isinstance(mapping, dict):
        return default
    return mapping.get(key, default)


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return "%.2f" % value if abs(value) >= 10 else "%.3f" % value
    return str(value)


def metric_rows(result: Dict[str, Any]) -> List[Tuple[str, str, Any]]:
    """Every computed number as (family, field, value), in a fixed order."""
    length = _g(result, "sentence_len", {})
    person = _g(result, "pronouns_per_1000", {})
    sup = _g(result, "superlatives", {})
    head = _g(result, "sentence_initial", {})
    imp = _g(result, "imperatives", {})
    punc = _g(result, "punctuation", {})
    return [
        ("lexical", "unique_words", _g(result, "unique_words")),
        ("lexical", "type_token_ratio", _g(result, "type_token_ratio")),
        ("lexical", "hapax_ratio", _g(result, "hapax_ratio")),
        ("length", "mean", _g(length, "mean")),
        ("length", "median", _g(length, "median")),
        ("length", "p90", _g(length, "p90")),
        ("length", "max", _g(length, "max")),
        ("length", "pct_under_9", _g(length, "pct_under_9")),
        ("length", "pct_over_39", _g(length, "pct_over_39")),
        ("person", "first_singular", _g(person, "first_singular")),
        ("person", "first_plural", _g(person, "first_plural")),
        ("person", "second", _g(person, "second")),
        ("person", "third_plural", _g(person, "third_plural")),
        ("negation", "per_1000", _g(result, "negation_per_1000")),
        ("intensifier", "per_1000", _g(result, "intensifier_per_1000")),
        ("intensifier", "very_count", _g(result, "very_count")),
        ("deixis", "per_1000", _g(result, "deixis_per_1000")),
        ("modals", "per_1000", _g(result, "modals_per_1000")),
        ("superlatives", "most", _g(sup, "most")),
        ("superlatives", "greatest", _g(sup, "greatest")),
        ("superlatives", "best", _g(sup, "best")),
        ("superlatives", "ever", _g(sup, "ever")),
        ("sentence_initial", "and", _g(head, "and")),
        ("sentence_initial", "but", _g(head, "but")),
        ("sentence_initial", "but_pct", _g(head, "but_pct")),
        ("sentence_initial", "longest_same_starter_run", _g(head, "longest_same_starter_run")),
        ("imperatives", "count", _g(imp, "count")),
        ("imperatives", "share_pct", _g(imp, "share_pct")),
        ("punctuation", "em_dash", _g(punc, "em_dash")),
        ("punctuation", "hyphen_dash", _g(punc, "hyphen_dash")),
        ("punctuation", "parentheses", _g(punc, "parentheses")),
        ("punctuation", "questions", _g(punc, "questions")),
        ("punctuation", "exclamations", _g(punc, "exclamations")),
        ("readability", "flesch_reading_ease", _g(result, "flesch_reading_ease")),
    ]


def show_compare(results: List[Dict[str, Any]]) -> None:
    """Print every metric the instrument computes, side by side, for each text.

    Previously this showed five columns (mean length, we, I, negation,
    intensifiers, Flesch and word count). Everything else the tool computed --
    the length tail, second and third person, deixis, modals, superlatives,
    sentence-initial runs, imperatives, punctuation, lexical diversity -- was
    measured and then thrown away at the only step a reader sees.
    """
    if not results:
        print("REFUSED: --compare needs at least one measured JSON file")
        return
    labels = [str(_g(r, "label", "unnamed")) for r in results]
    widths = [max(10, min(len(label), 22)) for label in labels]
    name_width = 32
    rule = "-" * (name_width + sum(width + 1 for width in widths))

    def row(cells: List[str], first: str) -> str:
        line = first.ljust(name_width)
        for cell, width in zip(cells, widths):
            line += " " + cell.rjust(width)
        return line

    print(row([label[:width] for label, width in zip(labels, widths)],
              "metric (%d families)" % len(METRIC_FAMILIES)))
    print()
    print(rule)
    for field, title in (("words", "corpus.words"),
                         ("sentences", "corpus.sentences"),
                         ("stage_directions_removed", "corpus.stage_directions_removed")):
        print(row([_fmt(_g(result, field)) for result in results], "  " + title))
    print(rule)

    per_result = [{(family, field): value for family, field, value in metric_rows(result)}
                  for result in results]
    last_family = ""
    for family, field, _value in metric_rows(results[0]):
        if family != last_family:
            print("  [%s]" % family)
            last_family = family
        cells = [_fmt(table.get((family, field), "")) for table in per_result]
        print(row(cells, "    %s.%s" % (family, field)))

    print(rule)
    print("%d metric families printed: %s" % (len(METRIC_FAMILIES), ", ".join(METRIC_FAMILIES)))
    print("Not in this table: top_content_words, top_trigrams, first/last sentence -- "
          "qualitative detail, kept in the JSON.")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure the prose of a public text.")
    parser.add_argument("--url", default="")
    parser.add_argument("--file", default="")
    parser.add_argument("--label", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--strip-stage", action="store_true",
                        help="remove (Applause.) style cues before measuring",
                        )
    parser.add_argument("--compare", nargs="*", default=[])
    parser.add_argument("--start-after", default="",
                        help="begin after this literal marker (refuses if absent)")
    parser.add_argument("--end-before", default="",
                        help="end before this literal marker (refuses if absent)")
    args = parser.parse_args(argv)

    if args.compare:
        loaded = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.compare]
        show_compare(loaded)
        return 0

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.url:
        text = fetch_text(args.url)
    else:
        parser.print_help()
        return 2

    # Trimming is explicit and refusable. Site chrome (nav, title, date, footer) is prose too,
    # and measuring it as if it were the speech is exactly the kind of quiet miscount that makes
    # a number untrustworthy. If a marker is missing, stop rather than measure everything.
    for marker, before in ((args.start_after, False), (args.end_before, True)):
        if not marker:
            continue
        index = text.find(marker)
        if index < 0:
            print("REFUSED: marker %r is not in the fetched text -- not measuring the whole page"
                  % marker, file=sys.stderr)
            return 3
        text = text[index + len(marker):] if not before else text[:index]
    result = measure(text, args.label or args.file or args.url, strip_cues=args.strip_stage)
    show(result)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=1), encoding="utf-8")
        print("wrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
