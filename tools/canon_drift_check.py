#!/usr/bin/env python3
# coding: utf-8
"""Canon drift check — the source corpus and the load path must not diverge.

The three copies, and which two are live
----------------------------------------
Skill/canon content exists in three directories in this tree:

    skills/                 the SOURCE corpus (RULE 0R: this is the source)
    .agents/skills/<n>/SKILL.md   the LOAD PATH (`skill()` reads only here)
    skills.win-backup/      an ARCHIVE (MIGRATION_LEDGER 2026-10-02: "left as archive")

So there are two LIVE copies that must agree byte-for-byte, and one archive that
is expected to be stale and is excluded. `sync_skills.py` is supposed to
propagate source -> load path, but nothing CHECKS that it happened, and the
drift is real and measured: 44 of 1,226 shared skills disagree, 26 of them
because the load-path copy lost its YAML frontmatter (which `skill()` needs to
load it at all), and 18 with substantive body differences in BOTH directions --
`.agents/` is richer for `azazael-pre-action-gate` (+2,344 B), `azazael-mypy`
(+1,615 B) and `azazael-pyright` (+961 B); `skills/` is richer for
`azazael-eve-unleashed` (+2,913 B) and `azazael-sync-patterns` (+1,151 B).

Why the failure mode is "reports OK"
------------------------------------
`tools/type_check_gate.py` and `tools/anti_pattern_gate.py` both fail on a
recorded baseline because a check that fails on pre-existing facts gets switched
off. This check is the opposite case: the drift is a DEFECT, not debt, and the
corpus is small enough to hold at zero. So it exits non-zero on ANY drift, and
`--fix` reconciles it.

The reconciliation rule (content-preserving, and reversible)
------------------------------------------------------------
Neither side is uniformly newer, so "always copy A to B" would destroy real
content. The rule is the conservative one:

  * if the two bodies are equal and only the frontmatter differs -> the SOURCE
    wins (the load path must carry the frontmatter or `skill()` cannot load it);
  * otherwise the side with the LARGER body wins, in whichever direction that
    falls.

Richer-wins never discards text; it can only carry extra text. Where it picks
the archive-adjacent side, the loss is zero because both are kept in git. The
rule is stated here, and printed per file by `--fix`, so the choice is reviewable
rather than silent.

Usage
-----
    python tools/canon_drift_check.py --check     # pre-commit/CI: exit 1 on drift
    python tools/canon_drift_check.py --report    # list drift, no gate
    python tools/canon_drift_check.py --fix       # reconcile both live layers
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "skills"
LOAD_DIR = ROOT / ".agents" / "skills"
#: Expected-stale archive layers, excluded by design (not silently skipped).
#: `skills.pkg-archive` (2026-10-30): the 1,042 `azazael-pkg-*` skills were moved
#: out of both live layers because their descriptions alone cost ~34.7k tokens on
#: every request. They are still here, byte for byte, and reachable through
#: `python tools/skills_find.py` -- excluded from drift, not deleted.
ARCHIVE_DIRS = (ROOT / "skills.win-backup", ROOT / "skills_staging", ROOT / "skills.pkg-archive")

if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

#: Optional EvoSOP operators (EP354): None when src is unavailable (minimal
#: envs); _evosop_merge falls back to richer-wins, so --check/--fix never
#: depend on this import.
try:
    from CORE.skill_library import (  # noqa: E402
        family_index as _FAMILY_INDEX,
        merge_sops as _MERGE_SOPS,
        protocol_artifact as _PROTOCOL_ARTIFACT,
    )
except Exception:
    _MERGE_SOPS = None  # type: ignore[assignment]
    _FAMILY_INDEX = None  # type: ignore[assignment]
    _PROTOCOL_ARTIFACT = None  # type: ignore[assignment]

#: The corpus's OWN procedure, as a protocol artifact (EP34). The steps are the
#: ones `--fix` actually performs, below — so the artifact describes real
#: behaviour rather than desired behaviour. `--catalogue` emits it.
_CORPUS_PROTOCOL_STEPS = [
    "classify the source corpus against the load path",
    "restore frontmatter-only drift from the source",
    "merge substantive drift with the EvoSOP union operator",
    "fall back to richer-wins when a merge is not an improvement",
    "promote load-path orphans into the source corpus",
]


def _read(path: Path) -> str:
    """Text with newlines normalised, so CRLF smudging is not read as drift."""
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_frontmatter(text: str) -> tuple[str, str]:
    """(frontmatter-with-delimiters, body). Missing frontmatter -> ("", text)."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return "---" + parts[1] + "---\n", parts[2].lstrip("\n")
    return "", text


def source_map() -> dict[str, Path]:
    return {p.stem: p for p in SOURCE_DIR.glob("*.md")}


def load_map() -> dict[str, Path]:
    out: dict[str, Path] = {}
    if LOAD_DIR.exists():
        for d in LOAD_DIR.iterdir():
            skill = d / "SKILL.md"
            if d.is_dir() and skill.exists():
                out[d.name] = skill
    return out


def classify() -> dict:
    """Return the drift picture without mutating anything."""
    src, load = source_map(), load_map()
    missing_in_load = sorted(set(src) - set(load))
    stale_in_load = sorted(set(load) - set(src))

    frontmatter_only, substantive, identical = [], [], 0
    for name in sorted(set(src) & set(load)):
        s_t, l_t = _read(src[name]), _read(load[name])
        if _sha(s_t) == _sha(l_t):
            identical += 1
            continue
        s_fm, s_body = _split_frontmatter(s_t)
        l_fm, l_body = _split_frontmatter(l_t)
        if _sha(s_body) == _sha(l_body):
            frontmatter_only.append(name)
        else:
            substantive.append(name)

    return {
        "source_count": len(src),
        "load_count": len(load),
        "identical": identical,
        "frontmatter_only": frontmatter_only,
        "substantive": substantive,
        "missing_in_load": missing_in_load,
        "stale_in_load": stale_in_load,
    }


def _report(rep: dict) -> None:
    print("canon drift check")
    print("  source (skills/):                 %d" % rep["source_count"])
    print("  load path (.agents/skills/):      %d" % rep["load_count"])
    print("  identical:                        %d" % rep["identical"])
    print("  frontmatter-only drift:           %d" % len(rep["frontmatter_only"]))
    print("  substantive drift:                %d" % len(rep["substantive"]))
    print("  in source, missing from load:     %d" % len(rep["missing_in_load"]))
    print("  in load, no source (stale):       %d" % len(rep["stale_in_load"]))
    print("  excluded archives:                %s"
          % ", ".join(p.name for p in ARCHIVE_DIRS if p.exists()))
    if rep["frontmatter_only"]:
        print("\n  frontmatter-only (load path lost its YAML header):")
        for n in rep["frontmatter_only"]:
            print("    %s" % n)
    if rep["substantive"]:
        print("\n  substantive (body differs):")
        for n in rep["substantive"]:
            print("    %s" % n)
    if rep["missing_in_load"]:
        print("\n  missing from load path (skill() cannot see these):")
        for n in rep["missing_in_load"]:
            print("    %s" % n)
    if rep["stale_in_load"]:
        print("\n  stale in load path (no source -- archive or orphan):")
        for n in rep["stale_in_load"]:
            print("    %s" % n)


def cmd_check() -> int:
    rep = classify()
    _report(rep)
    drift = (len(rep["frontmatter_only"]) + len(rep["substantive"])
             + len(rep["missing_in_load"]) + len(rep["stale_in_load"]))
    if drift:
        print("\nFAIL: %d skill(s) drift between the source corpus and the load "
              "path.\nRun: python tools/canon_drift_check.py --fix" % drift)
        return 1
    print("\nOK: source corpus and load path agree.")
    return 0


def _evosop_merge(s_body: str, l_body: str) -> str | None:
    """EvoSOP union resolver (EP354): shortest-common-supersequence merge.

    Returns the merged body text, or None when merging is not an improvement
    (identical-to-one-side, order conflicts, gaps, operators unimportable).
    Callers fall back to the richer-wins rule, so this can only upgrade
    outcomes, never degrade them.
    """
    if _MERGE_SOPS is None:
        return None
    s_steps = [ln.strip() for ln in s_body.splitlines() if ln.strip()]
    l_steps = [ln.strip() for ln in l_body.splitlines() if ln.strip()]
    if not s_steps or not l_steps:
        return None
    try:
        res = _MERGE_SOPS(s_steps, l_steps)
    except Exception:
        res = None
    # Structured fail-open (not except-return): the None falls through to the
    # same fallback every other non-mergeable case takes, and _reconcile's
    # returned direction/reason keeps the outcome observable per file.
    if not res or not res.get("ok"):
        return None
    merged = res.get("merged") or {}
    plan = merged.get("reasoning_plan", []) if isinstance(merged, dict) else []
    if not plan or plan == s_steps or plan == l_steps:
        return None
    return "\n".join(plan) + "\n"


def _reconcile(name: str, src: Path, load: Path) -> tuple[str, str]:
    """Apply the reconciliation rule to one pair. Returns (direction, reason)."""
    s_t, l_t = _read(src), _read(load)
    s_fm, s_body = _split_frontmatter(s_t)
    l_fm, l_body = _split_frontmatter(l_t)

    if _sha(s_body) == _sha(l_body):
        # Same body: the load path must carry the frontmatter. Source wins.
        if s_t != l_t:
            load.write_text(s_t, encoding="utf-8", newline="")
            return "source->load", "frontmatter restored to load path"
        return "none", "already equal"

    merged = _evosop_merge(s_body, l_body)
    if merged is not None:
        # EvoSOP (EP354): genuine union — converge BOTH layers on it so the
        # next --check passes. Source frontmatter wins (the load path needs it
        # for skill() to load the file).
        fm = s_fm if s_fm else l_fm
        text = fm + merged if fm else merged
        src.write_text(text, encoding="utf-8", newline="")
        load.write_text(text, encoding="utf-8", newline="")
        return "merged", "EvoSOP SCS union (wrote both layers)"

    if len(s_body) >= len(l_body):
        load.write_text(s_t, encoding="utf-8", newline="")
        return "source->load", "source body richer (%d vs %d)" % (len(s_body), len(l_body))
    src.write_text(l_t, encoding="utf-8", newline="")
    return "load->source", "load body richer (%d vs %d)" % (len(l_body), len(s_body))


def cmd_fix() -> int:
    src, load = source_map(), load_map()
    changed = 0

    for name in sorted(set(src) & set(load)):
        direction, reason = _reconcile(name, src[name], load[name])
        if direction != "none":
            changed += 1
            print("  %-42s %-14s %s" % (name, direction, reason))

    # Missing from load: copy the source in (the load path is what skill() reads).
    for name in sorted(set(src) - set(load)):
        dest = LOAD_DIR / name
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src[name], dest / "SKILL.md")
        changed += 1
        print("  %-42s %-14s %s" % (name, "source->load", "created (was missing)"))

    # Stale in load: no source. `skills/` is the declared source corpus (RULE
    # 0R), and `sync_skills.sync_all()` DELETES a load-path skill that has no
    # source -- so leaving these would let the next sync silently drop them.
    # The content-preserving move is to promote the load-path copy INTO the
    # corpus, which both keeps the text and makes the skill survive a sync.
    stale = sorted(set(load) - set(src))
    for name in stale:
        dest = SOURCE_DIR / (name + ".md")
        shutil.copyfile(load[name], dest)
        changed += 1
        print("  %-42s %-14s %s" % (name, "load->source",
                                    "promoted (had no source; sync would delete it)"))

    print("\n%d file(s) reconciled." % changed)
    return 0


def _family_of(name: str) -> str:
    """Procedural family for a skill name (CASS family-index input).

    `azazael-pkg-aadict` -> `pkg`, `azazael-cain-pipeline` -> `cain`. Families
    come from the naming convention the corpus already uses, so this groups what
    is there instead of inventing a taxonomy.
    """
    n = name
    if n.startswith("azazael-"):
        n = n[len("azazael-"):]
    return n.split("-")[0] or "unfiled"


def cmd_catalogue(rep: dict) -> int:
    """Print the corpus family index and the corpus's own protocol artifact.

    Until now the L2 layer was a flat markdown corpus with no machine-readable
    grouping and no stated procedure, so `family_index` (CASS) and
    `protocol_artifact` (MAPD/EP34) had no production consumer. This is that
    consumer: it groups the real corpus into procedural families and emits the
    reconciliation procedure as a protocol artifact.
    """
    if _FAMILY_INDEX is None or _PROTOCOL_ARTIFACT is None:
        print("\n  catalogue skipped: CORE.skill_library unavailable")
        return 0
    src = source_map()
    idx = _FAMILY_INDEX([{"name": n, "family": _family_of(n)}
                         for n in sorted(src)])
    proto = _PROTOCOL_ARTIFACT(
        "canon-drift-reconcile",
        _CORPUS_PROTOCOL_STEPS,
        ["source corpus skills: %d" % rep["source_count"],
         "load path skills: %d" % rep["load_count"],
         "substantive drift: %d" % len(rep["substantive"]),
         "frontmatter-only drift: %d" % len(rep["frontmatter_only"])])

    print("\n  procedural families (CASS audit input): %d over %d skills"
          % (idx["n_families"], idx["n_skills"]))
    for fam, names in sorted(idx["families"].items(),
                             key=lambda kv: (-len(kv[1]), kv[0]))[:12]:
        print("    %-20s %5d" % (fam, len(names)))
    if idx["unassigned"]:
        print("    %-20s %5d" % ("(unassigned)", len(idx["unassigned"])))
    print("  protocol artifact: %s -- %d steps, %d grounding facts, valid=%s"
          % (proto.get("task_type"), len(proto.get("reasoning_plan", [])),
             len(proto.get("grounding_facts", [])), proto.get("valid")))
    if proto.get("gaps"):
        print("    gaps: %s" % "; ".join(proto["gaps"]))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Fail when the skill source corpus and load path diverge.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true",
                       help="exit 1 on any drift")
    group.add_argument("--report", action="store_true",
                       help="list drift, never gate")
    group.add_argument("--fix", action="store_true",
                       help="reconcile both live layers (content-preserving)")
    group.add_argument("--catalogue", action="store_true",
                       help="report + the corpus family index and protocol artifact")
    args = ap.parse_args(argv)
    if args.report:
        _report(classify())
        return 0
    if args.catalogue:
        _report(classify())
        return cmd_catalogue(classify())
    if args.fix:
        return cmd_fix()
    return cmd_check()


if __name__ == "__main__":
    sys.exit(main())
