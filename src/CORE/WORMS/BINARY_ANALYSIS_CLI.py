# coding: utf-8
"""
BINARY_ANALYSIS_CLI — CLI entry point for BINARY_ANALYSIS worm.

Extracted from BINARY_ANALYSIS.py to reduce import-time compilation cost.
The core worm class is in BINARY_ANALYSIS.py; this module contains only
the argparse CLI dispatch code (~550 lines).

Usage:
    python -m CORE.WORMS.BINARY_ANALYSIS_CLI nm target.exe
    python -m CORE.WORMS.BINARY_ANALYSIS_CLI pe-headers target.exe
"""
"""
BINARY_ANALYSIS_CLI — CLI entry point for BINARY_ANALYSIS worm.

Extracted from BINARY_ANALYSIS.py to reduce import-time compilation cost.
The core worm class is in BINARY_ANALYSIS.py; this module contains only
the argparse CLI dispatch code (~550 lines).

Usage:
    python -m CORE.WORMS.BINARY_ANALYSIS_CLI nm target.exe
    python -m CORE.WORMS.BINARY_ANALYSIS_CLI pe-headers target.exe
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from CORE.WORMS.BINARY_ANALYSIS import BinaryAnalysis
from typing import List
import json

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def main():
    import argparse
    parser = argparse.ArgumentParser(description="BINARY_ANALYSIS — Binary Analysis & RE Worm")
    sub = parser.add_subparsers(dest="command", help="Analysis command")

    # nm
    nm_p = sub.add_parser("nm", help="Extract symbols")
    nm_p.add_argument("path", help="Binary path")
    nm_p.add_argument("--demangle", action="store_true")

    # objdump
    od_p = sub.add_parser("objdump", help="Disassemble")
    od_p.add_argument("path", help="Binary path")
    od_p.add_argument("-s", "--section", help="Section name")
    od_p.add_argument("-n", "--max", type=int, default=500, help="Max instructions")

    # pe-headers
    pe_p = sub.add_parser("pe-headers", help="Parse PE headers")
    pe_p.add_argument("path", help="PE binary path")

    # elf-headers
    elf_p = sub.add_parser("elf-headers", help="Parse ELF headers")
    elf_p.add_argument("path", help="ELF binary path")

    # strings
    str_p = sub.add_parser("strings", help="Extract strings")
    str_p.add_argument("path", help="Binary path")
    str_p.add_argument("-n", "--min-length", type=int, default=4)
    str_p.add_argument("-m", "--max", type=int, default=200)

    # entropy
    ent_p = sub.add_parser("entropy", help="Calculate entropy")
    ent_p.add_argument("path", help="Binary path")

    # hexdump
    hex_p = sub.add_parser("hexdump", help="Hex dump")
    hex_p.add_argument("path", help="Binary path")
    hex_p.add_argument("-o", "--offset", type=lambda x: int(x, 0), default=0)
    hex_p.add_argument("-l", "--length", type=int, default=512)

    # sed
    sed_p = sub.add_parser("sed", help="Stream editor")
    sed_p.add_argument("pattern", help="Regex pattern")
    sed_p.add_argument("replacement", help="Replacement string")
    sed_p.add_argument("--file", "-f", help="Input file")
    sed_p.add_argument("--no-regex", action="store_true", help="Literal replacement")
    sed_p.add_argument("--case-insensitive", "-i", action="store_true")

    # awk
    awk_p = sub.add_parser("awk", help="Text processing")
    awk_p.add_argument("--file", "-f", help="Input file")
    awk_p.add_argument("-F", "--field-sep", default=r"\s+", help="Field separator regex")
    awk_p.add_argument("--fields", nargs="+", type=int, help="Fields to output (1-indexed)")
    awk_p.add_argument("--filter", help="Filter pattern")

    # analyze (full)
    full_p = sub.add_parser("analyze", help="Full binary analysis")
    full_p.add_argument("path", help="Binary path")

    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()
    ba = BinaryAnalysis()

    if not hasattr(args, "command") or not args.command:
        print(f"BinaryAnalysis v{ba.VERSION}")
        print("  Commands: nm, objdump, pe-headers, elf-headers, strings, entropy, hexdump, sed, awk, analyze")
    elif args.command == "nm":
        syms = ba.nm(args.path, demangle=args.demangle)
        if args.json:
            print(json.dumps([s.__dict__ for s in syms], indent=2))
        else:
            for s in syms[:800]:
                addr = f"{s.address:016X}" if s.address else "        U"
                print(f"{addr} {s.symbol_type} {s.name}")
    elif args.command == "objdump":
        result = ba.objdump(args.path, section=args.section, max_instructions=args.max)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for ins in result.get("instructions", [])[:50]:
                print(f"  {ins['address']}: {ins['bytes']:<30} {ins['instruction']}")
    elif args.command == "pe-headers":
        hdrs = ba.pe_headers(args.path)
        if args.json:
            print(json.dumps(hdrs.__dict__, indent=2, default=str))
        else:
            print(f"PE Headers: {hdrs.path}")
            print(f"  Machine: {hdrs.machine}")
            print(f"  Subsystem: {hdrs.subsystem}")
            print(f"  Entry: {hex(hdrs.entry_point)}")
            print(f"  Image Base: {hex(hdrs.image_base)}")
            print(f"  Sections: {len(hdrs.sections)}")
            for s in hdrs.sections:
                print(f"    {s.name:<10} VA=0x{s.virtual_address:08X} Size=0x{s.virtual_size:08X} Entropy={s.entropy}")
    elif args.command == "elf-headers":
        hdrs = ba.elf_headers(args.path)
        if args.json:
            print(json.dumps(hdrs.__dict__, indent=2, default=str))
        else:
            print(f"ELF Headers: {hdrs.path}")
            print(f"  Class: {hdrs.elf_class}")
            print(f"  Endianness: {hdrs.endianness}")
            print(f"  Entry: {hex(hdrs.entry_point)}")
    elif args.command == "strings":
        strs = ba.strings(args.path, min_length=args.min_length, max_strings=args.max)
        if args.json:
            print(json.dumps(strs, indent=2))
        else:
            for s in strs:
                print(s)
    elif args.command == "entropy":
        ent = ba.entropy(args.path)
        if args.json:
            print(json.dumps(ent.__dict__, indent=2, default=str))
        else:
            print(f"Entropy: {ent.path}")
            print(f"  Overall: {ent.overall_entropy}/8.0")
            print(f"  Packed: {'YES' if ent.is_packed else 'No'}")
            if ent.section_entropy:
                for sec, val in ent.section_entropy.items():
                    print(f"    {sec}: {val}")
    elif args.command == "hexdump":
        dump = ba.hexdump(args.path, offset=args.offset, length=args.length)
        print(dump)
    elif args.command == "sed":
        result = ba.sed(
            args.pattern, args.replacement,
            input_path=args.file, regex=not args.no_regex,
            case_insensitive=args.case_insensitive,
        )
        if args.json:
            print(json.dumps({"result": result[:10000]}))
        else:
            print(result[:10000])
    elif args.command == "awk":
        result = ba.awk(
            input_path=args.file,
            field_separator=args.field_sep,
            output_fields=args.fields,
            filter_pattern=args.filter,
        )
        if args.json:
            print(json.dumps({"result": result[:10000]}))
        else:
            print(result[:10000])
    elif args.command == "analyze":
        report = ba.analyze_binary(args.path)
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            print(f"=== Binary Analysis: {args.path} ===")
            print(f"  Type: {report.get('type', 'UNKNOWN')}")
            print(f"  Symbols: {report.get('symbols', 0)}")
            print(f"  Entropy: {report.get('entropy', 0)} {'(PACKED!)' if report.get('is_packed') else ''}")
            print(f"  Strings: {report.get('strings', {}).get('total', 0)}")
            print(f"  URLs found: {len(report.get('strings', {}).get('urls', []))}")
            if report.get("hex_preview"):
                print(f"\n  Hex Preview:\n{report['hex_preview']}")


if __name__ == "__main__":
    main()
