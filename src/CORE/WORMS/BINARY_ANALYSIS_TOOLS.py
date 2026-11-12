# coding: utf-8
"""
BINARY_ANALYSIS_TOOLS — Text Processing & Utility Functions

Extracted from BINARY_ANALYSIS.py for modularity.
Provides text processing (sed, awk), disassembly (objdump),
string extraction, entropy analysis, hexdump, and memory map analysis.

Usage:
    >>> from CORE.WORMS.BINARY_ANALYSIS_TOOLS import extract_strings, run_sed, run_awk
"""

import sys
import re
import math
import subprocess
import threading
from pathlib import Path
from typing import Any, List
from collections import Counter

from CORE.WORMS.BINARY_ANALYSIS_TYPES import (
    EntropyResult, _get_pams, IS_WINDOWS, IS_LINUX,
)

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


# -- Helpers -----------------------------------------------------------

_LOCK = threading.Lock()


def _record(event_type: str, data: dict[str, Any], severity: str = "info"):
    """Record event to PAMS memory (standalone helper). Thread-safe via module lock."""
    with _LOCK:
        pams = _get_pams()
        if pams:
            try:
                pams.record_event(
                    event_type=f"binanalysis.{event_type}",
                    data=data, source="BinaryAnalysis", severity=severity,
                )
            except Exception as exc:
                note_failure("BINARY_ANALYSIS_TOOLS:L42", exc)


# -- objdump (Disassembly) --------------------------------------------

def disassemble_binary(
    path: str,
    section: (str | None) = None,
    max_instructions: int = 500,
) -> dict[str, Any]:
    """Disassemble a binary section.

    On Windows: uses dumpbin /DISASM
    On Linux: uses objdump -d
    """
    result = {
        "path": path,
        "section": section or "all",
        "instructions": [],
        "sections_found": [],
        "platform": sys.platform,
    }

    full_path = Path(path)
    if not full_path.exists():
        return {"error": "File not found"}

    if IS_WINDOWS:
        result = _objdump_windows(full_path, section, max_instructions)
    else:
        result = _objdump_linux(full_path, section, max_instructions)

    _record("objdump", {
        "path": str(full_path),
        "section": section,
        "instructions_found": len(result.get("instructions", [])),
    })

    return result


def _objdump_windows(path: Path, section: str | None, max_ins: int) -> dict[str, Any]:
    result = {"path": str(path), "section": section or "all", "instructions": [], "sections_found": []}
    try:
        args = ["dumpbin", "/DISASM", str(path)]
        if section:
            args.extend([f"/SECTION:{section}"])

        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)

        current_section = ""
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue

            if "SECTION HEADER" in line:
                try:
                    current_section = line.split("#")[1].strip().split()[0]
                    if current_section not in result["sections_found"]:
                        result["sections_found"].append(current_section)
                except IndexError as exc:
                    note_failure("BINARY_ANALYSIS_TOOLS:L104", exc)

            if ":" in line and len(line.split(":", 1)[0].strip()) >= 8:
                try:
                    addr_part, rest = line.split(":", 1)
                    addr = addr_part.strip()
                    asm_part = rest.strip()
                    byte_match = re.match(r"^((?:[0-9A-Fa-f]{2} )+)", asm_part)
                    if byte_match:
                        bytes_str = byte_match.group(1).strip()
                        instruction = asm_part[len(byte_match.group(0)):].strip()
                    else:
                        bytes_str = ""
                        instruction = asm_part

                    result["instructions"].append({
                        "address": addr,
                        "bytes": bytes_str,
                        "instruction": instruction,
                        "section": current_section,
                    })
                except Exception as exc:
                    note_failure("BINARY_ANALYSIS_TOOLS:L126", exc)

            if len(result["instructions"]) >= max_ins:
                break

    except FileNotFoundError as exc:
        note_failure("BINARY_ANALYSIS_TOOLS:L132", exc)
        result["error"] = "dumpbin not found. Install Visual Studio Build Tools."
    except Exception as e:
        note_failure("BINARY_ANALYSIS_TOOLS:L134", e)
        result["error"] = str(e)

    return result


def _objdump_linux(path: Path, section: str | None, max_ins: int) -> dict[str, Any]:
    result = {"path": str(path), "section": section or "all", "instructions": [], "sections_found": []}
    try:
        args = ["objdump", "-d"]
        if section:
            args.extend(["-j", section])
        args.append(str(path))

        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)

        current_section = ""
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue

            if "Disassembly of section" in line:
                current_section = line.split("section")[1].strip().rstrip(":")
                if current_section not in result["sections_found"]:
                    result["sections_found"].append(current_section)

            if ":" in line and len(line.split(":", 1)[0].strip()) >= 6:
                try:
                    addr_part, rest = line.split(":", 1)
                    addr = addr_part.strip()
                    tab_split = rest.split("\t")
                    if len(tab_split) >= 2:
                        bytes_str = tab_split[0].strip()
                        instruction = "\t".join(tab_split[1:]).strip()
                    else:
                        bytes_str = ""
                        instruction = rest.strip()

                    result["instructions"].append({
                        "address": addr,
                        "bytes": bytes_str,
                        "instruction": instruction,
                        "section": current_section,
                    })
                except Exception as exc:
                    note_failure("BINARY_ANALYSIS_TOOLS:L179", exc)

            if len(result["instructions"]) >= max_ins:
                break

    except FileNotFoundError as exc:
        note_failure("BINARY_ANALYSIS_TOOLS:L185", exc)
        result["error"] = "objdump not found. Install binutils."
    except Exception as e:
        note_failure("BINARY_ANALYSIS_TOOLS:L187", e)
        result["error"] = str(e)

    return result


# -- Strings ----------------------------------------------------------

def extract_strings(
    path: str,
    min_length: int = 4,
    encoding: str = "ascii",
    max_strings: int = 200,
) -> list[str]:
    """Extract printable strings from a binary file."""
    results = []
    full_path = Path(path)

    if not full_path.exists():
        return results

    try:
        with open(full_path, "rb") as f:
            data = f.read(10 * 1024 * 1024)

        current = b""
        for byte in data:
            if 32 <= byte <= 126:
                current += bytes([byte])
            else:
                if len(current) >= min_length:
                    results.append(current.decode("ascii", errors="replace"))
                current = b""
        if len(current) >= min_length:
            results.append(current.decode("ascii", errors="replace"))

    except Exception as e:
        _record("strings_error", {"path": str(full_path), "error": str(e)}, severity="error")

    if not results or IS_LINUX:
        try:
            proc = subprocess.run(
                ["strings", "-n", str(min_length), str(full_path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            for line in proc.stdout.splitlines():
                if line not in results:
                    results.append(line)
        except Exception as exc:
            note_failure("BINARY_ANALYSIS_TOOLS:L235", exc)

    _record("strings", {
        "path": str(full_path), "strings_found": len(results), "min_length": min_length,
    })

    return results[:max_strings]


# -- Entropy ----------------------------------------------------------

def _shannon_entropy(data: bytes) -> float:
    """Calculate Shannon entropy of byte data."""
    if not data:
        return 0.0
    counter = Counter(data)
    total = len(data)
    entropy = 0.0
    for count in counter.values():
        if count > 0:
            p = count / total
            entropy -= p * math.log2(p)
    return entropy


def analyze_entropy(path: str) -> EntropyResult:
    """Calculate byte entropy for packing/malware detection."""
    full_path = Path(path)
    result = EntropyResult(
        path=str(full_path),
        overall_entropy=0.0,
        max_possible=8.0,
        is_packed=False,
    )

    if not full_path.exists():
        return result

    try:
        with open(full_path, "rb") as f:
            data = f.read(5 * 1024 * 1024)

        result.overall_entropy = round(_shannon_entropy(data), 4)
        result.is_packed = result.overall_entropy > 7.0

        # Per-section entropy if PE
        from CORE.WORMS.BINARY_ANALYSIS_PARSE import parse_pe_headers
        pe = parse_pe_headers(str(full_path))
        if pe.is_pe:
            with open(full_path, "rb") as f:
                for sec in pe.sections:
                    if sec.raw_size > 0:
                        f.seek(sec.raw_offset)
                        sec_data = f.read(min(sec.raw_size, 65536))
                        result.section_entropy[sec.name] = round(_shannon_entropy(sec_data), 4)

    except Exception as e:
        _record("entropy_error", {"path": str(full_path), "error": str(e)}, severity="error")

    _record("entropy", {
        "path": str(full_path),
        "entropy": result.overall_entropy,
        "is_packed": result.is_packed,
    })
    return result


# -- mapfile (Memory Map Analysis) ------------------------------------

def analyze_memory_maps(pid: (int | None) = None) -> dict[int, list[dict[str, Any]]]:
    """Analyze memory maps for a process (or all processes)."""
    import psutil
    result = {}

    processes = []
    if pid:
        try:
            processes = [psutil.Process(pid)]
        except psutil.NoSuchProcess as exc:
            note_failure("BINARY_ANALYSIS_TOOLS:L314", exc)
            return {}
    else:
        try:
            processes = list(psutil.process_iter())[:50]
        except Exception as exc:
            note_failure("BINARY_ANALYSIS_TOOLS:L319", exc)

    for proc in processes:
        mappings = []
        try:
            for mmap in proc.memory_maps():
                mappings.append({
                    "path": mmap.path,
                    "rss_kb": mmap.rss // 1024,
                    "size_kb": (mmap.rss // 1024) if hasattr(mmap, 'rss') else 0,
                    "perms": "rwx",
                })
        except (psutil.AccessDenied, psutil.NoSuchProcess) as exc:
            note_failure("BINARY_ANALYSIS_TOOLS:L332", exc)

        if mappings:
            result[proc.pid] = mappings

    _record("mapfile", {
        "processes_analyzed": len(processes),
        "processes_with_maps": len(result),
        "total_regions": sum(len(v) for v in result.values()),
    })
    return result


# -- hexdump ----------------------------------------------------------

def generate_hexdump(
    path: str,
    offset: int = 0,
    length: int = 512,
    bytes_per_line: int = 16,
) -> str:
    """Produce a hex dump with ASCII sidebar."""
    full_path = Path(path)
    if not full_path.exists():
        return "File not found"

    lines = []
    try:
        with open(full_path, "rb") as f:
            f.seek(offset)
            data = f.read(length)

        for i in range(0, len(data), bytes_per_line):
            chunk = data[i:i + bytes_per_line]
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            hex_part = hex_part.ljust(bytes_per_line * 3)

            ascii_part = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)

            lines.append(f"{offset + i:08X}  {hex_part} |{ascii_part}|")

    except Exception as e:
        note_failure("BINARY_ANALYSIS_TOOLS:L374", e)
        return f"Error: {e}"

    return "\n".join(lines)


# -- sed (Stream Editor) ---------------------------------------------

def run_sed(
    pattern: str,
    replacement: str,
    input_path: (str | None) = None,
    text: (str | None) = None,
    regex: bool = True,
    global_replace: bool = True,
    case_insensitive: bool = False,
) -> str:
    """Stream editor: regex or literal find/replace.

    Mimics `sed 's/pattern/replacement/g'` behavior.
    """
    if input_path:
        try:
            with open(input_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception as e:
            note_failure("BINARY_ANALYSIS_TOOLS:L399", e)
            return f"Error reading file: {e}"

    if not text:
        return ""

    flags = 0
    if case_insensitive:
        flags |= re.IGNORECASE

    if regex:
        count = 0 if global_replace else 1
        result = re.sub(pattern, replacement, text, count=count, flags=flags)
    else:
        if case_insensitive:
            result = re.sub(re.escape(pattern), replacement, text, flags=re.IGNORECASE)
        else:
            result = text.replace(pattern, replacement)

    if regex:
        replacement_count = len(re.findall(pattern, text, flags))
    elif case_insensitive:
        replacement_count = len(re.findall(re.escape(pattern), text, re.IGNORECASE))
    else:
        replacement_count = text.count(pattern)

    _record("sed", {"input": input_path or "<text>", "pattern": pattern,
                     "replacements": replacement_count})
    return result


# -- awk (Text Processing) -------------------------------------------

def run_awk(
    input_path: (str | None) = None,
    text: (str | None) = None,
    field_separator: str = r"\s+",
    output_fields: (list[int] | None) = None,
    filter_pattern: (str | None) = None,
    output_separator: str = "\t",
) -> str:
    """Text processing: column extraction, filtering, aggregation.

    Mimics `awk '{print $1, $3}'` behavior for column-based text.
    """
    if input_path:
        try:
            with open(input_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception as e:
            note_failure("BINARY_ANALYSIS_TOOLS:L448", e)
            return f"Error reading file: {e}"

    if not text:
        return ""

    lines_out = []
    for line in text.splitlines():
        if filter_pattern:
            if not re.search(filter_pattern, line):
                continue

        fields = re.split(field_separator, line.strip())
        fields = [f for f in fields if f]

        if output_fields:
            selected = []
            for idx in output_fields:
                if idx > 0:
                    if idx <= len(fields):
                        selected.append(fields[idx - 1])
                    else:
                        selected.append("")
                else:
                    try:
                        selected.append(fields[idx])
                    except IndexError as exc:
                        note_failure("BINARY_ANALYSIS_TOOLS:L474", exc)
                        selected.append("")
            lines_out.append(output_separator.join(selected))
        else:
            lines_out.append(output_separator.join(fields))

    result = "\n".join(lines_out)

    _record("awk", {"input": input_path or "<text>", "lines_processed": len(lines_out)})
    return result
