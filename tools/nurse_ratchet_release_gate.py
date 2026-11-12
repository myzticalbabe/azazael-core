#!/usr/bin/env python3
# coding: utf-8
"""Fail a publication whose paperwork, allowlist or disclosures are incomplete.

WHY THIS IS A GATE AND NOT A CHECKLIST
--------------------------------------
Publishing is the one operation in this project that cannot be undone. Three
failure modes are all silent, and each one is mechanical enough to check:

  1. **A missing community file.** GitHub's own community-profile checklist
     defines the set (README, LICENSE, CODE_OF_CONDUCT, CONTRIBUTING, SECURITY,
     issue templates with valid keys, PR template). A repository without them is
     not read as minimal; it is read as abandoned.
  2. **Fails-open evidence.** A deny-list ships whatever nobody remembered to
     add. So publication is an ALLOWLIST: every shipped path must be declared in
     PUBLIC_MANIFEST.json, and an undeclared path above the size floor fails.
  3. **A silent secret.** One key in one file is the whole cost of the mistake.

And one check exists for a reason that is not mechanical. `--require-disclosure`
fails when the README has lost its Limitations section. This project's crisis
router measured **0.18 recall on one holdout and 0.83 on another**; a public therapeutic
project that hides that number is worse than one that does not exist, because a
reader would trust it. The gate refuses to let the disclosure be deleted.

Usage:
  python tools/nurse_ratchet_release_gate.py --check
  python tools/nurse_ratchet_release_gate.py --check --json
"""
from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
#: The gate runs in two layouts: the monorepo, where the public tree is a
#: subdirectory, and the materialised public tree itself, where it is the root.
#: Resolving by existence rather than by rewriting keeps one copy of the file
#: honest in both -- a rewrite applied at copy time is a second version of the
#: gate that nobody tests.
PROJECT = (ROOT / "Project_Nurse_Ratchet"
           if (ROOT / "Project_Nurse_Ratchet").is_dir() else ROOT)
MANIFEST = PROJECT / "PUBLIC_MANIFEST.json"

#: GitHub's community-profile checklist, plus the four files this project adds
#: because a forensic-adjacent project owes a reader more than a code README.
REQUIRED_FILES: Tuple[str, ...] = (
    "README.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "CITATION.cff",
    "DISCLOSURE_POLICY.md",
    "PUBLIC_MANIFEST.json",
    "docs/index.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
)

#: Headings the README must carry. Limitations and the negative results are
#: required, not encouraged -- see the module docstring.
README_REQUIRED_HEADINGS: Tuple[str, ...] = (
    "## What this is",
    "## How certifiable this is",
    "## What this is not",
    "## Limitations",
    "## Negative results and open problems",
    "## Ethics",
    "## Status",
)

#: Issue templates must carry these keys in their frontmatter. GitHub's checklist
#: reads them, so a template without them shows as MISSING in the profile even
#: though the file exists -- a failure that looks like success.
ISSUE_TEMPLATE_KEYS: Tuple[str, ...] = ("name:", "description:")

SECRET_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("aws_access_key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("github_token", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    ("slack_token", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    ("openai_key", r"\bsk-[A-Za-z0-9]{32,}\b"),
    ("assigned_secret", r"(?i)\b(api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*"
                        r"[\"']?[A-Za-z0-9_\-]{16,}[\"']?"),
)

#: Extensions worth reading for secrets. Binary and vendored blobs are skipped.
TEXT_SUFFIXES = (".py", ".md", ".json", ".yml", ".yaml", ".cff", ".toml", ".cfg",
                 ".ini", ".txt", ".sh", ".js", ".ts", ".html")

#: Files below this size are not demanded in the manifest: a licence, a template
#: or a small config is not where evidence hides.
MANIFEST_SIZE_FLOOR = 4096


def load_manifest(path: Path = MANIFEST) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))


def check_required_files(project: Path = PROJECT) -> List[str]:
    missing = []
    for relative in REQUIRED_FILES:
        if not (project / relative).exists():
            missing.append(relative)
    return missing


def check_readme_disclosure(project: Path = PROJECT) -> List[str]:
    """The Limitations section is load-bearing, so its absence is a failure."""
    readme = project / "README.md"
    if not readme.exists():
        return ["README.md is absent"]
    text = readme.read_text(encoding="utf-8")
    return ["README.md is missing the %r section" % heading
            for heading in README_REQUIRED_HEADINGS if heading not in text]


def check_certification_bar(project: Path = PROJECT) -> List[str]:
    """The percentage a reader sees must be the percentage the data supports.

    The tool is loaded from the tree under test rather than imported as a module:
    there is no installed package to import it from, and a gate that verified a
    different copy than the one shipping would be measuring the wrong file.
    Malformed data raises out of the tool, which is a failure here by design --
    an unreadable certification file must not pass as "no findings".
    """
    tool = project / "tools" / "certification_bar.py"
    if not tool.exists():
        return ["tools/certification_bar.py is missing; the bar cannot be verified"]
    spec = importlib.util.spec_from_file_location("nurse_ratchet_certification_bar",
                                                  tool)
    if spec is None or spec.loader is None:
        return ["tools/certification_bar.py could not be loaded"]
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        ok, message = module.check(project)
    except Exception as exc:                                   # noqa: BLE001 - reported
        return ["certification bar could not be verified: %s" % exc]
    return [] if ok else [message]


def check_issue_templates(project: Path = PROJECT) -> List[str]:
    problems = []
    directory = project / ".github" / "ISSUE_TEMPLATE"
    for path in sorted(directory.glob("*.yml")) + sorted(directory.glob("*.md")):
        if path.name == "config.yml":
            continue
        text = path.read_text(encoding="utf-8")
        for key in ISSUE_TEMPLATE_KEYS:
            if not re.search(r"(?m)^%s" % re.escape(key), text):
                problems.append("%s lacks frontmatter key %r" % (path.name, key))
    return problems


def check_manifest_paths(manifest: Dict, project: Path = PROJECT) -> Tuple[List[str], List[str]]:
    """Every declared path exists, and no declared path matches a deny rule."""
    problems: List[str] = []
    denied: List[str] = []
    for entry in manifest.get("ship", []):
        source = (project / entry["from"]).resolve()
        if not source.exists():
            problems.append("manifest declares a path that does not exist: %s"
                            % entry["from"])
            continue
        if _denied(str(source), manifest):
            denied.append("%s matches a never_ship rule" % entry["from"])
    return problems, denied


def _denied(path_text: str, manifest: Dict) -> bool:
    lowered = path_text.replace("\\", "/").lower()
    if any(token.lower() in lowered for token in manifest.get("deny_substrings", [])):
        return True
    name = Path(lowered).name
    for rule in manifest.get("never_ship", []):
        glob = rule["glob"].lower()
        if fnmatch.fnmatch(name, glob) or fnmatch.fnmatch(lowered, "*/" + glob):
            return True
    return False


def scan_secrets(path: Path) -> List[Dict]:
    """Report secret-shaped text with the value masked.

    The finding names the file and line and the RULE, never the secret: a scanner
    that prints what it found has copied the secret into the log.
    """
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    findings = []
    for number, line in enumerate(text.splitlines(), start=1):
        for name, pattern in SECRET_PATTERNS:
            match = re.search(pattern, line)
            if match:
                findings.append({"file": str(path), "line": number, "rule": name,
                                 "masked": match.group(0)[:4] + "..." + "(%d chars)"
                                 % len(match.group(0))})
    return findings


def check_undeclared(manifest: Dict, project: Path = PROJECT) -> List[str]:
    """Files above the size floor that no manifest entry claims.

    This is the half that fails CLOSED: a new evidence directory shows up here
    the moment it is added, without anyone remembering to deny it.
    """
    declared = set()
    for entry in manifest.get("ship", []):
        source = (project / entry["from"]).resolve()
        declared.add(source)
        # The DESTINATION of a declared entry is declared by definition. Leaving
        # it out made every materialised tree fail its own gate: `publicize.py`
        # wrote nurse_ratchet/psyche/affect.py exactly as the manifest said, and the gate
        # then called it undeclared, because only `from` was collected. The
        # monorepo looked clean only because nothing had been materialised yet.
        declared.add((project / str(entry["to"])).resolve())
        if source.exists() and source.is_dir():
            declared.update(p.resolve() for p in source.rglob("*") if p.is_file())
    problems = []
    for path in sorted(project.rglob("*")):
        if not path.is_file():
            continue
        if ".git" in path.parts:
            continue
        # Byte-compiled, cached and build output is not content: it is derived
        # from declared files and differs per interpreter. Demanding it be
        # declared would produce a manifest that no longer means anything.
        if any(part in {"__pycache__", ".pytest_cache", ".mypy_cache",
                        ".ruff_cache", "build", "dist"} for part in path.parts):
            continue
        if path.suffix == ".pyc" or ".egg-info" in str(path):
            continue
        if path.stat().st_size < MANIFEST_SIZE_FLOOR:
            continue
        if path.resolve() not in declared:
            problems.append("undeclared file above %d bytes: %s"
                            % (MANIFEST_SIZE_FLOOR, path.relative_to(project)))
    return problems


def run_checks(project: Path = PROJECT, manifest_path: Optional[Path] = None) -> Dict:
    manifest = load_manifest(manifest_path or MANIFEST)
    missing = check_required_files(project)
    disclosure = check_readme_disclosure(project)
    templates = check_issue_templates(project)
    manifest_problems, denied = check_manifest_paths(manifest, project)
    undeclared = check_undeclared(manifest, project)
    certification = check_certification_bar(project)
    secrets_found: List[Dict] = []
    for path in sorted(project.rglob("*")):
        if path.is_file() and ".git" not in path.parts:
            secrets_found.extend(scan_secrets(path))
    checks = {
        "required_files": missing,
        "readme_disclosure": disclosure,
        "issue_templates": templates,
        "manifest_paths": manifest_problems,
        "denied_paths": denied,
        "undeclared_files": undeclared,
        "certification_bar": certification,
        "secrets": secrets_found,
    }
    failures = sum(len(v) for v in checks.values())
    return {"project": str(project), "checks": checks, "failures": failures,
            "result": "PASS" if failures == 0 else "FAIL"}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--project", default=str(PROJECT))
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = run_checks(Path(args.project),
                        Path(args.manifest) if args.manifest else None)
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        print("nurse-ratchet publication gate -- %s" % report["project"])
        for name, findings in report["checks"].items():
            status = "ok" if not findings else "%d problem(s)" % len(findings)
            print("  %-18s %s" % (name, status))
            for finding in findings[:12]:
                print("      %s" % (finding if isinstance(finding, str)
                                    else json.dumps(finding)))
        print("\n%s: %d failure(s)" % (report["result"], report["failures"]))
        if report["failures"]:
            print("Publication is blocked. An undeclared file is not necessarily "
                  "forbidden -- declare it in PUBLIC_MANIFEST.json, or remove it.")
    return 0 if report["failures"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
