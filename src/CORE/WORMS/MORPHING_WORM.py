# coding: utf-8
"""
MORPHING WORM SYSTEM V1 - Adaptive Self-Modifying Error Detection
Worms can morph, spawn variants, and learn from new error patterns
"""

import sys
import re
import json
import subprocess
from pathlib import Path
# from typing import Dict, List, Optional  # removed — all names have native equivalents
from dataclasses import dataclass, field

from CORE.paths import get_project_root
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class WormGenome:
    name: str
    pattern_type: str
    detection_code: str
    fix_code: str = ""
    severity: str = "MEDIUM"
    success_count: int = 0
    variants: list[str] = field(default_factory=list)


class MorphingWorm:
    VERSION = "1.0.0"

    def __init__(self, root: str | None = None):
        self.root = Path(root) if root else get_project_root()
        self.genome_library: dict[str, WormGenome] = {}
        self.error_patterns: list[dict] = []
        self.stats = {"morphs": 0, "fixes": 0, "scans": 0}

    def register_genome(self, genome: WormGenome):
        self.genome_library[genome.name] = genome
        print(f"  [GENOME REGISTERED] {genome.name}")

    def morph_worm(self, name: str, new_code: str) -> bool:
        if name in self.genome_library:
            self.genome_library[name].detection_code = new_code
            self.genome_library[name].success_count += 1
            self.stats["morphs"] += 1
            print(f"  [MORPHED] {name}")
            return True
        return False

    def spawn_variant(
        self, parent: str, variant_name: str, modifications: str
    ) -> WormGenome | None:
        if parent in self.genome_library:
            parent_gene = self.genome_library[parent]
            new_gene = WormGenome(
                name=variant_name,
                pattern_type=parent_gene.pattern_type,
                detection_code=parent_gene.detection_code + "\n" + modifications,
                fix_code=parent_gene.fix_code,
                severity=parent_gene.severity,
                variants=[parent],
            )
            self.genome_library[variant_name] = new_gene
            self.stats["morphs"] += 1
            print(f"  [VARIANT SPAWNED] {variant_name} from {parent}")
            return new_gene
        return None

    def detect_bom(self, fp: Path) -> (dict | None):
        try:
            with open(fp, "rb") as f:
                content = f.read()
            if b"\xef\xbb\xbf" in content:
                return {"type": "BOM", "severity": "HIGH", "fix": "remove_bom"}
        except (OSError, PermissionError) as exc:
            note_failure("MORPHING_WORM:L76", exc)
        return None

    def detect_syntax(self, fp: Path) -> (dict | None):
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                code = f.read()
            compile(code, str(fp), "exec")
        except SyntaxError as e:
            note_failure("MORPHING_WORM:L85", e)
            return {
                "type": "SYNTAX",
                "severity": "CRITICAL",
                "line": e.lineno,
                "msg": e.msg,
            }
        except Exception as exc:
            note_failure("MORPHING_WORM:L92", exc)
        return None

    def detect_imports(self, fp: Path) -> (dict | None):
        try:
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", str(fp)],
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=10,
            )
            if result.returncode != 0:
                return {"type": "IMPORT", "severity": "MEDIUM"}
        except (subprocess.TimeoutExpired, Exception) as exc:
            note_failure("MORPHING_WORM:L106", exc)
        return None

    def detect_escape_sequences(self, fp: Path) -> (dict | None):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                content = f.read()
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                if "\\ " in line and 'r"' not in line and "r'" not in line:
                    if '"""' not in line and "'''" not in line:
                        return {"type": "ESCAPE", "severity": "LOW", "line": i}
        except (OSError, PermissionError) as exc:
            note_failure("MORPHING_WORM:L119", exc)
        return None

    def detect_malformed_patterns(self, fp: Path) -> list[dict]:
        issues = []
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            patterns = [
                (r"except:\s*$", "Bare except clause"),
                (r"sys\.path\.insert\(0,\s*str\(", "str() wrapper on path"),
                (r"import\s+\w+\s+as\s+\w+\s*$", "Import missing newline"),
                (r"\[.*\]\s*$", "Unclosed bracket"),
                (r"\(\s*$", "Unclosed paren"),
            ]

            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                for pattern, desc in patterns:
                    if re.search(pattern, line.strip()):
                        issues.append(
                            {
                                "type": "MALFORMED",
                                "severity": "HIGH",
                                "line": i,
                                "desc": desc,
                            }
                        )
        except (OSError, UnicodeDecodeError) as exc:
            note_failure("MORPHING_WORM:L149", exc)
        return issues

    def auto_fix_bom(self, fp: Path) -> bool:
        try:
            with open(fp, "rb") as f:
                content = f.read()
            content = content.replace(b"\xef\xbb\xbf", b"")
            with open(fp, "wb") as f:
                f.write(content)
            return True
        except (OSError, PermissionError) as exc:
            note_failure("MORPHING_WORM:L161", exc)
            return False

    def auto_fix_syntax_known(self, fp: Path, error_type: str) -> bool:
        if error_type == "BOM":
            return self.auto_fix_bom(fp)
        return False

    def scan_file(self, fp: Path) -> list[dict]:
        issues = []

        if bom := self.detect_bom(fp):
            issues.append(bom)

        if syntax := self.detect_syntax(fp):
            issues.append(syntax)

        if imports := self.detect_imports(fp):
            issues.append(imports)

        if escapes := self.detect_escape_sequences(fp):
            issues.append(escapes)

        for mal in self.detect_malformed_patterns(fp):
            issues.append(mal)

        return issues

    def evolve_from_errors(self, errors: list[dict]):
        for err in errors:
            pattern_type = err.get("type", "UNKNOWN")
            existing = [
                g
                for g in self.genome_library.values()
                if g.pattern_type == pattern_type
            ]
            if not existing:
                new_genome = WormGenome(
                    name=f"WORM_{pattern_type}_{len(self.genome_library)}",
                    pattern_type=pattern_type,
                    detection_code=f"# Auto-evolved for {pattern_type}\ndef detect_{pattern_type.lower()}(fp):\n    pass",
                    severity=err.get("severity", "MEDIUM"),
                )
                self.register_genome(new_genome)
                self.stats["morphs"] += 1

    def run_comprehensive_scan(self):
        print("=" * 70)
        print(f"MORPHING WORM SYSTEM V{self.VERSION}")
        print("=" * 70)
        print()

        all_issues = []
        files = [
            p
            for p in self.root.rglob("*.py")
            if "__pycache__" not in str(p) and ".venv" not in str(p)
        ]

        print(f"[+] Scanning {len(files)} files...")
        print()

        critical = 0
        high = 0
        low = 0

        for i, fp in enumerate(files):
            if i % 50 == 0:
                print(f"  Progress: {i}/{len(files)}")

            issues = self.scan_file(fp)
            for issue in issues:
                issue["file"] = str(fp.relative_to(self.root))
                all_issues.append(issue)

                sev = issue.get("severity", "LOW")
                if sev == "CRITICAL":
                    critical += 1
                elif sev == "HIGH":
                    high += 1
                else:
                    low += 1

                if issue.get("type") == "BOM":
                    if self.auto_fix_bom(fp):
                        issue["fixed"] = True
                        self.stats["fixes"] += 1

        print()
        print("=" * 70)
        print("MORPH SCAN COMPLETE")
        print("=" * 70)
        print(f"Files scanned: {len(files)}")
        print(f"Issues found: {len(all_issues)}")
        print(f"  CRITICAL: {critical}")
        print(f"  HIGH: {high}")
        print(f"  LOW: {low}")
        print(f"Fixes applied: {self.stats['fixes']}")
        print(f"Genome morphs: {self.stats['morphs']}")
        print()

        if critical > 0 or high > 0:
            print("CRITICAL/HIGH ISSUES:")
            for issue in all_issues:
                if issue.get("severity") in ["CRITICAL", "HIGH"]:
                    print(
                        f"  {issue['file']}: {issue.get('type')} - {issue.get('desc', issue.get('msg', ''))}"
                    )
        else:
            print("[[OK]] No critical or high severity issues!")

        print()
        self.evolve_from_errors(all_issues)

        return all_issues

    def save_state(self, filename: str = "morph_worm_state.json"):
        state = {
            "genomes": {
                k: {
                    "name": v.name,
                    "pattern_type": v.pattern_type,
                    "severity": v.severity,
                    "success_count": v.success_count,
                }
                for k, v in self.genome_library.items()
            },
            "stats": self.stats,
        }
        with open(self.root / filename, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        print(f"[+] State saved to {filename}")


    def get_summary(self) -> dict:
        """Fast summary — avoids full file scan."""
        return {
            "status": "ok",
            "worm": "MorphingWorm",
            "version": self.VERSION,
            "genomes": len(self.genome_library),
            "patterns": len(self.error_patterns),
            "stats": self.stats,
        }


if __name__ == "__main__":
    worm = MorphingWorm()
    issues = worm.run_comprehensive_scan()
    worm.save_state()