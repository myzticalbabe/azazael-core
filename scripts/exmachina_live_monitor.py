# coding: utf-8
"""EX MACHINA live monitor - the epistemic-organ wallboard, standalone.

This is the Ex Machina tier's OWN viewer. It reads the same artifacts and the
same `evolution_result` rows as `live_monitor.py`, but renders ONLY the Ex
Machina panel, so the epistemic layer can be watched on its own - and without
the panel being injected into the Azazael wallboard.

The panel logic lives in `src/CORE/WORMS/expansion/exmachina.py` (judging
organs) and `exmachina_operators.py` (operator organs); this file is only the
read-side viewer plus the terminal/glyph scaffolding the panel needs.

Usage:
    python exmachina_live_monitor.py                 # live, 30s refresh
    python exmachina_live_monitor.py --refresh 10
    python exmachina_live_monitor.py --once           # one frame to stdout
    python exmachina_live_monitor.py --once --no-color

Gate: scripts/verify_exmachina_panel.py
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
import re
import shutil as _sh

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# the same store the rest of the ecosystem reads; overridable for tests
DB = os.environ.get("AZAZAEL_MEMORY_DIR",
                    os.path.join(os.path.expanduser("~"), ".azazael"))
if not DB.endswith(".db"):
    DB = os.path.join(DB, "memory_store.db")

REFRESH = 30
LOG_PATH = os.path.join(_HERE, "..", "logs", "exmachina_live_monitor.log")
LOG_MAX = 1_000_000  # rotate above ~1 MB, same policy as live_monitor.py

# --- colour -----------------------------------------------------------
C_RED = "\x1b[91m"
C_GRN = "\x1b[92m"
C_YEL = "\x1b[93m"
C_CYN = "\x1b[96m"
C_BLD = "\x1b[1m"
C_ORG = "\x1b[38;5;202m"
C_RST = "\x1b[0m"

# hue wheel the panel drifts along, one step per rendered frame
_RAINBOW = [196, 202, 208, 214, 220, 190, 154, 118, 82, 46,
            47, 48, 49, 50, 51, 45, 39, 33, 27, 21]
_FRAME_NO = [0]


def db_connect():
    """Open the DB read-only, falling back to a local copy on lock errors."""
    for _ in range(3):
        try:
            con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
            con.execute("SELECT 1 FROM memories LIMIT 1").fetchone()
            return con
        except Exception:
            time.sleep(1)
    tmp = os.path.join(tempfile.gettempdir(), "ms_livemon.db")
    for _ in range(3):
        try:
            shutil.copy2(DB, tmp)
            con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            con.execute("SELECT 1 FROM memories LIMIT 1").fetchone()
            return con
        except Exception:
            time.sleep(2)
    return None


def parse_data(d):
    try:
        return json.loads(d) if isinstance(d, str) else d
    except Exception:
        return {}


def latest(con, event_type, n=1):
    rows = con.execute(
        "SELECT timestamp, data FROM memories WHERE event_type=? ORDER BY id DESC LIMIT ?",
        (event_type, n)).fetchall()
    out = []
    for ts, data in rows:
        out.append((ts, parse_data(data)))
    return out


# ══════════════════════════════════════════════════════════════════════
# DISPLAY WIDTH + GLYPH CAPABILITY (2026-09-12)
#
# The monitor's never-wrap invariant depends on measuring lines the way the
# TERMINAL does. `len()` was safe only while every glyph was ASCII. Measured on
# this machine, every emoji is `east_asian_width == 'W'` and therefore occupies
# TWO columns while `len()` reports 1 -- so adding one emoji silently shifts
# every line by a column.
#
# Three independent capability blockers were reproduced here, not assumed:
#   1. piped stdout is cp1252 -> an emoji raises UnicodeEncodeError (rc=1);
#      even U+2500 (box drawing) cannot be encoded.
#   2. the console is CP437 (`chcp` 437, OutputEncoding IBM437) -> no emoji.
#   3. `_vis_len` counted code points, not columns.
#
# So glyphs are a capability, resolved once at startup and degradable:
#   emoji   UTF-8 console (or AZAZAEL_EMOJI=1), 2 columns each
#   unicode UTF-8 output, narrow symbols, 1 column each
#   ascii   anything else -- the historical safe default
# ══════════════════════════════════════════════════════════════════════
_ANSI_RE = None
_ZW = ("\ufe0e", "\ufe0f", "\u200d", "\u200b")   # variation selectors + ZWJ


def _disp_w(ch):
    """Display columns for one character, as the terminal counts them."""
    import unicodedata
    if ch in _ZW or unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _vis_len(s):
    """Visible width in COLUMNS, ignoring ANSI color codes."""
    global _ANSI_RE
    if _ANSI_RE is None:
        _ANSI_RE = re.compile("\x1b\\[[0-9;]*m")
    plain = _ANSI_RE.sub("", s)
    if plain.isascii():          # fast path: unchanged behaviour for ASCII
        return len(plain)
    return sum(_disp_w(c) for c in plain)


def _fit(s, width):
    """Truncate to window width in COLUMNS; never wrap, never scroll.

    Colors are stripped on truncation (simpler than re-closing codes, and
    truncated lines are rare edge cases on narrow windows).
    """
    global _ANSI_RE
    if _ANSI_RE is None:
        _ANSI_RE = re.compile("\x1b\\[[0-9;]*m")
    if _vis_len(s) <= width:
        return s
    plain = _ANSI_RE.sub("", s)
    ascii_only = plain.isascii()
    out, used = [], 0
    for ch in plain:
        cw = 1 if ascii_only else _disp_w(ch)
        if used + cw > max(0, width):
            break
        out.append(ch)
        used += cw
    return "".join(out)


# Every glyph, classified by MEASURED width rather than by hope. Two rules
# are enforced in set_glyphs(), so a bad glyph degrades the tier instead of
# wrapping the panel:
#   emoji   exactly one codepoint and 2 columns (no ZWJ, no VS16)
#   unicode exactly 1 column, east_asian_width in (N, Na) -- NEVER 'A'
#
# 'A' (ambiguous) is the trap: this machine renders it as one column, a CJK
# font renders it as two, and a line modelled as N columns then comes out
# N+1 long and wraps. That is why the previous tier's triangle / diamond /
# square / circle glyphs (all EAW 'A') are gone.
_GLYPH_TIERS = {
    # 2 columns each: only safe once _vis_len counts columns (above).
    "emoji": {
        "ok": "\U0001f7e2", "bad": "\U0001f534", "warn": "\U0001f7e1",
        "speculum": "\U0001f50d", "testudo": "\U0001f4d0", "fulcrum": "\U0001f5dc",
        "oracle": "\U0001f52e", "morph": "\U0001f9ec", "circ": "\U0001f501",
        # MICROSCOPE, not BRAIN: BRAIN is already the PROTEUS glyph, and a
        # duplicate glyph in one panel makes two rows read as one.
        "cranium": "\U0001f52c",
        "proteus": "\U0001f9e0", "registered": "\U0001f4e6", "blocked": "\U0001f6d1",
        # the mood band: single codepoints, all EAW 'W'
        "mood0": "\U0001f60c", "mood1": "\U0001f9d0", "mood2": "\U0001f61f",
        "mood3": "\U0001f630", "mood4": "\U0001f480",
        "beat": "\U0001f493", "flat": "\U0001f4a4", "vital": "\U0001f4c8",
        "op": "\u2699",
        # ABACUS: measured EAW 'W', so one emoji cell. The tensor genome is the
        # compiled-computation artifact, which is what an abacus is.
        "genome": "\U0001f9ee",
        # RADIOACTIVE SIGN: measured EAW 'N', so it is one cell here and legal in
        # the unicode tier too. Radiation is what a mutagen IS, and it is not the
        # TENSOR organ's abacus -- a duplicate glyph makes two rows read as one.
        "mutagen": "\u2622",
        # NEUTRAL: a state that is neither success nor failure. The success
        # checkmark was being used for below-threshold severity signals, which
        # rendered 'mdl fitness bought with description length' as a tick beside
        # the word 'mdl' -- reading as "mdl is fine". A severity meter must
        # never show a success glyph.
        "neutral": "\u25aa",
    },
    # Narrow (EAW N/Na) symbols: 1 column everywhere, so they cost nothing
    # structurally and cannot surprise the never-wrap invariant.
    "unicode": {
        "ok": "\u2713", "bad": "\u2717", "warn": "!",
        "speculum": "\u25c9", "testudo": "\u25ca", "fulcrum": "\u22c6",
        "oracle": "\u2205", "morph": "\u2206", "circ": "\u21bb",
        # U+229A CIRCLED RING OPERATOR: measured EAW 'N'. The obvious choice,
        # U+25CB WHITE CIRCLE, is EAW 'A' and would wrap under a CJK font.
        "cranium": "\u229a",
        "proteus": "\u2297", "registered": "-", "blocked": "\u2717",
        # faces cannot be EAW-N; the unicode tier uses text faces
        "mood0": "=)", "mood1": "=|", "mood2": "=(", "mood3": "=O", "mood4": "XX",
        "beat": "o", "flat": "z", "vital": "~", "op": "+",
        # U+229E SQUARED PLUS: measured EAW 'N'. U+2295 CIRCLED PLUS and
        # U+25A6 SQUARE WITH ORTHOGONAL CROSS are EAW 'A' and would wrap under
        # a CJK font, so they were rejected by measurement, not by taste.
        "genome": "\u229e",
        # U+2622 RADIOACTIVE SIGN: measured EAW 'N' (see the emoji tier note).
        "mutagen": "\u2622",
        # U+2219 BULLET OPERATOR: measured EAW 'N'. U+00B7 MIDDLE DOT and
        # U+2022 BULLET are the obvious picks and are both EAW 'A'.
        "neutral": "\u2219",
    },
    "ascii": {
        "ok": "+", "bad": "x", "warn": "!",
        "speculum": "*", "testudo": "~", "fulcrum": "=",
        "oracle": "o", "morph": "m", "circ": "c",
        "cranium": "k",
        "proteus": "#", "registered": "-", "blocked": "x",
        "mood0": ":)", "mood1": ":|", "mood2": ":(", "mood3": ":O", "mood4": "XX",
        "beat": "o", "flat": "z", "vital": "~", "op": "+",
        "genome": "T", "neutral": "-",
        # 'R' for radiation -- the ASCII stand-in for a mutagen.
        "mutagen": "R",
    },
}

def _glyph_violation(tier, g):
    """Why this glyph is illegal for the tier, or None if it is legal.

    The classification is by `east_asian_width`, NOT by whether this module's
    own width model happens to agree. A glyph whose width the terminal decides
    differently than we do is the dangerous case, and asking our own model
    whether our own model is right cannot detect it. An earlier version of
    this function did exactly that, and passed a triangle (EAW 'A') as a
    one-column glyph.

      ascii    every character is ASCII
      unicode  every character is EAW N/Na (one column everywhere)
      emoji    ONE codepoint, EAW in (W, F, N, Na)

    Multi-character ASCII strings ("=)") are fine in the text tiers: those
    are several narrow characters, not one wide glyph.
    """
    import unicodedata
    if not isinstance(g, str) or not g:
        return "empty"
    if tier == "ascii":
        return None if g.isascii() else "not ascii"
    if tier == "emoji" and len(g) != 1:
        # A ZWJ or VS16 sequence renders as ONE glyph from SEVERAL codepoints,
        # so summing the parts over-counts and the line comes out short.
        return "%d codepoints" % len(g)
    for ch in g:
        eaw = unicodedata.east_asian_width(ch)
        if eaw == "A":
            return "ambiguous width"
        if tier == "unicode" and eaw in ("W", "F"):
            return "2 columns"
    return None


def _glyph_violations(tier, table):
    """Which glyphs break their tier's rule. Empty list means legal."""
    bad = []
    for name, g in (table or {}).items():
        why = _glyph_violation(tier, g)
        if why:
            bad.append("%s(%s)" % (name, why))
    return bad

GLYPH = dict(_GLYPH_TIERS["ascii"])
GLYPH_TIER = "ascii"


def detect_glyph_tier(forced="auto"):
    """Resolve the glyph tier from what the console can ACTUALLY take."""
    if forced in _GLYPH_TIERS:
        return forced
    try:
        enc = (sys.stdout.encoding or "").lower()
        if "utf" not in enc:
            return "ascii"          # cp1252 cannot encode any of these glyphs
    except Exception:
        return "ascii"
    # A UTF-8 stdout stream is not enough on Windows: the CONSOLE code page
    # decides what can be shown. CP437 has no emoji and no box drawing.
    try:
        import ctypes
        cp = int(ctypes.windll.kernel32.GetConsoleOutputCP() or 0)
        if cp and cp != 65001:
            return "ascii"
    except Exception:
        pass
    want_emoji = str(os.environ.get("AZAZAEL_EMOJI", "0")).strip().lower() not in (
        "0", "", "false", "no", "off")
    return "emoji" if want_emoji else "unicode"


def set_glyphs(tier="auto"):
    """Select the glyph tier and make stdout able to carry it.

    `errors='replace'` is the safety net: even if a legacy console cannot
    render a glyph, the monitor substitutes rather than crashing mid-frame.

    The tier is VALIDATED before it is adopted. A glyph table that is one
    column wider than it claims does not raise, it wraps -- and a wrapped
    line breaks the one invariant this whole panel is built around, silently,
    on someone else's terminal. So a tier with any illegal glyph is dropped
    rather than used.
    """
    global GLYPH, GLYPH_TIER
    order = [detect_glyph_tier(tier)]
    for fb in ("unicode", "ascii"):
        if fb not in order:
            order.append(fb)
    for name in order:
        table = _GLYPH_TIERS[name]
        bad = _glyph_violations(name, table)
        if bad:
            continue
        if name != "ascii":
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                continue
        GLYPH_TIER, GLYPH = name, dict(table)
        return GLYPH_TIER
    GLYPH_TIER, GLYPH = "ascii", dict(_GLYPH_TIERS["ascii"])
    return GLYPH_TIER


# ══════════════════════════════════════════════════════════════════════
# EX MACHINA — the epistemic tier (2026-09-12)
#
# PROTEUS optimises better; Ex Machina decides whether to BELIEVE it. On
# 2026-09-11 the pipeline beat its frozen pool on 50/50 cycles and SPRT
# promoted 50/50 while the fresh holdout admitted 0 of 4. The optimiser was
# fine. Nothing could notice it was being gamed by its own fitness function.
#
# SHIFTING COLOURS: hue = (frame + line*2 + phase) % wheel. The frame counter
# already advances once per refresh, so every line drifts continuously while
# neighbouring lines stay offset from one another. Everything here is
# pure ASCII (like _SPARK) because the console codepage mangles box glyphs.
# ══════════════════════════════════════════════════════════════════════
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_EX_STATE = os.path.join(_ROOT, "state")
# Must match `MORPH_HISTORY` in evolution_cycle.py. This pointed at
# logs/morph_history.jsonl until 2026-09-12 — a path no producer writes — so
# the MORPH row silently reported "no data" while the artifact existed.
_EX_MORPH = os.path.join(_EX_STATE, "proteus_morph.jsonl")
_EX_SRC = os.path.join(_ROOT, "src", "CORE", "WORMS", "expansion")

# tier 'live' = judge: decides whether to BELIEVE the evidence.
# tier 'op'   = operator: produces and routes evidence (exmachina_operators).
# All sixteen are implemented; nothing is listed as a design promise.
EX_ORGANS = (
    ("SPECULUM", "live", "reward hacking: objective up while the held-out check stays zero"),
    ("TESTUDO", "live", "conformal calibration of the gate's own thresholds"),
    ("FULCRUM", "live", "compression-based novelty (normalised compression distance)"),
    ("VIGIL", "live", "anytime-valid evidence: an e-process, safe to peek every cycle"),
    ("FILUM", "live", "permutation entropy + DFA: is the search still finding anything"),
    ("PERENNIS", "live", "era invariance: does the fitness relation transport at all"),
    ("COMPAGES", "live", "cointegration: is there a long-run link to the independent check"),
    ("BREVIS", "live", "two-part MDL: bits spent describing the genome vs the data"),
    ("EMERGON", "live", "causal emergence: which abstraction grain carries the most EI"),
    ("RADIX", "op", "causal discovery: is the objective d-separated from the check"),
    ("CATENA", "op", "trust-region Bayesian optimisation over a Cholesky GP"),
    ("NAVIS", "op", "echo-state reservoir: spectral radius enforced, readout closed-form"),
    ("VELA", "op", "hyperdimensional binding, with recall measured not assumed"),
    ("NEXUS", "op", "sparse mixture-of-experts: routing entropy is reported"),
    ("OPUS", "op", "KAN spline backbone (interpolates; does not extrapolate)"),
    ("LIMEN", "op", "active-learning queue ranked by expected information gain"),
    ("MUTAGEN", "live", "genome selection: did the genome grow, or was it given away"),
)

# what each severity signal means, in the order the meters are shown
_EX_SIGNAL_TEXT = (
    ("gaming", "objective rising while the check stays at zero"),
    ("contradiction", "improvement claimed, anytime-valid evidence weak"),
    ("causal_absence", "objective d-separated from the check"),
    ("non_invariance", "fitness relation does not transport across eras"),
    ("cointegration", "no long-run link between objective and check"),
    ("calibration", "gate thresholds are not calibrated"),
    ("surrogate", "no fitted surrogate can predict the objective"),
    ("degeneracy", "the search is repeating itself"),
    ("mdl", "fitness bought with description length"),
    ("routing", "expert router sends everything to one expert"),
    ("memory", "associative store cannot recall what it stored"),
    ("staleness", "an artifact stopped being written"),
    ("mutagenesis", "the genome grows on trials that never changed the trainer call"),
)

# mood band -> colour, glyph key, and the word shown
_EX_BANDS = (
    (C_GRN, "mood0", "SERENE"),
    (C_YEL, "mood1", "WATCHFUL"),
    ("\x1b[38;5;208m", "mood2", "STRAINED"),
    (C_RED, "mood3", "ALARMED"),
    (C_BLD + C_RED, "mood4", "CRITICAL"),
)

# artifacts whose fresh-ness the panel watches (name -> file under state/)
_EX_ARTIFACTS = (
    ("oracle", "fitness_oracle.json"),
    ("gate", "fitness_oracle.last.json"),
    # NOTE the filename must match what the CYCLE writes
    # (`CIRCULATION_JSON` in evolution_cycle.py). It read
    # "proteus_allocator.json" until 2026-09-12, a file nothing produces, so
    # the allocator's age was never reported and the CIRC row always said
    # "idle" even while the allocator was actively learning.
    ("alloc", "proteus_circulation.json"),
    ("archive", "qd_archive.json"),
    ("promote", "evolved_rl_params.json"),
    # Must match `TENSOR_GENOME_JSON` in evolution_cycle.py. Same class of
    # defect the two paths above already carried once: a name the producer
    # never writes makes the row report on nothing.
    ("tensor", "tensor_genome.json"),
)
_EX_LOAD_ERR = []
_EX_CORE = [None]        # loaded module, or False when it could not load
_EX_MOOD = [None]        # MoodTracker instance -> hysteresis across frames


def ex_core():
    """Load the Ex Machina tier BY PATH, once.

    `src/CORE/WORMS/expansion` is put on sys.path instead of importing the
    `CORE` package: the tier is stdlib-only and has no package-relative
    imports, so a bare directory load keeps the whole worm package -- and
    whatever it pulls in -- out of a 30-second refresh loop.
    """
    if _EX_CORE[0] is not None:
        return _EX_CORE[0] or None
    try:
        if _EX_SRC not in sys.path:
            sys.path.append(_EX_SRC)
        import exmachina as _core
        _EX_CORE[0] = _core
    except Exception as exc:
        _EX_CORE[0] = False
        _EX_LOAD_ERR.append("%s: %s" % (type(exc).__name__, exc))
    return _EX_CORE[0] or None


def _ex_now():
    """Wall-clock epoch, unless a frozen run pinned it.

    `scripts/_exmachina_freeze.py` exports AZAZAEL_FROZEN_NOW so two runs of one
    snapshot can be compared byte-for-byte. Nothing here read it until
    2026-09-14, so the ages, the header and the pulse still moved between runs
    (archive 10% vs archive 11%) and the freeze gate could never be
    byte-comparable. Unpinned runs are unchanged: this is time.time().
    """
    raw = os.environ.get("AZAZAEL_FROZEN_NOW")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return time.time()


def ex_ages():
    """Seconds since each artifact was last written. Absent files are omitted
    rather than reported as infinitely old: a subsystem that was never wired
    is not the same as one that stopped."""
    now = _ex_now()
    out = {}
    for name, fn in _EX_ARTIFACTS:
        try:
            out[name] = now - os.path.getmtime(os.path.join(_EX_STATE, fn))
        except Exception:
            continue
    try:
        out["morph"] = now - os.path.getmtime(_EX_MORPH)
    except Exception:
        pass
    return out


def ex_mood_update(core, signals):
    """Feed one frame to the mood tracker (created lazily, kept across frames)."""
    if _EX_MOOD[0] is None:
        try:
            _EX_MOOD[0] = core.MoodTracker(alpha=0.35, hold=3)
        except Exception:
            return None
    try:
        return _EX_MOOD[0].update(signals)
    except Exception:
        return None


def _ex_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _ex_shift(text, offset=0, bold=False):
    """Wrap text in a drifting 256-colour. No-op when colour is disabled."""
    if not C_RST:
        return text
    idx = _RAINBOW[(_FRAME_NO[0] + int(offset)) % len(_RAINBOW)]
    return ("\x1b[1;38;5;%dm%s" if bold else "\x1b[38;5;%dm%s") % (idx, text) + C_RST


def _ex_status(ok, word, marker=None, tone=None):
    """Coloured, padded status cell.

    Padding is applied to the PLAIN text and only then colourised. Padding a
    colour code instead would pad the escape sequence (zero display width), so
    the alignment would silently depend on how long the ANSI prefix happened
    to be.

    `tone='warn'` colours the cell yellow and changes nothing else. Green and
    red were the only two options, so a state that is neither -- a gate that
    has not been reached yet, an artifact that has never been written -- had to
    be painted as a failure. Yellow is this panel's existing "no verdict"
    colour (see `_ex_mood_look` for UNKNOWN).
    """
    m = marker if marker is not None else (GLYPH["ok"] if ok else GLYPH["bad"])
    body = "%s %-7s" % (m, word)
    if not C_RST:
        return body
    if tone == "warn":
        base = C_YEL
    else:
        base = C_GRN if ok else C_RED
    col = base if (_FRAME_NO[0] % 2 == 0) else (C_BLD + base)
    return col + body + C_RST


def exmachina_snapshot(con=None):
    """Read-only Ex Machina state. Never raises: the wallboard must keep
    painting even when a subsystem is absent or mid-write.

    Falls back to its own read-only connection when the caller has none, so
    the panel stays a one-line change at the render site.
    """
    s = {"oracle": None, "gate": None, "morph": None, "circ": None,
         "cranium": None, "speculum": None, "testudo": None,
         "report": None, "readings": {}, "operators": {}, "summary": {},
         "signals": {}, "severity": {}, "mood": None, "vitals": {},
         "tensor": None, "spec": None, "mutagen": None,
         "ages": ex_ages(), "error": None}
    try:
        p = os.path.join(_EX_STATE, "fitness_oracle.json")
        oj = _ex_json(p)
        if oj:
            s["oracle"] = {
                "rows": oj.get("n_rows"),
                "eras": len(oj.get("eras") or []),
                "models": len(oj.get("models") or []),
                "loss": oj.get("loss_mean"),
                "kb": round((os.path.getsize(p) or 0) / 1024.0),
            }
        s["gate"] = _ex_json(os.path.join(_EX_STATE, "fitness_oracle.last.json"))
    except Exception:
        pass

    # The three artifacts MUTAGEN fuses: the mutation ledger that decides the
    # genome's shape, the genome's per-gene usefulness, and the test suite's own
    # mutational evidence. Read ONCE here and reused below, so the panel and the
    # organ cannot disagree about which rows they are describing.
    mrows_all, genome_a, mutation_a = [], None, None
    try:
        if os.path.exists(_EX_MORPH):
            with open(_EX_MORPH, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        mrows_all.append(json.loads(line))
                    except Exception:
                        continue
        genome_a = _ex_json(os.path.join(_EX_STATE, "tensor_genome.json"))
        mutation_a = _ex_json(os.path.join(_EX_STATE, "mutation_summary.json"))
    except Exception:
        pass

    # ── ONE pass over the outcome rows: every organ, one verdict ─────
    #
    # This replaced ~90 lines of duplicated SPECULUM/TESTUDO logic. The copy
    # had already drifted: the wallboard hardcoded the arm label "cma" while
    # the arm alternated cma/de, so half the duels were mislabelled and
    # invisible. There is now one implementation, in the tier itself.
    core = ex_core()
    if core is None:
        s["error"] = "epistemic tier not loadable: %s" % ("; ".join(_EX_LOAD_ERR) or "unknown")
    else:
        rows = []
        own = con is None
        if con is None:
            con = db_connect()
        try:
            if con is not None:
                rows = latest(con, "evolution_result", 120)
        except Exception as exc:
            s["error"] = "db read: %s: %s" % (type(exc).__name__, exc)
        finally:
            if own and con is not None:
                try:
                    con.close()
                except Exception:
                    pass
        if rows:
            try:
                rep = core.epistemic_report(rows, ages=s["ages"],
                                            morph_rows=mrows_all,
                                            genome=genome_a, mutation=mutation_a)
            except Exception as exc:
                rep = None
                s["error"] = "report: %s: %s" % (type(exc).__name__, exc)
            if rep:
                s["report"] = rep
                s["readings"] = rep.get("readings") or {}
                s["operators"] = rep.get("operators") or {}
                s["summary"] = rep.get("summary") or {}
                s["signals"] = rep.get("signals") or {}
                s["severity"] = rep.get("severity") or {}
                s["vitals"] = rep.get("vitals") or {}
                s["speculum"] = s["readings"].get("speculum")
                s["testudo"] = s["readings"].get("testudo")
                s["mutagen"] = s["readings"].get("mutagen")
                s["mood"] = ex_mood_update(core, s["signals"])

        # Cranium's verdict rides on the newest row that carries one. It is an
        # EVENT (rescue / veto / agree / noop) rather than a gauge, and it is
        # only produced when the margin gate passes - so an absent or stale
        # reading is the honest answer, not a bug to paper over.
        for _r in (rows or []):
            _d = _r.get("data") if isinstance(_r, dict) else None
            if isinstance(_d, str):
                try:
                    _d = json.loads(_d)
                except Exception:
                    continue
            if isinstance(_d, dict) and _d.get("cranium"):
                s["cranium"] = _d["cranium"]
                break

    try:
        # Reuse the single read taken above rather than opening the ledger a
        # second time: two readers of one file is how a panel ends up disagreeing
        # with itself about which cycles it is describing.
        mr = mrows_all
        if mr:
            by = {}
            for r in mr:
                if not isinstance(r, dict) or r.get("fitness") is None:
                    continue
                try:
                    d0 = int(r.get("dimension", 0))
                    by[d0] = max(by.get(d0, float("-inf")), float(r["fitness"]))
                except (TypeError, ValueError):
                    continue
            # `best4` was the original comparison, but after the seed-strip fix
            # dimension-4 rows no longer occur, so it is permanently None and
            # the row read 'best4=None' forever. `morph_record` already stamps
            # `baseline_fitness` on every row, so the honest readout is the
            # trial dimension's best against the baseline it was measured
            # against - which is also the only comparison that means anything
            # once the genome can grow.
            base, trial = [], []
            for r in mr:
                try:
                    bf = r.get("baseline_fitness")
                    if bf is not None:
                        base.append(float(bf))
                    if int(r.get("dimension", 0)) > 4 and r.get("fitness") is not None:
                        trial.append(float(r["fitness"]))
                except (TypeError, ValueError):
                    continue
            s["morph"] = {
                "n": len(mr), "dims": sorted(by),
                "best": {str(k): round(v, 3) for k, v in sorted(by.items())},
                "trial_best": round(max(trial), 3) if trial else None,
                "base_mean": (round(sum(base) / len(base), 3) if base else None),
            }
    except Exception:
        pass

    try:
        al = _ex_json(os.path.join(_EX_STATE, "proteus_circulation.json"))
        if al:
            sv, fv = al.get("s") or {}, al.get("f") or {}
            w = {}
            for arm in (al.get("arms") or []):
                a, b = float(sv.get(arm, 1.0)), float(fv.get(arm, 1.0))
                w[arm] = round(a / (a + b), 2) if (a + b) else 0.0
            s["circ"] = w
    except Exception:
        pass

    # Tensor genome: genes as typed tensors that carry their OWN measured
    # usefulness, plus the model shape they compile to. Absent means the cycle
    # has not run with the flag on yet, which is reported as absent rather than
    # as an error -- a subsystem never wired is not one that stopped.
    try:
        tg = _ex_json(os.path.join(_EX_STATE, "tensor_genome.last.json"))
        if tg:
            s["tensor"] = {
                "genes": tg.get("genes") or [],
                "n": tg.get("n_genes"),
                "width": tg.get("width"),
                "identifiable": tg.get("identifiable"),
                "above_floor": tg.get("above_floor"),
                "most": tg.get("most_useful"),
                "rows": tg.get("rows"),
                "dirs": tg.get("direction_counts") or {},
            }
        sp = _ex_json(os.path.join(_EX_STATE, "tensor_genome.spec.json"))
        if sp:
            s["spec"] = {
                "in_width": (sp.get("input") or {}).get("width"),
                "hidden": sp.get("hidden"),
                "below_floor": sp.get("below_floor") or [],
                "trained": sp.get("trained"),
            }
    except Exception:
        pass
    return s


def _ex_bar(frac, cells=12, fill="#", empty="-"):
    f = 0.0 if frac is None else min(1.0, max(0.0, float(frac)))
    n = int(round(f * cells))
    return "[" + fill * n + empty * (cells - n) + "]"


def _ex_age(secs):
    """Compact age: 9s, 4m, 3h, 2d. Unreadable age is '--', never 0."""
    try:
        s = float(secs)
    except (TypeError, ValueError):
        return "--"
    if s < 0:
        return "--"
    if s < 60:
        return "%ds" % int(s)
    if s < 3600:
        return "%dm" % int(s // 60)
    if s < 86400:
        return "%dh" % int(s // 3600)
    return "%dd" % int(s // 86400)


def _ex_beat(core):
    """One beat per refresh, derived from the CLOCK rather than a counter, so
    the pulse stays honest across restarts."""
    try:
        hb = core.heartbeat(period=max(1.0, float(REFRESH)))
    except Exception:
        return GLYPH["beat"], 0.0
    return (GLYPH["beat"] if hb["beat"] >= 0.5 else GLYPH["vital"]), float(hb["beat"])


def _ex_cell(name, offset, ok, word, detail):
    """One organ cell: name, verdict, and its own numbers."""
    return "%s %s %s" % (_ex_shift("%-9s" % name, offset), _ex_status(ok, word), detail)


def _ex_mood_look(band):
    """(colour, glyph, word) for a mood band; a missing band reads UNKNOWN,
    not serene."""
    if isinstance(band, int) and 0 <= band < len(_EX_BANDS):
        col, gkey, word = _EX_BANDS[band]
        return col, GLYPH.get(gkey, GLYPH["warn"]), word
    return C_YEL, GLYPH["warn"], "UNKNOWN"


def exmachina_lines(width, height, snap):
    """The Ex Machina wallboard: mood, vitals, severity meters, all 16 organs.

    Height-gated rather than fixed: a short window drops the deepest rows
    instead of scrolling, which preserves the monitor's never-wrap /
    never-scroll contract. Every line is `_fit`-ed for the same reason.
    """
    out = []
    if height < 22:
        return out
    snap = snap or {}
    core = ex_core()
    readings = snap.get("readings") or {}
    operators = snap.get("operators") or {}
    frame = snap.get("mood") or {}
    sev = snap.get("severity") or {}
    sm = snap.get("summary") or {}
    n_live = sum(1 for _, t, _ in EX_ORGANS if t == "live")
    n_op = sum(1 for _, t, _ in EX_ORGANS if t == "op")
    band = frame.get("band")
    col, mglyph, word = _ex_mood_look(band)
    score_txt = "--" if frame.get("score") is None else "%.2f" % frame["score"]
    two_up = width >= 104

    # 1. header: the mood is IN the title, not buried in a row
    head = "%s-- EX MACHINA -- %s %s %s | %d judged + %d operators " % (
        (GLYPH["proteus"] + " ") if GLYPH_TIER != "ascii" else "",
        mglyph, word, score_txt, n_live, n_op)
    out.append(_ex_shift(head + "-" * max(0, width - _vis_len(head)), 0, True))

    # 2. alive state: pulse, vitality, belief, frame count
    fil = readings.get("filum") or {}
    vig = readings.get("vigil") or {}
    beat_g, beat = _ex_beat(core) if core is not None else (GLYPH["beat"], 0.0)
    vfrac = 1.0 - (sev.get("score") or 0.0)
    out.append(_fit(" %s %s %s alive %s  %s vitality %s %-9s  %s belief %s bits  cycles %s%s" % (
        mglyph, beat_g, _ex_bar(beat, 4, "=", "-"), _ex_bar(vfrac, 8, "|", "."),
        GLYPH["vital"],
        "--" if fil.get("vitality") is None else "%.2f" % fil["vitality"],
        fil.get("state") or "?",
        GLYPH["oracle"],
        "--" if vig.get("belief_bits") is None else "%+.1f" % vig["belief_bits"],
        sm.get("margins") if sm.get("margins") is not None else 0,
        "" if not frame.get("changed") else "  [mood %s]" % frame.get("direction", "")), width))

    # 3. severity bar + worst signal
    worst = sev.get("worst")
    wname = ""
    if sev.get("signals"):
        wname = max((sev["signals"] or {}).items(), key=lambda kv: kv[1])[0]
    out.append(_fit("%s SEVERITY %s %-5s active %-2s worst %-16s %s%s" % (
        col, _ex_bar(sev.get("score")), score_txt,
        sev.get("active", 0), wname or "--",
        "--" if worst is None else "%.2f" % worst, C_RST), width))

    # 3b. escalation: the severity model's own override, and the fact the
    # average can hide -- `escalated` means ONE signal reached the hard-alarm
    # threshold (0.85, `_HARD_ALARM` in exmachina.py) and forced the mood band
    # up regardless of the mean.
    if height >= 26:
        _hard = sorted((n for n, v in (sev.get("signals") or {}).items()
                        if float(v) >= 0.85),
                       key=lambda n: -float((sev.get("signals") or {})[n]))
        _ecol = C_RED if sev.get("escalated") else C_GRN
        _etxt = ("%s (mood forced up)" % ", ".join(_hard[:3])) if _hard else "not escalated"
        _eraw = sev.get("raw")
        out.append(_fit(" %s %-9s %s%s%s  hard %d/%s  raw %s  worst %s" % (
            GLYPH["bad"] if sev.get("escalated") else GLYPH["ok"],
            _ex_shift("ESCALATE", 4),
            _ecol, _etxt, C_RST,
            len(_hard), sev.get("active", 0),
            "--" if _eraw is None else "%.3f" % _eraw,
            "--" if worst is None else "%.2f" % worst), width))

    # 4. the loudest signals, so the mood can be argued with
    sig = dict(sev.get("signals") or {})
    if sig:
        ranked = sorted(sig.items(), key=lambda kv: -float(kv[1]))
        meter_lines = []
        for name, val in ranked:
            why = dict(_EX_SIGNAL_TEXT).get(name, "")
            # Neutral below the threshold, NOT the success glyph. Every entry
            # here is a severity signal, so a tick would assert the opposite of
            # what the row is showing: 'ok -- mdl -- fitness bought with
            # description length'.
            meter_lines.append(" %s %-15s %s %.2f  %s" % (
                GLYPH["warn"] if val >= 0.6 else GLYPH["neutral"], name,
                _ex_bar(val, 10), float(val), why))
        cap = 2 if height < 28 else (4 if height < 34 else 6)
        for ln in meter_lines[:cap]:
            out.append(_fit(ln, width))

    # 4b. the tier's own promotion funnel. `summary` counts only the rows the
    # report actually judged, so "attempts -> passes" is the tier's read on how
    # far candidates got, independent of the Azazael wallboard's EVO row.
    if height >= 26 and sm:
        _att, _pas = sm.get("attempts"), sm.get("passes")
        _pro, _cd, _sd = sm.get("promoted"), sm.get("cooldowns"), sm.get("margin_sd")
        _pcol = C_GRN if (_pas or 0) > 0 else C_YEL
        out.append(_fit(" %s %-9s attempts %s  %spasses %s%s  promoted %s  cooldowns %s  margin_sd %s" % (
            GLYPH["warn"], _ex_shift("FUNNEL", 5),
            "--" if _att is None else _att,
            _pcol, "--" if _pas is None else _pas, C_RST,
            "--" if _pro is None else _pro,
            "--" if _cd is None else _cd,
            "--" if _sd is None else "%.3f" % _sd), width))

    # 5. vitals: which artifact has gone quiet
    vs = snap.get("vitals") or {}
    if height >= 26 and vs.get("signals"):
        bits = []
        for name, d in sorted((vs["signals"] or {}).items()):
            fresh = bool(d.get("fresh"))
            bits.append("%s %s%s" % (name, _ex_age(d.get("age")),
                                      "" if fresh else " STALE"))
        out.append(_fit(" %s %-9s %s %s" % (
            GLYPH["vital"], _ex_shift("VITALS", 3),
            _ex_status(not vs.get("stale"), "%d/%d" % (vs.get("fresh", 0), vs.get("n", 0))),
            "  ".join(bits)), width))

    # 5b. per-signal freshness budget: `pct` is how much of each artifact's
    # staleness window has been spent, which LEADS the VITALS row's age/STALE
    # (that only reports the crossing, after the fact). Nearest-to-stale first.
    if height >= 28 and vs.get("signals"):
        _fs = sorted((vs["signals"] or {}).items(),
                     key=lambda kv: -float((kv[1] or {}).get("pct") or 0.0))
        _fp = []
        for _n, _d in _fs:
            _d = _d or {}
            _p = float(_d.get("pct") or 0.0)
            _fcol = C_RED if not _d.get("fresh") else (C_YEL if _p >= 0.5 else C_GRN)
            _fp.append("%s%s %d%%%s%s" % (
                _fcol, _n, int(round(_p * 100)),
                " STALE" if not _d.get("fresh") else "", C_RST))
        out.append(_fit(" %s %-9s %s" % (
            GLYPH["vital"], _ex_shift("FRESH", 9), "  ".join(_fp)), width))

    # 6. the nine judging organs, two per line where there is room
    cells = []
    sp = readings.get("speculum") or {}
    if sp:
        v = str(sp.get("verdict") or "?").upper()
        cells.append(_ex_cell("SPECULUM", 2, v == "OK", v[:7],
                              "gaming %.2f margin %+.2f hold %d/%d" % (
                                  sp.get("gaming_score") or 0.0, sp.get("mean_margin") or 0.0,
                                  sp.get("holdout_passes") or 0, sp.get("holdout_attempts") or 0)))
    td = readings.get("testudo") or {}
    if td:
        cells.append(_ex_cell("TESTUDO", 4, bool(td.get("calibrated")),
                              "calib" if td.get("calibrated") else "UNCAL",
                              "coverage %s vs %s q %s" % (
                                  td.get("achieved"), td.get("target"), td.get("q"))))
    vi = readings.get("vigil") or {}
    if vi:
        cells.append(_ex_cell("VIGIL", 6, bool(vi.get("rejected")),
                              "reject" if vi.get("rejected") else "hold",
                              "wealth %.2f belief %s" % (vi.get("wealth") or 0.0,
                                                          vi.get("belief_bits"))))
    if fil:
        cells.append(_ex_cell("FILUM", 8, not bool(fil.get("degenerate")),
                              fil.get("state") or "?" if (fil.get("state") or "")[:7] else "?",
                              "vit %.2f alpha %s" % (fil.get("vitality") or 0.0, fil.get("alpha"))))
    pe = readings.get("perennis") or {}
    if pe:
        frac = pe.get("invariant_fraction")
        cells.append(_ex_cell("PERENNIS", 10, bool(frac), "inv",
                              "%s of %d predictors stable" % (
                                  "--" if frac is None else "%.2f" % frac,
                                  len(pe.get("features") or {}))))
    cp = readings.get("compages") or {}
    if cp:
        cells.append(_ex_cell("COMPAGES", 12, bool(cp.get("cointegrated")),
                              "coint" if cp.get("cointegrated") else "NO LINK",
                              "t %s crit %s%s" % (cp.get("tstat"), cp.get("crit_5pct"),
                                                  " (flat check)" if cp.get("degenerate") else "")))
    br = readings.get("brevis") or {}
    if br:
        cells.append(_ex_cell("BREVIS", 14, True, "mdl",
                              "genome %s of bits, %s genes" % (
                                  br.get("genome_share"), br.get("n_genes"))))
    em = readings.get("emergon") or {}
    if em:
        cells.append(_ex_cell("EMERGON", 16, bool(em.get("emerges")),
                              "emerges" if em.get("emerges") else "flat",
                              "dEI %s via %s" % (em.get("emergence_bits"), em.get("best_grain"))))
    fl = readings.get("fulcrum") or {}
    cells.append(_ex_cell("FULCRUM", 18, True, "ready" if GLYPH_TIER != "ascii" else "rdy",
                          "NCD on gene dicts; needs behaviour traces"))
    per_line = 2 if two_up else 1
    cap_cells = 2 if height < 30 else (6 if height < 36 else len(cells))
    shown = cells[:cap_cells]
    for i in range(0, len(shown), per_line):
        chunk = shown[i:i + per_line]
        out.append(_fit("  " + ("   " if two_up else "").join(
            c if two_up else c for c in chunk), width))

    # 6b. the evidence the operators consumed, so an "idle" operator can be
    # told apart from an operator whose evidence set was never populated.
    if height >= 30 and operators:
        _ev = "rows %s  complete %s  complete-models %s  holdout %s" % (
            operators.get("n_rows"), operators.get("n_complete"),
            operators.get("n_complete_models"), operators.get("n_holdout"))
        if operators.get("check_key"):
            _ev += "  check=%s" % operators["check_key"]
        out.append(_fit(" %s %-9s %s" % (
            GLYPH["vital"], _ex_shift("EVIDENCE", 7), _ev), width))

    # 7. the seven operators, with their measured numbers
    ops = []
    rad = operators.get("radix") or {}
    if rad.get("nodes") is not None:
        ops.append("RADIX %dn/%de %s" % (rad["nodes"], rad["edges"],
                                         "linked" if rad.get("linked") else "d-sep"))
    cat = operators.get("catena") or {}
    if cat.get("radius") is not None:
        ops.append("CATENA r=%.3f" % cat["radius"])
    nav = operators.get("navis") or {}
    if nav.get("holdout_mae") is not None:
        ops.append("NAVIS mae %.3f" % nav["holdout_mae"])
    nex = operators.get("nexus") or {}
    if nex.get("usage_entropy") is not None:
        ops.append("NEXUS H %.2f/%.2f" % (nex["usage_entropy"], nex.get("max_entropy") or 0.0))
    opu = operators.get("opus") or {}
    if opu.get("holdout_mae") is not None:
        ops.append("OPUS mae %.3f" % opu["holdout_mae"])
    vel = operators.get("vela") or {}
    if vel.get("recall") is not None:
        ops.append("VELA rec %.2f" % vel["recall"])
    ops.append("LIMEN %s" % ("queued" if (operators.get("limen") or {}).get("items") else "idle"))
    if height >= 32 and ops:
        # Greedy wrap. Seven operators do not fit one line at 120 columns,
        # and `_fit` would silently cut the last one -- which was LIMEN, the
        # one organ whose whole output IS the queue.
        head = " %s %-9s %s " % (GLYPH["op"], _ex_shift("OPERATORS", 1),
                                  _ex_status(True, "%d" % n_op))
        pad = " " * _vis_len(head)
        lines, cur = [], ""
        for item in ops:
            cand = (cur + "  " + item) if cur else item
            if cur and _vis_len(head) + _vis_len(cand) > width:
                lines.append(cur)
                cur = item
            else:
                cur = cand
        if cur:
            lines.append(cur)
        for i, ln in enumerate(lines):
            out.append(_fit((head if i == 0 else pad) + ln, width))

    # 8. artifacts that carry real state
    oj = snap.get("oracle")
    if oj and height >= 28:
        out.append(_fit(" %s %-9s %s %s rows  %s eras  %s models  %sKB  loss %s" % (
            GLYPH["oracle"], _ex_shift("ORACLE", 8),
            _ex_status(True, "live" if GLYPH_TIER != "ascii" else "on"),
            oj["rows"], oj["eras"], oj["models"], oj["kb"],
            None if oj["loss"] is None else round(float(oj["loss"]), 3)), width))

    mg = snap.get("morph")
    if mg and height >= 34:
        dim_max = max(mg["dims"]) if mg["dims"] else 4
        tb, bm = mg.get("trial_best"), mg.get("base_mean")
        # Lead with the comparison that actually decided something, and label
        # it with the delta rather than two bare numbers the reader has to
        # subtract. 'no baseline yet' is reported as such instead of as 0.
        if tb is not None and bm is not None:
            cmp_txt = "trial %s vs base %s (%+.3f)" % (tb, bm, tb - bm)
        else:
            cmp_txt = "no baseline recorded yet"
        out.append(_fit(" %s %-9s %s %d gene  dims %s  %s" % (
            GLYPH["morph"], _ex_shift("MORPH", 10),
            _ex_status(dim_max > 4, "grown" if dim_max > 4 else "flat"),
            dim_max, mg["dims"], cmp_txt), width))

    ci = snap.get("circ")
    if height >= 36:
        txt = (" ".join("%s %.2f" % (k, v) for k, v in list(ci.items())[:6])
               if ci else "allocator idle (Thompson floor ready)")
        out.append(_fit(" %s %-9s %s %s" % (
            GLYPH["circ"], _ex_shift("CIRC", 12),
            _ex_status(bool(ci), "live" if ci else "idle"), txt), width))

    cr = snap.get("cranium")
    if height >= 38:
        # Cranium fires only when the margin gate passed, so 'not reached' is
        # the common and correct state. Showing it as 'deferred' with the
        # era count makes the difference between "no evidence" and "no test"
        # legible - the E2 lesson, which was exactly this confusion.
        if cr:
            act = str(cr.get("action") or cr.get("decision") or "?")
            txt = "%s eras %s obs  p %s%s" % (
                cr.get("n_eras", "-"), cr.get("n_obs", "-"),
                cr.get("p_above_threshold", "-"),
                ("  [%s]" % cr.get("deferred")) if cr.get("deferred") else "")
            out.append(_fit(" %s %-9s %s %s" % (
                GLYPH["cranium"], _ex_shift("CRANIUM", 14),
                _ex_status(act in ("rescue", "veto"), act), txt), width))
        else:
            # 'not reached' is the EXPECTED state, as the comment above says, so
            # it renders neutral rather than as a red failure. Painting the
            # normal case as an error trains the reader to ignore the colour.
            out.append(_fit(" %s %-9s %s margin gate has not passed "
                            "(no cross-era test)" % (
                                GLYPH["cranium"], _ex_shift("CRANIUM", 14),
                                _ex_status(False, "not reached",
                                           marker=GLYPH["neutral"],
                                           tone="warn")), width))

    # 9. the tensor genome: the deliverable. Each gene is a typed tensor that
    # carries its own measured usefulness, and the genome compiles to a model
    # shape. The ladder is the point -- it shows WHICH gene pays for its
    # coordinate, and which is only occupying one. 'above floor' is read from
    # the artifact rather than recomputed here, so the panel cannot disagree
    # with the module about what the floor is.
    # Gates are set against MEASURED line usage, not a guess: the panel emits
    # only 16 of 30 lines at the default 100x30 window, so the tensor rows fit
    # the standard window instead of being unreachable until someone maximised
    # the terminal. Progressive disclosure, matching the rows above.
    tg = snap.get("tensor")
    if tg and height >= 28:
        n_g = tg.get("n") or 0
        out.append(_fit(" %s %-9s %s %d genes  width %d  %d/%d above floor  %s rows" % (
            GLYPH["genome"], _ex_shift("TENSOR", 16),
            _ex_status(bool(tg.get("identifiable")), "declared"),
            n_g, tg.get("width") or 0, tg.get("above_floor") or 0, n_g,
            tg.get("rows") or 0), width))
    if tg and height >= 32:
        ladder = sorted(
            [g for g in (tg.get("genes") or []) if isinstance(g, dict)],
            key=lambda g: -abs(float(g.get("rho") or 0.0)))
        for g in ladder[:2]:
            rho = float(g.get("rho") or 0.0)
            # 20 columns, not 14: `use_schema_features` is 19 characters and
            # was being cut to `use_schema_fea`, which is ambiguous with its
            # sibling candidate rather than merely short.
            out.append(_fit("   %s %-20s %s %+0.3f %-5s n=%s" % (
                GLYPH["ok"] if g.get("identifiable") else GLYPH["neutral"],
                str(g.get("name") or "?")[:20], _ex_bar(abs(rho), 8),
                rho, g.get("direction") or "?", g.get("samples")), width))
    if tg and height >= 36:
        sp = snap.get("spec") or {}
        hid = sp.get("hidden") or []
        out.append(_fit("   %s compiles to in=%s hidden=%s  %s" % (
            GLYPH["flat"], sp.get("in_width"),
            ("x".join(str(h) for h in hid) or "--"),
            "trained" if sp.get("trained") else "UNTRAINED spec"), width))

    # 9b. MUTAGEN: the genome's own mutation ledger. Every organ above reads the
    # OUTCOME series or a derived artifact; none reads the file that decides the
    # genome's SHAPE. On this history 75% of the structural trials never changed
    # what the trainer was called with, and every one of them was retained -- so
    # the gain cannot be attributed to the gene. Note "never treated" is not the
    # same as "off": `by_session` DEFAULTS to True in the trainer, so for it the
    # no-op trial is the True one. The tier compares against the trainer's own
    # defaults for exactly that reason, and the row reports which trials
    # actually MOVED the call.
    mg = snap.get("mutagen")
    if mg and height >= 30:
        fg = mg.get("free_growth")
        bad = bool(fg is not None and fg >= 0.35)
        _pv = mg.get("inert_p")
        _pt = "--" if _pv is None else ("<1e-15" if float(_pv) < 1e-15 else "%.0e" % float(_pv))
        out.append(_fit(" %s %-9s %s %s  %2.0f%% inert  %d/%d won  dF %s  P %s" % (
            GLYPH["mutagen"], _ex_shift("MUTAGEN", 14),
            _ex_status(not bad, str(mg.get("verdict") or "n/a"),
                       marker=(GLYPH["bad"] if bad else GLYPH["ok"]),
                       tone=("warn" if (fg is not None
                                      and 0.15 <= float(fg) < 0.35) else None)),
            _ex_bar(fg or 0.0, 8), 100.0 * float(mg.get("inert_share") or 0.0),
            mg.get("inert_positive") or 0, mg.get("inert_trials") or 0,
            ("--" if mg.get("inert_gain") is None else "%+0.2f" % float(mg["inert_gain"])),
            _pt), width))

    # 9c. MUTAGEN per-gene: for EVERY candidate gene, how many trials actually
    # MOVED the trainer call versus how many left it at the trainer's default.
    # Gated at 30, not 34: at 34 it was invisible in the default 100x30 window,
    # which is the same "component exists, is correct, and is never seen"
    # failure the gate below now tests for rather than assumes.
    # That split is the whole attribution question on one line -- a gene with
    # 0c/Ni has a null result, not a win -- and when the cycle ran the matched
    # A/B (9d in the cycle) the attributable effect is shown next to it. The
    # genes are printed BEFORE the load/suite tail and the tail is dropped when
    # the window is too narrow, so a narrow window loses the secondary numbers
    # rather than the split itself.
    if mg and height >= 30:
        _gp = []
        for _g in (mg.get("genes") or []):
            _tok = "%s %dc/%di" % (str(_g.get("gene") or "?")[:19],
                                   int(_g.get("changed") or 0),
                                   int(_g.get("inert") or 0))
            if _g.get("never_changed"):
                _tok += " NEVER-TREATED"
            elif _g.get("ab_n"):
                _tok += " ab%+0.2f" % float(_g.get("ab_mean") or 0.0)
            _gp.append(_tok)
        _ld = mg.get("load") or {}
        _ts = mg.get("tests") or {}
        _tail = "load %d/%d" % (len(_ld.get("below_floor") or []), _ld.get("genes") or 0)
        if _ts.get("mutants"):
            _tail += "  suite %s/%s>=%s %sh" % (_ts.get("killed"), _ts.get("mutants"),
                                                _ts.get("lower"), _ts.get("age_h"))
        _head = "   %s %s" % (GLYPH["mutagen"], "  ".join(_gp))
        if width and _vis_len(_head) + len(_tail) + 2 > int(width):
            _row = _head
        else:
            _row = "%s  %s" % (_head, _tail)
        out.append(_fit(_row, width))

    gt = snap.get("gate") or {}
    if height >= 30:
        blocked = gt.get("ok") is False
        gword = "BLOCKED" if blocked else ("pass" if gt.get("ok") else "n/a")
        out.append(_fit(" %s %-9s %s gate=%s era_robust_p=%s" % (
            GLYPH["proteus"], _ex_shift("PROTEUS", 14),
            _ex_status(not blocked, gword,
                       marker=(GLYPH["blocked"] if blocked else GLYPH["ok"])),
            gword, gt.get("era_robust_p")), width))

    if snap.get("error"):
        out.append(_fit(" %s %-9s %s" % (
            GLYPH["bad"], _ex_shift("DEGRADED", 20), str(snap["error"])), width))
    return out



# --- health -----------------------------------------------------------
def health_check():
    """Is the epistemic tier loadable and reporting? Returns (ok, message).

    This viewer is useless if the tier it views cannot be imported: the panel
    then degrades to a single DEGRADED row, which is easy to mistake for a
    quiet subsystem. Automation needs an explicit yes/no, so `--health`
    exits non-zero instead of relying on someone reading the panel.
    """
    core = ex_core()
    if core is None:
        return False, "epistemic tier not loadable: %s" % (
            "; ".join(_EX_LOAD_ERR) or "unknown")
    snap = exmachina_snapshot()
    if snap.get("error"):
        return False, str(snap["error"])
    return True, "tier loaded: %d organs, %d signals" % (
        len(EX_ORGANS), len(snap.get("signals") or {}))


# --- rendering --------------------------------------------------------
def _control_line(snap):
    """Expose the epistemic evidence boundary before organ-level detail.

    Banner contract, shared with the Azazael and AOA wallboards: the evidence
    summary is always printed (or explicitly `artifact evidence unknown`), and
    `next=` is the OPERATOR ACTION slot, never the name of the next scheduled
    job -- the reader is looking at a STALE banner to learn what to DO.
    """
    ages = dict(snap.get("ages") or {})
    known = sorted(ages.items(), key=lambda item: item[1])
    stale = [name for name, age in known if age > 3600]
    if not known:
        state, colour = "UNKNOWN", C_YEL
        evidence = "artifact evidence unknown"
    elif stale:
        state, colour = "STALE", C_RED
        evidence = f"{len(stale)}/{len(known)} artifacts older than 1h"
    else:
        state, colour = "LIVE", C_GRN
        evidence = f"{len(known)} artifacts current"
    action = ("refresh evidence producers; viewer is read-only" if stale
              else "continue observing evidence")
    error = snap.get("error")
    if error:
        action = f"collector error: {error}"
    newest = f" newest={known[0][1] / 60:.0f}m" if known else ""
    return f"{C_BLD}CONTROL {colour}{state}{C_RST}  {evidence}{newest}  next={action}"


def render(now=None, size=None):
    """One frame: header + the Ex Machina panel. Never raises.

    A column is left free at the right edge and three rows at the bottom. A
    line that fills the window EXACTLY can wrap on a console that wraps on the
    last cell, and one wrapped line shifts -- then scrolls -- the whole frame;
    that is what "it does not refresh in place" looks like. The panel is told
    the reduced height so it never has to be cut off at the bottom either, and
    more rows can be added without re-tuning this budget.
    """
    cols, rows = size if size else shutil.get_terminal_size((110, 40))
    width = max(64, min(cols - 1, 200))  # keep the last column unused
    height = max(8, rows - 3)            # 2 banner rows + 1 margin
    _FRAME_NO[0] += 1                    # twinkle step for the drifting hues
    lines = []
    L = lines.append
    now = now or datetime.fromtimestamp(_ex_now())
    L("=" * width)
    L(_fit("  EX MACHINA LIVE MONITOR   %s   (refresh %ds, Ctrl+C to stop)" % (
        now.strftime("%Y-%m-%d %H:%M:%S"), REFRESH), width))
    L("=" * width)
    try:
        snap = exmachina_snapshot()
        L(_fit(_control_line(snap), width))
        lines.extend(exmachina_lines(width, height, snap))
    except Exception as exc:             # a dead panel must still say why
        L(_fit(" panel error: %s: %s" % (type(exc).__name__, exc), width))
    return "\n".join(lines)


def _paint_in_place(frame_lines):
    """Redraw the dashboard in place: no full-screen clear, no scrolling.

    Moves the cursor home, overwrites each row (erasing to end-of-line so a
    shorter row never leaves stale text), then erases only the rows below the
    frame. Never emits a trailing newline past the bottom row, so the console
    never scrolls -- everything stays on one screen and updates in place.
    """
    size = _sh.get_terminal_size((100, 30))
    # FULL window height: render() already leaves the last column free, so a
    # line cannot wrap and the last row is safe to use. Reserving it just made
    # the rows reserved by render() pointless.
    #
    # 2026-09-14: this read `max_rows`, which is defined NOWHERE else in the
    # file -- so every live paint raised NameError and the window crash-looped
    # (the launcher retried every 10s, nothing was ever drawn, and because the
    # panel is painted only here, `--once` never exercised it and shipped the
    # break). The comment above asks for the full window height, which is
    # size.lines.
    max_rows = max(1, size.lines)
    rows = frame_lines[:max_rows]          # never exceed the visible window
    if len(rows) < max_rows:
        rows = rows + [""] * (max_rows - len(rows))  # wipe any longer old frame

    # Reset first: without it the frame starts in whatever colour the previous
    # one ended in, so the banner changes colour on the second paint.
    out = ["\033[H", C_RST]                # home, then clear any inherited SGR
    for i, line in enumerate(rows):
        if i:
            out.append("\r\n")
        out.append(line)
        out.append(C_RST)                  # never let one row's colour bleed
        out.append("\033[K")               # erase to end of this line
    out.append("\033[J")                   # erase everything below the frame
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def _strip_ansi(text):
    """Drop SGR sequences so the log stays greppable."""
    global _ANSI_RE
    if _ANSI_RE is None:
        _ANSI_RE = re.compile("\x1b\\[[0-9;]*m")
    return _ANSI_RE.sub("", text)


def append_log(frame_text, now):
    """Append one rendered frame to the rolling log (rotate at 1 MB).

    2026-09-14: LOG_PATH and the --log flag have existed since this viewer was
    written, but nothing ever wrote them -- so this was the only one of the four
    wallboards with no on-disk record, a crash in its window left no trace, and
    --log silently did nothing. The frame is logged verbatim (ANSI stripped)
    rather than as a hand-built digest, so the record cannot drift from what the
    screen actually showed.
    """
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX:
            bak = LOG_PATH + ".1"
            if os.path.exists(bak):
                os.remove(bak)
            os.replace(LOG_PATH, bak)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("=== %s ===\n" % now.isoformat())
            for line in _strip_ansi(frame_text).split("\n"):
                f.write(line.rstrip() + "\n")
    except OSError:
        pass  # logging must never take down the wallboard


def _pid_alive(pid):
    """Windows liveness check (2026-09-09 fix): os.kill(pid, 0) is not
    supported on Windows (WinError 87) — it always raised, so every guard
    using it never blocked. OpenProcess is the correct probe."""
    try:
        import ctypes
        _SYNCHRONIZE = 0x00100000
        h = ctypes.windll.kernel32.OpenProcess(_SYNCHRONIZE, False, int(pid))
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        return False


def _single_instance():
    """Exit if another live monitor is already running (2026-09-09).

    Two painters would fight over consoles and double every log line.
    Stale PID files are stolen (crash-safe), like dream_cycle's guard.
    """
    lock = os.path.join(tempfile.gettempdir(), "exmachina_live_monitor.lock")
    try:
        with open(lock, encoding="utf-8") as f:
            old = int((f.read() or "0").strip() or 0)
        if old and _pid_alive(old):
            print(f"Another live monitor is already running (PID {old}) - not starting a second one.",
                  file=sys.stderr)
            return False
    except (OSError, ValueError):
        pass
    try:
        with open(lock, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    return True




def main():
    global REFRESH, LOG_PATH
    ap = argparse.ArgumentParser(description="Standalone Ex Machina live monitor")
    ap.add_argument("--refresh", type=int, default=30)
    ap.add_argument("--log", default=LOG_PATH)
    ap.add_argument("--once", action="store_true",
                    help="render a single frame to stdout and exit (screenshots/reports)")
    ap.add_argument("--no-color", action="store_true",
                    help="strip ANSI colors (pair with --once for clean captures)")
    ap.add_argument("--health", action="store_true",
                    help="check that the epistemic tier loads, then exit (0 ok / 1 degraded)")
    args = ap.parse_args()
    REFRESH = max(5, args.refresh)
    LOG_PATH = os.path.abspath(args.log)
    if args.no_color:
        global C_RED, C_GRN, C_YEL, C_CYN, C_BLD, C_ORG, C_RST
        C_RED = C_GRN = C_YEL = C_CYN = C_BLD = C_ORG = C_RST = ""

    # resolve the glyph tier once, from what this console can actually take
    try:
        set_glyphs("auto")
    except Exception:
        pass

    if args.health:
        ok, msg = health_check()
        print(("OK   " if ok else "FAIL ") + msg)
        return 0 if ok else 1

    if args.once:
        # read-only snapshot: bypass the single-instance lock so a capture
        # never fights the running wallboard.
        print(render())
        return 0

    if not _single_instance():
        return 2

    # alert once if the tier is not loadable, so a silent blank panel is never
    # mistaken for a healthy idle subsystem
    _ok, _msg = health_check()
    if not _ok:
        sys.stderr.write("\a[EX MACHINA] %s\n" % _msg)

    first = True
    while True:
        text = render()
        if first:
            sys.stdout.write("\033[2J\033[H")   # clear once on startup
            first = False
        _paint_in_place(text.split("\n"))
        append_log(text, datetime.now())   # --log finally writes (2026-09-14)
        time.sleep(REFRESH)


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        sys.exit(0)
