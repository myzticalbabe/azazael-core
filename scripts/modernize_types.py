#!/usr/bin/env python3
"""
Modernize Python type annotations from `typing` generics to native syntax.

Converts:
    Dict[K, V]  → dict[K, V]
    List[T]     → list[T]
    Set[T]      → set[T]
    Tuple[T, …] → tuple[T, …]
    FrozenSet[T] → frozenset[T]
    Type[T]     → type[T]
    Optional[T] → T | None
    Union[X, Y] → X | Y

Usage:
    python scripts/modernize_types.py              # dry-run
    python scripts/modernize_types.py --apply       # apply changes
    python scripts/modernize_types.py --path src/   # target specific dir
"""

import argparse
import ast
import re
import os
import sys
from pathlib import Path
from typing import List, Tuple, Set as TypingSet


# ── Types that have native equivalents ──────────────────────────────
NATIVE_TYPES = {
    "Dict": "dict",
    "List": "list",
    "Set": "set",
    "Tuple": "tuple",
    "FrozenSet": "frozenset",
    "Type": "type",
}

# Types that use pipe syntax (not subscriptable generics)
PIPE_TYPES = {"Optional", "Union"}

# Types without native generic syntax — stay in typing import
KEEP_IN_TYPING = {
    "Any", "Callable", "Generator", "AsyncGenerator", "Iterable",
    "Iterator", "Sequence", "Mapping", "MutableMapping", "MutableSet",
    "MutableSequence", "Iterable", "Iterator", "Reversible",
    "Collection", "Container", "Sized", "Hashable", "Iterable",
    "Awaitable", "Coroutine", "AsyncIterable", "AsyncIterator",
    "AsyncContextManager", "ContextManager",
    "TYPE_CHECKING", "Literal", "TypeVar", "Protocol",
    "Final", "ClassVar", "cast", "overload", "no_type_check",
    "NoReturn", "Never", "Self",
}

ALL_TYPING_NAMES = set(NATIVE_TYPES) | PIPE_TYPES | KEEP_IN_TYPING


# ── Bracket-matching helper ────────────────────────────────────────
def _find_bracket_end(text: str, start: int) -> int:
    """Find the closing bracket matching the opening at `start` (which must be '[')."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _split_top_level_commas(text: str) -> List[str]:
    """Split text on commas that are not inside nested brackets."""
    parts = []
    depth = 0
    current: List[str] = []
    for ch in text:
        if ch == "[":
            depth += 1
            current.append(ch)
        elif ch == "]":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return parts


def _replace_optional(text: str) -> str:
    """Replace Optional[T] with (T | None). Returns the text with replacements."""
    result = []
    i = 0
    while i < len(text):
        # Check for Optional[
        if text[i:i+9] == "Optional[":
            bracket_start = i + 8  # position of '['
            bracket_end = _find_bracket_end(text, bracket_start)
            if bracket_end > 0:
                inner = text[bracket_start + 1 : bracket_end]
                replacement = f"({inner} | None)"
                result.append(replacement)
                i = bracket_end + 1
                continue
        result.append(text[i])
        i += 1
    return "".join(result)


def _replace_union(text: str) -> str:
    """Replace Union[T, U, V] with T | U | V."""
    result = []
    i = 0
    while i < len(text):
        if text[i:i+6] == "Union[":
            bracket_start = i + 5
            bracket_end = _find_bracket_end(text, bracket_start)
            if bracket_end > 0:
                inner = text[bracket_start + 1 : bracket_end]
                parts = _split_top_level_commas(inner)
                replacement = " | ".join(parts)
                result.append(replacement)
                i = bracket_end + 1
                continue
        result.append(text[i])
        i += 1
    return "".join(result)


def _replace_native_subscript(text: str, type_map: dict) -> str:
    """Replace Dict[K,V], List[T], Set[T], Tuple[T,...], FrozenSet[T], Type[T]."""
    # Process from longest to shortest name to avoid partial matches
    sorted_types = sorted(type_map.keys(), key=len, reverse=True)
    result = []
    i = 0
    while i < len(text):
        matched = False
        for typing_name in sorted_types:
            native_name = type_map[typing_name]
            n = len(typing_name)
            if (text[i:i+n] == typing_name and
                i + n < len(text) and text[i + n] == "["):
                # Verify it's not part of a longer identifier
                if i > 0 and (text[i-1].isalnum() or text[i-1] == '_'):
                    continue
                bracket_start = i + n  # position of '['
                bracket_end = _find_bracket_end(text, bracket_start)
                if bracket_end > 0:
                    inner = text[bracket_start + 1 : bracket_end]
                    # For Tuple, strip trailing comma
                    if typing_name == "Tuple":
                        # Handle empty tuple
                        if inner.strip() == "":
                            replacement = "tuple[()]"
                        elif inner.strip() == "...":
                            replacement = "tuple[...]"
                        elif inner.strip().endswith(", ..."):
                            base = inner.strip()[:-4].rstrip(",").strip()
                            replacement = f"tuple[{base}, ...]"
                        elif inner.strip().endswith("..."):
                            replacement = f"tuple[{inner.strip()}]"
                        else:
                            replacement = f"tuple[{inner}]"
                    elif typing_name == "FrozenSet":
                        replacement = f"frozenset[{inner}]"
                    elif typing_name == "Type":
                        replacement = f"type[{inner}]"
                    else:
                        replacement = f"{native_name}[{inner}]"
                    result.append(replacement)
                    i = bracket_end + 1
                    matched = True
                    break
        if not matched:
            result.append(text[i])
            i += 1
    return "".join(result)


def _replace_callable(text: str) -> str:
    """
    Handle Callable[[Args], Ret] — make sure brackets stay balanced.
    Not converting to native (no native Callable syntax).
    """
    return text


def modernize_types_in_text(text: str) -> Tuple[str, bool]:
    """
    Modernize type annotations in a single file's text.
    Returns (new_text, was_modified).
    """
    original = text

    # Apply replacements iteratively (multiple passes) to handle nested types
    # like Dict[str, List[str]] → dict[str, list[str]]
    for _ in range(10):  # max 10 passes should be enough for any nesting depth
        prev = text
        # 1. Replace subscript native types: Dict, List, Set, Tuple, FrozenSet, Type
        text = _replace_native_subscript(text, NATIVE_TYPES)
        
        # 2. Replace Union[T, U] → T | U
        text = _replace_union(text)
        
        # 3. Replace Optional[T] → T | None
        text = _replace_optional(text)
        
        if text == prev:
            break

    # 4. Clean up typing imports (one pass only, since import names don't nest)
    text = _clean_imports(text)

    return text, text != original


def _clean_imports(text: str) -> str:
    """Remove unneeded names from `from typing import ...` lines."""
    lines = text.split("\n")
    new_lines = []
    in_multi_import = False
    import_line_parts: List[str] = []

    for line in lines:
        stripped = line.strip()

        # Detect start of a multi-line import
        if "from typing import" in stripped and "(" in stripped:
            in_multi_import = True
            # Extract the part after '('
            before = line[: stripped.index("(") + 1]
            paren_idx = stripped.index("(")
            after_paren = stripped[paren_idx + 1:].strip().rstrip(",")
            import_line_parts = [before]
            if after_paren:
                # Get names from this line
                names = [n.strip() for n in after_paren.split(",") if n.strip()]
                import_line_parts.extend(names)
            continue

        if in_multi_import:
            # Check if this line closes the import
            if ")" in stripped:
                in_multi_import = False
                # Get names before ')'
                idx = stripped.index(")")
                part = stripped[:idx].strip().rstrip(",")
                names = [n.strip() for n in part.split(",") if n.strip()]
                import_line_parts.extend(names)

                # Now filter the names
                filtered = _filter_typing_names(import_line_parts[1:])  # skip the 'from ... ('

                if not filtered:
                    # Remove entire import statement — skip the lines
                    # But we need to handle the case where we haven't appended anything yet
                    parts_before_paren = import_line_parts[0]
                    new_lines.append(f"# {parts_before_paren}  # removed — all names have native equivalents")
                    continue

                # Rebuild the import
                indent = " " * (len(import_line_parts[0]) - len(import_line_parts[0].lstrip()))
                if len(filtered) == 1:
                    new_lines.append(f"{indent}from typing import {filtered[0]}")
                else:
                    items = ",\n".join(f"{indent}    {n}" for n in filtered)
                    new_lines.append(f"{indent}from typing import (\n{items}\n{indent})")
                import_line_parts = []
                continue

            # Middle of multi-line import
            names = [n.strip() for n in stripped.split(",") if n.strip()]
            import_line_parts.extend(names)
            continue

        # Single-line import
        if stripped.startswith("from typing import") and "(" not in stripped:
            prefix = line[: line.index("from")]
            import_part = stripped[len("from typing import"):].strip()
            names = [n.strip() for n in import_part.split(",") if n.strip()]
            filtered = _filter_typing_names(names)

            if not filtered:
                # Remove entire line
                new_lines.append(f"{prefix}# {stripped}  # removed — all names have native equivalents")
                continue

            if len(filtered) == 1:
                new_lines.append(f"{prefix}from typing import {filtered[0]}")
            else:
                new_lines.append(f"{prefix}from typing import {', '.join(filtered)}")
            continue

        new_lines.append(line)

    return "\n".join(new_lines)


def _filter_typing_names(names: List[str]) -> List[str]:
    """Filter out names that have native equivalents, keeping only those that stay in typing."""
    result = []
    for name in names:
        # Strip any comments after name
        clean_name = name.split("  #")[0].split("#")[0].strip()

        # Handle 'as' aliases
        if " as " in clean_name:
            parts = clean_name.split(" as ")
            clean_name = parts[0].strip()
            alias = parts[1].strip()
        else:
            alias = None

        base_name = clean_name.rstrip(",").strip()

        # Remove if it has a native equivalent or pipe equivalent
        if base_name in NATIVE_TYPES or base_name in PIPE_TYPES:
            continue  # Skip — has native equivalent

        if base_name in KEEP_IN_TYPING:
            if alias:
                result.append(f"{base_name} as {alias}")
            else:
                result.append(base_name)
            continue

        # If it's not in any of our sets, keep it (could be something else)
        if alias:
            result.append(f"{base_name} as {alias}")
        else:
            result.append(base_name)

    return result


def should_process(path: Path) -> bool:
    """Check if a file should be processed."""
    # Skip archived directories
    if "archived" in str(path):
        return False
    # Skip __pycache__
    if "__pycache__" in str(path):
        return False
    # Skip output directories
    if "/output/" in str(path):
        return False
    # Skip .git
    if "/.git/" in str(path):
        return False
    # Skip venv
    if "/.venv" in str(path) or "venv" in str(path).split(os.sep):
        return False
    # Only .py files
    if path.suffix != ".py":
        return False
    return True


def process_file(path: Path, apply: bool = False) -> Tuple[str, bool, str]:
    """Process a single file. Returns (filepath, modified, error_message_or_empty)."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return str(path), False, f"read error: {e}"

    # Quick check — does it import from typing?
    if "from typing import" not in text:
        return str(path), False, ""

    new_text, modified = modernize_types_in_text(text)
    if not modified:
        return str(path), False, ""

    if apply:
        path.write_text(new_text, encoding="utf-8")
        try:
            compile(new_text, str(path), "exec")
            return str(path), True, ""
        except SyntaxError as e:
            return str(path), True, f"WARNING: pre-existing syntax error in {path.name}: {e}"
    else:
        return str(path), True, ""


def main():
    parser = argparse.ArgumentParser(description="Modernize typing imports to native generics")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")
    parser.add_argument("--path", default="src", help="Root path to process")
    parser.add_argument("--ext", default="tests", help="Also process tests directory")
    args = parser.parse_args()

    root = Path(args.path)
    if not root.exists():
        print(f"Error: {root} does not exist")
        sys.exit(1)

    files: List[Path] = []
    if root.is_dir():
        for f in root.rglob("*.py"):
            if should_process(f):
                files.append(f)
    else:
        files.append(root)

    # If we're processing src/, also check tests/
    if args.path == "src" and args.ext:
        tests_root = Path(args.ext)
        if tests_root.exists():
            for f in tests_root.rglob("*.py"):
                if should_process(f):
                    files.append(f)

    files.sort()
    print(f"Scanning {len(files)} files...\n")

    modified_count = 0
    error_count = 0
    unchanged_count = 0
    stats = {}  # type_name -> count

    for fpath in files:
        rel = fpath.relative_to(Path.cwd()) if fpath.is_relative_to(Path.cwd()) else fpath
        filepath, modified, error = process_file(fpath, apply=args.apply)
        
        if error:
            if error != "":
                print(f"  ERROR  {filepath}: {error}")
                error_count += 1
            else:
                unchanged_count += 1
        elif modified:
            print(f"  {'APPLIED' if args.apply else 'WOULD'}  {filepath}")
            modified_count += 1
        else:
            unchanged_count += 1

    print(f"\n{'─' * 60}")
    print(f"  Mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"  Files scanned: {len(files)}")
    print(f"  Files modified: {modified_count}")
    print(f"  Files unchanged: {unchanged_count}")
    print(f"  Errors: {error_count}")
    print(f"{'─' * 60}")


if __name__ == "__main__":
    main()
