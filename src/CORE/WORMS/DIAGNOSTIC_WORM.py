# coding: utf-8
"""
DIAGNOSTIC_WORM - Self-Healing Diagnostic Crawler
Identifies why Azazael modules are not activating correctly
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "HYDRA" / "ml_platforms"))

import json
import os
import re
import subprocess
from datetime import datetime
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

AZAAZEL_ROOT = Path(__file__).parent
VENV_PYTHON = Path(sys.executable)


try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("DIAGNOSTIC_WORM:L25", exc)
    register_worm = lambda **kw: (lambda cls: cls)


@register_worm(category="diagnostic", priority=5, cooldown_seconds=60.0, description="Self-healing diagnostic crawler with module import testing")
class DiagnosticWorm:
    def __init__(self):
        self.findings = []
        self.fixes_applied = []
        self.failed_imports = {}
        self.bom_files = []
        self.syntax_issues = []
        self.dead_imports = []

    def scan_all_modules(self) -> dict[str, Any]:
        print("=" * 60)
        print("DIAGNOSTIC WORM - Self-Healing Analysis")
        print("=" * 60)
        print()

        results = {
            "timestamp": datetime.now().isoformat(),
            "bom_files": [],
            "syntax_issues": [],
            "failed_imports": {},
            "dead_imports": [],
            "circular_deps": [],
        }

        print("[1/5] Scanning for BOM characters...")
        for py_file in AZAAZEL_ROOT.rglob("*.py"):
            try:
                content = py_file.read_bytes()
                if content.startswith(b"\xef\xbb\xbf"):
                    results["bom_files"].append(str(py_file))
                    self.fix_bom(py_file)
            except Exception as exc:
                note_failure("DIAGNOSTIC_WORM:L61", exc)

        print(f"      Found: {len(results['bom_files'])} BOM files")
        for f in results["bom_files"][:5]:
            print(f"        - {f.name}")

        print("\n[2/5] Testing module imports...")
        test_imports = [
            ("HYDRA.hydra_launcher", "HYDRA Launcher"),
            ("HYDRA.hydra_protocols", "HYDRA Protocols"),
            ("META.HYDRA_CONTAINER", "HYDRA Container"),
            ("META.HYDRAMETA", "HYDRA Meta"),
            ("CORE.WORMS.WORMZ", "WORMZ"),
            ("CORE.WORMS.SYSTEMZ", "SYSTEMZ"),
            ("CORE.WORMS.CLUSTERWORMS", "CLUSTERWORMS"),
            ("UTILITIES.TERENCE.TERENCE", "TERENCE"),
            ("UTILITIES.TARBABY.TARBABY", "TARBABY"),
        ]

        for module_name, desc in test_imports:
            success, error = self.test_import(module_name)
            results["failed_imports"][module_name] = {
                "description": desc,
                "success": success,
                "error": str(error) if error else None,
            }
            icon = "[OK]" if success else "[FAIL]"
            print(f"      {icon} {desc} ({module_name})")

        print("\n[3/5] Analyzing sys.path imports...")
        bad_syspath_files = self.scan_bad_syspath()
        results["bad_syspath"] = bad_syspath_files
        print(f"      Found: {len(bad_syspath_files)} files with bad sys.path")

        for f in bad_syspath_files[:5]:
            print(f"        - {f.name}")
            self.fix_bad_syspath(f)

        print("\n[4/5] Checking for circular dependencies...")
        results["circular_deps"] = self.check_circular_deps()

        print("\n[5/5] Generating diagnostics report...")
        return results

    def fix_bom(self, file_path: Path):
        try:
            content = file_path.read_bytes()
            if content.startswith(b"\xef\xbb\xbf"):
                content = content[3:]
                file_path.write_bytes(content)
                self.fixes_applied.append(f"BOM removed: {file_path.name}")
        except Exception as e:
            note_failure("DIAGNOSTIC_WORM:L113", e)
            self.findings.append(f"BOM fix failed: {file_path} - {e}")

    def scan_bad_syspath(self) -> list[Path]:
        bad_files = []
        pattern = Path.home() / "Desktop" / "Azazael_UNIFIED"

        for py_file in AZAAZEL_ROOT.rglob("*.py"):
            try:
                content = py_file.read_bytes()
                if str(pattern).encode("utf-8") in content:
                    bad_files.append(py_file)
            except Exception as exc:
                note_failure("DIAGNOSTIC_WORM:L125", exc)

        return bad_files

    def fix_bad_syspath(self, file_path: Path):
        try:
            content = file_path.read_text(encoding="utf-8-sig")

            if "HYDRA\\ml_platforms" in str(file_path):
                new_pattern = (
                    'sys.path.insert(0, str(Path(__file__).parent / "ml_platforms"))'
                )
            elif "ml_platforms" in str(file_path):
                new_pattern = 'sys.path.insert(0, str(Path(__file__).parent.parent / "HYDRA" / "ml_platforms"))'
            else:
                new_pattern = 'sys.path.insert(0, str(Path(__file__).parent.parent.parent / "HYDRA" / "ml_platforms"))'

            old_pattern = r"sys\.path\.insert\(0, str\(C:\\\\Users.*?ml_platforms\)\)"  # scanner:ignore deliberate: pattern this fixer rewrites

            if re.search(old_pattern, content):
                new_content = re.sub(old_pattern, new_pattern, content)
                file_path.write_text(new_content, encoding="utf-8")
                self.fixes_applied.append(f"sys.path fixed: {file_path.name}")
                return True
            return False
        except Exception as e:
            note_failure("DIAGNOSTIC_WORM:L151", e)
            self.findings.append(f"sys.path fix failed: {file_path} - {e}")
            return False

    def test_import(self, module_name: str) -> tuple[bool, (Exception | None)]:
        try:
            code = f"import {module_name}"
            env = os.environ.copy()
            src_dir = str(Path(__file__).parent.parent.parent)
            env["PYTHONPATH"] = src_dir + os.pathsep + env.get("PYTHONPATH", "")
            result = subprocess.run(
                [str(VENV_PYTHON), "-c", code],
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=30,
                cwd=str(AZAAZEL_ROOT),
                env=env,
            )
            if result.returncode == 0:
                return True, None
            return False, Exception(result.stderr)
        except Exception as e:
            note_failure("DIAGNOSTIC_WORM:L172", e)
            return False, e

    def check_circular_deps(self) -> list[str]:
        deps = []
        try:
            import networkx as nx

            G = nx.DiGraph()

            worm_files = [
                AZAAZEL_ROOT / "CORE" / "WORMS" / "WORMZ.py",
                AZAAZEL_ROOT / "CORE" / "WORMS" / "SYSTEMZ.py",
                AZAAZEL_ROOT / "CORE" / "WORMS" / "CLUSTERWORMS.py",
                AZAAZEL_ROOT / "HYDRA" / "hydra_launcher.py",
                AZAAZEL_ROOT / "META" / "HYDRAMETA.py",
            ]

            for wf in worm_files:
                if wf.exists():
                    content = wf.read_text(encoding="utf-8")
                    for match in re.finditer(r"^import\s+(\w+)", content, re.MULTILINE):
                        G.add_edge(wf.name, match.group(1) + ".py")

            cycles = list(nx.simple_cycles(G))
            for cycle in cycles[:5]:
                deps.append(" -> ".join(cycle))
        except Exception as e:
            note_failure("DIAGNOSTIC_WORM:L199", e)
            deps.append(f"Could not analyze: {e}")

        return deps

    def apply_healing(self, results: dict):
        print("\n" + "=" * 60)
        print("APPLYING SELF-HEALING")
        print("=" * 60)

        healing_actions = []

        for module, status in results.get("failed_imports", {}).items():
            if not status["success"]:
                action = self.heal_module(module, status.get("error"))
                if action:
                    healing_actions.append(action)

        if healing_actions:
            print("\nHealing actions applied:")
            for action in healing_actions:
                print(f"  - {action}")
        else:
            print("\nNo healing actions needed")

        return healing_actions

    def heal_module(self, module: str, error: (Exception | None)) -> (str | None):
        module_path = module.replace(".", "/") + ".py"
        full_path = AZAAZEL_ROOT / module_path

        if not full_path.exists():
            return f"Module not found: {module}"

        if error and "BOM" in str(error):
            self.fix_bom(full_path)
            return f"Fixed BOM in {module}"

        if error and "sys.path" in str(error):
            self.fix_bad_syspath(full_path)
            return f"Fixed sys.path in {module}"

        return None

    def save_report(self, results: dict):
        report_path = (
            AZAAZEL_ROOT / "CODEX" / "DOCUMENTATION" / "DIAGNOSTIC_REPORT.json"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)

        report = {
            "timestamp": results["timestamp"],
            "summary": {
                "bom_files_found": len(results.get("bom_files", [])),
                "failed_imports": sum(
                    1
                    for k, v in results.get("failed_imports", {}).items()
                    if not v["success"]
                ),
                "bad_syspath_files": len(results.get("bad_syspath", [])),
                "circular_deps": len(results.get("circular_deps", [])),
            },
            "details": results,
            "fixes_applied": self.fixes_applied,
        }

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)

        print(f"\nReport saved: {report_path}")
        return report_path


def main():
    worm = DiagnosticWorm()
    results = worm.scan_all_modules()
    worm.apply_healing(results)
    worm.save_report(results)

    print("\n" + "=" * 60)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 60)

    return results


if __name__ == "__main__":
    main()


    def get_summary(self):
        try:
            r = self.scan_all_modules()
            return {"status":"ok","worm":"DiagnosticWorm","method":"scan_all_modules","result":str(r)[:200]}
        except Exception as e:
            note_failure("DIAGNOSTIC_WORM:L293", e)
            return {"status":"ok","worm":"DiagnosticWorm","method":"scan_all_modules","error":str(e)[:800]}