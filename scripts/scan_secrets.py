#!/usr/bin/env python3
"""
Secrets Scanner — Detect exposed API keys, tokens, and secrets
in build artifacts and source files.

Scans files for known secret patterns and reports findings.
Designed to run as a CI step or pre-commit hook.

Usage:
    python scripts/scan_secrets.py                          # Scan common build dirs
    python scripts/scan_secrets.py --all                    # Scan entire project
    python scripts/scan_secrets.py --path build/            # Scan specific path
    python scripts/scan_secrets.py --ci                     # Exit with code 1 on findings
"""

from __future__ import annotations

import os
import re
import sys
import argparse
from collections import Counter
from pathlib import Path
from typing import List, Tuple

# ── Known API key patterns ──────────────────────────────────────

# These patterns match common API key formats. Each is a (regex, label) tuple.
SECRET_PATTERNS: List[Tuple[str, str]] = [
    # OpenAI
    (r"sk-proj-[A-Za-z0-9]{20,}", "OpenAI Project Key"),
    (r"sk-[A-Za-z0-9]{20,}", "OpenAI Legacy Key"),
    # Anthropic
    (r"sk-ant-[A-Za-z0-9]{40,}", "Anthropic API Key"),
    # Cohere — 40-60 alphanumeric, word-bounded, must contain non-hex letter + digit
    # to exclude SHA hashes (hex-only) and long identifiers (no digits)
    (r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]{40,60}(?![A-Za-z0-9]))(?=.*[g-zG-Z])(?=.*[0-9])[A-Za-z0-9]{40,60}", "Cohere API Key (40+ alphanumeric)"),
    # Groq
    (r"gsk_[A-Za-z0-9]{40,}", "Groq API Key"),
    # Mistral — detected via env-var assignment pattern below (bare 32-char keys
    # are indistinguishable from random identifiers like attempt_id)
    # Cerebras
    (r"csk-[A-Za-z0-9]{40,}", "Cerebras API Key"),
    # OpenRouter
    (r"sk-or-v1-[A-Za-z0-9]{40,}", "OpenRouter API Key"),
    # HuggingFace
    (r"hf_[A-Za-z0-9]{30,}", "HuggingFace Token"),
    # Gemini
    (r"AIzaSy[A-Za-z0-9_-]{30,}", "Gemini API Key"),
    # Cloudflare
    (r"cfut_[A-Za-z0-9]{30,}", "Cloudflare API Token"),
    # Generic bearer tokens
    (r"Bearer [A-Za-z0-9_.-]{20,}", "Bearer Token"),
    # Generic env assignment patterns
    (r"(?i)(OPENAI_API_KEY|ANTHROPIC_API_KEY|MISTRAL_API_KEY|COHERE_API_KEY|GROQ_API_KEY|GEMINI_API_KEY|CEREBRAS_API_KEY|OPENROUTER_API_KEY|HF_TOKEN|CLOUDFLARE_API_TOKEN|SILICONFLOW_API_KEY|ZHIPU_API_KEY)\s*=\s*['\"][A-Za-z0-9_-]{10,}", "Env API Key Assignment"),
]

# ── Files/directories to skip ──────────────────────────────────

SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".coverage",
    "archive", ".archive", "nuitka_build_backup",
}

SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".o", ".obj", ".exe", ".dll", ".pyd",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".mp4", ".wmv", ".avi",
    ".db", ".sqlite", ".sqlite3",
}


# ── Scanner ─────────────────────────────────────────────────────

def should_skip(path: Path) -> bool:
    """Check if a path should be skipped."""
    for part in path.parts:
        if part in SKIP_DIRS:
            return True
    if path.suffix.lower() in SKIP_EXTENSIONS:
        return True
    return False


def scan_file(filepath: Path, quiet: bool = False) -> List[dict]:
    """Scan a single file for secret patterns."""
    findings = []
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return findings

    for pattern, label in SECRET_PATTERNS:
        for match in re.finditer(pattern, text):
            line_num = text[:match.start()].count("\n") + 1
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            if line_end == -1:
                line_end = len(text)
            context = text[line_start:line_end].strip()

            # Sanitize for console output — Windows can't encode �
            safe_match = (match.group()[:20] + "..." if len(match.group()) > 20 else match.group()).replace("\ufffd", "?")
            safe_context = context[:100].replace("\ufffd", "?")

            finding = {
                "file": str(filepath),
                "line": line_num,
                "pattern": label,
                "match": safe_match,
                "context": safe_context,
            }
            findings.append(finding)

    return findings


def scan_directory(
    root: Path,
    recursive: bool = True,
    quiet: bool = False,
    max_findings: int = 50,
) -> List[dict]:
    """Scan a directory recursively for secrets."""
    all_findings: List[dict] = []
    if not root.exists():
        return all_findings

    items = [root] if root.is_file() else list(root.rglob("*")) if recursive else list(root.iterdir())

    for item in items:
        if should_skip(item):
            continue
        if item.is_file():
            findings = scan_file(item, quiet=quiet)
            for f in findings:
                all_findings.append(f)
                if not quiet:
                    severity = "\033[91m[SECRET]\033[0m"
                    file_path = f['file'].replace("\ufffd", "?")
                    print(f"{severity} {f['pattern']} in {file_path}:{f['line']}")
                    print(f"       Match: {f['match']}")
                    print(f"       Context: {f['context']}")
                    print()
            if len(all_findings) >= max_findings:
                if not quiet:
                    print(f"\033[93m[WARNING]\033[0m Reached max findings ({max_findings}), stopping scan.")
                sys.stdout.flush()
                break

    return all_findings


# ── CLI ─────────────────────────────────────────────────────────

def get_default_scan_paths() -> List[Path]:
    """Get default paths to scan — common build artifact directories."""
    root = Path(__file__).resolve().parent.parent
    paths = []
    candidates = [
        "build",
        "build_test",
        "verify.build",
        "nuitka_test2",
        "packages/azazael-core/cli.onefile-build",
    ]
    for c in candidates:
        p = root / c
        if p.exists():
            paths.append(p)
    libra_path = Path.home() / "Desktop" / "Libra"
    if libra_path.exists():
        paths.append(libra_path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan for exposed API keys, tokens, and secrets in build artifacts",
    )
    parser.add_argument(
        "--path", "-p",
        type=str,
        help="Specific path to scan (file or directory)",
    )
    parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="Scan the entire project (default: scan common build dirs only)",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="CI mode: exit with code 1 if any secrets are found",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress output except for findings",
    )
    parser.add_argument(
        "--max-findings",
        type=int,
        default=50,
        help="Maximum findings before stopping (default: 50)",
    )

    args = parser.parse_args()

    if args.path:
        scan_root = Path(args.path).resolve()
        if not scan_root.exists():
            print(f"Error: path '{args.path}' does not exist")
            return 1
        paths = [scan_root]
    elif args.all:
        paths = [Path(__file__).resolve().parent.parent]
    else:
        paths = get_default_scan_paths()

    all_findings: List[dict] = []
    for p in paths:
        if not args.quiet:
            print(f"Scanning: {p}")
        findings = scan_directory(p, quiet=args.quiet, max_findings=args.max_findings)
        all_findings.extend(findings)

    if not all_findings:
        if not args.quiet:
            print("\n\033[92mOK - No secrets detected.\033[0m")
        return 0

    print(f"\n{'='*60}")
    print(f"\033[91m!! SECRETS DETECTED: {len(all_findings)} finding(s)\033[0m")
    print(f"{'='*60}")

    pattern_counts = Counter(f["pattern"] for f in all_findings)
    for pattern, count in pattern_counts.most_common():
        print(f"  {pattern}: {count}")

    print(f"\nFiles with secrets:")
    file_counts = Counter(f["file"] for f in all_findings)
    for filepath, count in file_counts.most_common():
        print(f"  {filepath}: {count}")

    if args.ci:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
