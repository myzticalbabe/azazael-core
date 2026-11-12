#!/usr/bin/env python3
"""Append this lane's harvest rows to the Kilo lane file.

Rows are written as the errors are encountered; this script is the mechanical
writer so the JSONL stays valid. Appends (never truncates) so an earlier
session's rows on the same local date survive.
"""
import io
import json
import os

LANE = r"C:\Azazael\azazael\state\bake_harvest_kilo_2026-10-21.jsonl"
SOURCE = "kilo-typecheck-2026-10-21"

ROWS = [
    {
        "item_id": "k-01",
        "kind": "error_fix",
        "title": "Nine CODEX/VIDEOS scripts carry 'from pathlib import Path' INSIDE the module docstring",
        "body": "The nine CODEX/VIDEOS scrolling-video scripts (create_scrolling_video, make_video, make_video_quad, make_video_vibrant, make_video_zip, make_video_zipper, make_video_docs, make_video_v2, make_video_v3) carry 'from pathlib import Path' on line 4, inside the module docstring, so the import never executes. The module-level 'OUTPUT_DIR = Path.home() / \"Desktop\" / \"CODEX\"' then raises NameError: name 'Path' is not defined AT IMPORT TIME. Proof: importing the HEAD version of all nine raises NameError; after the repair seven import cleanly and make_video_v3 is the ninth, while create_scrolling_video keeps a separate generated-template defect (row k-11). This is the same class as harvest row g1613-04, which found one docstring-swallowed import in VIDEO_ANALYZER_WORM: the pattern recurs across a whole directory, not a single file, so a per-file check will keep finding them one at a time.",
    },
    {
        "item_id": "k-02",
        "kind": "error_fix",
        "title": "create_video.py called Image.new and ImageDraw.Draw with no PIL import anywhere in the module",
        "body": "src/CODEX/VIDEOS/create_video.py create_frame() called 'Image.new(\"RGB\", resolution, ...)' and 'ImageDraw.Draw(img)' while the module imported only os, subprocess, numpy and typing. There was no PIL import at module scope and none at the point of use, so both names were unbound and create_frame() raised NameError on every call. Fixed with a module-level 'from PIL import Image, ImageDraw', matching the eight sibling scripts in the same directory and the pillow==12.3.0 pin in requirements-3.15t.txt.",
    },
    {
        "item_id": "k-03",
        "kind": "error_fix",
        "title": "OMNI_WORM.py had a rbPath typo, and the corrected value was a Path compared against file bytes",
        "body": "src/CORE/WORMS/OMNI_WORM.py L66 read 'pattern = rbPath.home() / \"Desktop\" / \"Azazael_UNIFIED\"'. rbPath is a typo; 'from pathlib import Path' was already imported at L10, so the name was never bound. Fixing the typo alone was not sufficient: pattern would then be a Path, and the next line tests 'pattern in content' where content comes from py_file.read_bytes() and is bytes, so the test raises TypeError (a bytes-like object is required, not 'PosixPath') on the first file scanned. Fixed to 'str(Path.home() / \"Desktop\" / \"Azazael_UNIFIED\").encode(\"utf-8\")' so the membership test is bytes-in-bytes. Lesson: repairing an undefined name can unmask the type error the undefined name was hiding.",
    },
    {
        "item_id": "k-04",
        "kind": "error_fix",
        "title": "REAL_FRAMEWORK_TEST.py: POSIX-only 'import resource' sat outside the try meant to guard it",
        "body": "src/REAL_FRAMEWORK_TEST.py test_system_info() did 'import resource' at function scope and then wrapped only the USE of resource in try/except Exception. resource is POSIX-only, so on Windows the import raised ModuleNotFoundError before the handler was ever reached and the whole method died with the test suite. Found by the behavioural smoke, not by the type checker, which reported no error here at all. Fixed by moving 'import resource' inside the existing try so the established note_failure handler covers the import as well as the use.",
    },
    {
        "item_id": "k-05",
        "kind": "error_fix",
        "title": "enhanced_animations.py: 'import argparse' was written into the docstring's usage example",
        "body": "src/CODEX/CORE/enhanced_animations.py main() built 'argparse.ArgumentParser(...)' but the module never imported argparse; the only 'import argparse' in the file sat on line 15, inside the docstring's 'Usage:' example block, so the CLI raised NameError on every invocation. Fixed by removing the stray line from the example and adding a real module-level 'import argparse' beside 'import json'.",
    },
    {
        "item_id": "k-06",
        "kind": "error_fix",
        "title": "WORMZ_POPUP.py: Path used but never imported, plus an unguarded Optional stdout pipe",
        "body": "src/CORE/WORMS/WORMZ_POPUP.py used 'Path(__file__).resolve().parent' with no 'from pathlib import Path' anywhere in the module, and iterated 'process.stdout.readline' where stdout is Optional[IO[str]], which basedpyright reported as reportOptionalMemberAccess. Fixed both: added the pathlib import, and captured the pipe once with an explicit None-guard that raises RuntimeError, because the subprocess is always spawned with stdout=PIPE, so a None pipe is a genuine error rather than a normal path.",
    },
    {
        "item_id": "k-07",
        "kind": "error_fix",
        "title": "generate_matrix_video.py: discarded-value column expression and the binding that fed only it",
        "body": "The per-pixel loop computed 'col_width = width // num_columns' and then evaluated 'x // col_width' as a statement whose value was discarded, reported as reportUnusedExpression. Neither identifier has any reader in the file, and the 'rain in columns' intent the adjacent comment describes was never implemented by the per-pixel random draw. Removed both lines, but kept 'num_columns = random.randint(15, 25)' so the random stream the frame generator consumes is bit-identical. The unimplemented column intent is a visual gap, not a lint finding, and is deliberately not repaired here.",
    },
    {
        "item_id": "k-08",
        "kind": "error_fix",
        "title": "generate_dashboard_video.py: font_variant exists only on FreeTypeFont, so the sized default font was loaded through an unprovable path",
        "body": "The font fallback chain ended in 'ImageFont.load_default().font_variant(size=size)'. load_default() is typed FreeTypeFont | ImageFont and font_variant is a FreeTypeFont member, so basedpyright reported reportAttributeAccessIssue, even though the call does work at runtime on the installed Pillow 12.3.0. Fixed with the documented API 'ImageFont.load_default(size=size)', which returns a FreeTypeFont directly and is available in every Pillow the repo pins (12.3.0 in requirements-3.15t.txt, 12.1.1 in requirements_314_full.txt). Verified at runtime that both forms return the same type.",
    },
    {
        "item_id": "k-09",
        "kind": "error_fix",
        "title": "21 diagnostics cleared across 16 files in two slices; 8 of the files were dead on arrival at import",
        "body": "Two explicit-pathspec slice commits (bb8c6f30 8 files +8/-18, b43bf6a9 8 files +13/-10) took 16 files to basedpyright 0E/0W. Measured with the identical clean-worktree method at 71bac91e (before) and b43bf6a9 (after): 21 diagnostics before (16 errors, 5 warnings), 0 after, 0 remaining. The whole-tree pool moved from 231E/54W (285 ids) to 190E/39W (229 ids) over the same interval, of which this lane's share is 21. These were real runtime bugs rather than type noise: 9 NameError-at-import, 2 NameError-on-call, 1 TypeError-on-call, 1 AttributeError-on-call, plus 8 lint-level findings. Import smoke moved from 8/8 slice files failing at HEAD to 7/8 passing.",
    },
    {
        "item_id": "k-10",
        "kind": "lesson",
        "title": "The tracked typecheck report lagged HEAD by four commits; the committed blob was not the pool",
        "body": "logs/basedpyright_report.json is rewritten by every lane run, so the COMMITTED blob is not the pool at HEAD. At 71bac91e the committed blob read 275E/63W (recorded 22:33Z, before four later commits landed) while the working-tree copy of the same file read 203E/54W (recorded 00:30Z, after 71bac91e landed at 20:28-04:00). Building the per-file pick list from the committed blob produced files the two Beam commits had already cleared, so the first pick list was four commits stale. Rule: compare the report's recorded_at against 'git log -1 --format=%cI HEAD' and take the newer of the two before deriving any per-file count.",
    },
    {
        "item_id": "k-11",
        "kind": "anti_pattern",
        "title": "create_scrolling_video.py: generated-template body sits at module scope with an unsubstituted {0} placeholder",
        "body": "Repairing the missing Path import moved this file's failure later in the import sequence, exposing the next defect: ast.parse shows 'def generate_video():' ending at L531, after which L584-L952 executes at MODULE scope, and it uses the template placeholders 'output_dir = r\"{0}\"' and 'video_file = r\"{1}\"' literally, so os.path.join(output_dir, 'azazael_scroll_text.txt') yields the path '{0}\\\\azazael_scroll_text.txt' and the open() raises FileNotFoundError at import. Repairing it needs the ~370-line body re-indented into the function, which exceeds the 150-lines-per-file commit gate, and the placeholders need a real substitution policy. Left for a dedicated lane and recorded here so the finding is not lost with the file.",
    },
    {
        "item_id": "k-12",
        "kind": "lesson",
        "title": "Measure the pool in a clean HEAD-pinned worktree when another lane has uncommitted edits",
        "body": "The main working tree carried 47 files modified by a live OpenCode lane, so a whole-tree basedpyright run there measures THEIR in-flight work, not the commit. This lane measured at a sparse git worktree checked out at the target commit instead, with pyrightconfig.json's relative venvPath repointed at the absolute C:/Azazael/.venvs, and reused tools/type_check_gate.py's own identity scheme by monkeypatching its REPO so ids stayed comparable with the tracked baseline. That also made the attribution exact rather than inferential: 231E/54W at 71bac91e versus 190E/39W at b43bf6a9, and a 21-diagnostic delta over the 16 slice files.",
    },
    {
        "item_id": "k-13",
        "kind": "violation",
        "title": "Near-miss: a live lane staged into the shared index, so the commit had to be path-scoped",
        "body": "The OpenCode lane staged its own 8 files (CORE/WORMS/expansion/{container,dendrite,dreaming,driver,envmesh,error_recovery,glia,immune}) into the shared index while I was staging mine, making the index 16 files. A plain 'git commit' would have swallowed their slice. Used 'git commit --only -- <paths>', which builds the commit from HEAD plus the named paths; the bulk gate (max 8 staged files) and the index drift check both saw only the 8 named files and passed. Side effect worth knowing: the partial commit left the other lane's 8 files modified but UNSTAGED. Their worktree content was fully intact and they commit with explicit pathspec, but a lane that assumed its index survived would have found it empty.",
    },
    {
        "item_id": "k-14",
        "kind": "anti_pattern",
        "title": "The ratchet caught a function-local import I introduced; the fix was the repo convention, not a suppression",
        "body": "My first repair for create_video.py put 'from PIL import Image, ImageDraw' inside create_frame(), to keep PIL optional and mirror how the same file imports moviepy lazily. The pre-commit anti-pattern ratchet failed the commit with 'import_inside_function 946 -> 947 (+1)'. The fix is the repo's own convention rather than a suppression: a module-level import, matching the eight sibling scripts in the same directory and the pillow pin in requirements. Suppression would have been the fallback; the structural fix came first, and the ratchet count returned to 2220 = baseline.",
    },
    {
        "item_id": "k-15",
        "kind": "lesson",
        "title": "The import smoke test earned its keep: it caught a defect the type checker could not",
        "body": "Checker plus compile verification reported the nine video files clean, but only an actual import proved that 7 of 8 slice files had been dead on arrival at HEAD with NameError. A file that compiles and type-checks can still be unimportable, and in this class of bug the import IS the user path. The import smoke plus the HEAD-versus-worktree before/after table is what turned a lint number into evidence, and it is what surfaced the separate {0} template defect in create_scrolling_video.py.",
    },
    {
        "item_id": "k-16",
        "kind": "lesson",
        "title": "The type gate's baseline is two days stale and reports pre-existing findings as NEW",
        "body": "logs/basedpyright_baseline.json was recorded 2026-10-20 and holds 1071 ids, while the tree now yields 229, so the diff reports 51 NEW and 893 fixed. None of the 51 are in the 16 files this lane touched; 29 of them are in src/eve_unleashed/cli.py, whose working-tree version (uncommitted by another lane) removes most of them. Re-recording the baseline would tighten the gate for every future commit but is a shared parameter, so this lane refreshed only the report and left the decision to the coordinator. The stale baseline is also why a clean tree can read as a large regression count.",
    },
    {
        "item_id": "k-17",
        "kind": "lesson",
        "title": "Harvest rows must be written at encounter time, not reconstructed at session end",
        "body": "This lane completed two verified slice commits and its harvest file was still empty at session end, which is exactly the failure the ERROR CAPTURE clause (#714/#716) exists to prevent. The detail that made the diagnosis fast, that create_scrolling_video.py's template body is at module scope, that the ratchet counts the whole shared index, that the report blob lagged HEAD by four commits, was still precise only because the lane's own artifacts carried it: the commit messages, the verify output, the before/after table. Reconstruction at session end produces plausible but unverified reasons, and a plausible reason that is wrong is worse than no row. Write the row when the error appears.",
    },
]


def main():
    existing = 0
    if os.path.exists(LANE):
        with io.open(LANE, encoding="utf-8") as fh:
            existing = sum(1 for ln in fh if ln.strip())
    with io.open(LANE, "a", encoding="utf-8", newline="\n") as fh:
        for row in ROWS:
            rec = {
                "source": SOURCE,
                "item_id": row["item_id"],
                "title": row["title"],
                "body": row["body"],
                "kind": row["kind"],
            }
            assert len(rec["body"]) >= 40, rec["item_id"]
            assert rec["kind"] in {"error_fix", "lesson", "mandate", "sop_rule",
                                   "violation", "anti_pattern"}, rec["item_id"]
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("lane file %s: %d existing rows + %d appended = %d"
          % (os.path.basename(LANE), existing, len(ROWS), existing + len(ROWS)))


if __name__ == "__main__":
    main()
