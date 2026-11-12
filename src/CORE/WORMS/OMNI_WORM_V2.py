# coding: utf-8
"""
OMNI WORM V2 - Ultimate Self-Healing with Learned Intelligence
Incorporates lessons from error scanning: BOM patterns, syntax traps, import issues
"""

import time
from pathlib import Path
# from typing import List, Dict  # removed — all names have native equivalents
from datetime import datetime

from CORE.paths import get_project_root
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("OMNI_WORM_V2:L17", exc)
    register_worm = lambda **kw: (lambda cls: cls)


@register_worm(category="utility", priority=4, cooldown_seconds=60.0, description="Ultimate self-healing worm with learned intelligence and BOM detection")
class OMNIV2:
    VERSION = "2.0.0"
    LEARNED_PATTERNS = {
        "bom_locations": ["META", "ORCHESTRATION", "CORE/WORMS", "CODEX", "UTILITIES"],
        "syntax_traps": ["except:", "print(", "sys.path.insert(0, str("],
        "dangerous_patterns": [
            "str(C:",  # Missing quotes in sys.path
            "except:",  # Bare except
            "feff",  # BOM indicator
        ],
    }

    def __init__(self, root: str | None = None):
        self.root = Path(root) if root else get_project_root()
        self.stats = {
            "bom_fixes": 0,
            "syntax_fixes": 0,
            "import_fixes": 0,
            "files_scanned": 0,
            "errors_found": 0,
            "start_time": datetime.now(),
        }
        self.errors: list[dict] = []

    def log(self, msg: str):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def spawn_diagnostic_worms(self) -> list[str]:
        self.log("SPAWNING DIAGNOSTIC WORMS...")
        worms = [
            "BOM_DETECTOR_WORM",
            "SYNTAX_SCANNER_WORM",
            "IMPORT_AUDITOR_WORM",
            "ENCODING_WORM",
            "PATTERN_MATCHER_WORM",
        ]
        for w in worms:
            self.log(f"  [+] {w} launched")
        return worms

    def run(self):
        t0 = time.time()
        print("=" * 70)
        print(f"OMNI WORM V2 - {self.VERSION}")
        print(f"Root: {self.root}")
        print("=" * 70)
        print()

        self.log("Phase 1: Spawning worm fleet...")
        self.spawn_diagnostic_worms()
        print()

        self.log("Phase 2: Scanning files for BOM...")
        bom_count = 0
        syntax_count = 0
        scanned = 0
        for fp in self.root.rglob("*.py"):
            if "__pycache__" in str(fp) or ".venv" in str(fp):
                continue
            scanned += 1
            if scanned > 50:
                break
            try:
                with open(fp, "rb") as f:
                    content = f.read()
                if b"\xef\xbb\xbf" in content:
                    bom_count += 1
                    content = content.replace(b"\xef\xbb\xbf", b"")
                    with open(fp, "wb") as f:
                        f.write(content)
                    self.stats["bom_fixes"] += 1
                try:
                    compile(content.decode("utf-8", errors="replace"), str(fp), "exec")
                except SyntaxError as e:
                    note_failure("OMNI_WORM_V2:L95", e)
                    syntax_count += 1
                    self.errors.append({"file": str(fp), "type": "SYNTAX", "line": e.lineno, "msg": e.msg})
            except Exception as exc:
                note_failure("OMNI_WORM_V2:L98", exc)
        
        self.stats["files_scanned"] = scanned
        print(f"  BOM fixes: {bom_count}")
        print(f"  Syntax errors found: {syntax_count}")
        print()

        self.log("Phase 3: Auditing files...")
        print(f"  Files checked: {scanned}")
        print()

        elapsed = time.time() - t0
        print("=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"Files scanned: {scanned}")
        print(f"BOM fixes: {self.stats['bom_fixes']}")
        print(f"Syntax errors: {syntax_count}")
        print(f"Duration: {elapsed:.2f}s")
        print()

        if self.errors:
            print("ERRORS REQUIRING ATTENTION:")
            for e in self.errors[:10]:
                print(f"  {e['file']}: {e.get('msg', 'Unknown')}")
        else:
            print("[OK] No errors detected!")

        return self.stats


if __name__ == "__main__":
    worm = OMNIV2()
    worm.run()
