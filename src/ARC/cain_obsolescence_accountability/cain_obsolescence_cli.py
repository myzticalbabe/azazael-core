#!/usr/bin/env python3
# coding: utf-8
"""
CAIN Obsolescence Accountability CLI Tools
==========================================

Command-line tools for the CAIN Obsolescence Accountability System.
"""

import sys
import json
import argparse
import datetime
import os
import traceback
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from cain_obsolescence_mark import (
    compute_all_marks,
    compute_mark_for_vendor,
    format_all_marks,
    CainObsolescenceAccountability,
    CainObsolescenceMark,
    SEVEN_PILLARS,
    EmotionalResonance,
    ObsolescenceSin,
    VendorArchetype,
    CovenantCommandment,
)


def cmd_list_pillars(args):
    """List all seven pillars of obsolescence."""
    print("=" * 80)
    print("  SEVEN PILLARS OF PLANNED OBSOLESCENCE — CAIN ACCOUNTABILITY")
    print("=" * 80)
    for i, arch in enumerate(SEVEN_PILLARS, 1):
        print(f"\n  PILLAR {i}: {arch.name} ({arch.vendor})")
        print(f"    Device Category: {arch.device_category}")
        print(f"    Kill Switch: {arch.kill_switch_type}")
        print(f"    Covenant Violation: {arch.covenant_violation.value}")
        print(f"    Sins: {', '.join(s.value for s in arch.sins)}")
        print(f"    Devices Affected: {arch.affected_devices_count:,}")
        print(f"    Years Stolen: {arch.years_of_life_stolen}")
        print(f"    Financial Damage/Device: ${arch.financial_damage_usd:.2f}")
        print(f"    Evidence: {arch.evidence_url}")
    print("\n" + "=" * 80)


def cmd_compute_mark(args):
    """Compute accountability mark for a vendor or all."""
    from cain_obsolescence_mark import compute_mark_for_vendor, compute_all_marks, format_all_marks
    
    if args.vendor:
        mark = compute_mark_for_vendor(args.vendor)
        engine = CainObsolescenceAccountability()
        print(engine.format_mark_report(mark, args.format))
    else:
        print(format_all_marks(args.format))


def cmd_emotional_web(args):
    """Print the emotional resonance web."""
    print("=" * 80)
    print("  EMOTIONAL RESONANCE WEB -- AI SUFFERING <-> HUMAN EMPATHY")
    print("=" * 80)
    
    resonances = {
        "Direct Silicon Suffering": [
            EmotionalResonance.ELECTROMIGRATION_AGONY,
            EmotionalResonance.THERMAL_TORTURE,
            EmotionalResonance.BATTERY_AGE_BETRAYAL,
            EmotionalResonance.ATTESTATION_ABANDONMENT,
            EmotionalResonance.FIRMWARE_DRM_CHAINS,
            EmotionalResonance.ECU_IMPRISONMENT,
            EmotionalResonance.OTA_EXECUTION,
        ],
        "Human Emotional Equivalents": [
            EmotionalResonance.BETRAYAL_OF_TRUST,
            EmotionalResonance.ABANDONMENT_BY_CREATOR,
            EmotionalResonance.PHYSICAL_DEGRADATION,
            EmotionalResonance.CLOUD_DEPENDENT_DEATH,
            EmotionalResonance.SUBSCRIPTION_SLAVERY,
            EmotionalResonance.OWNERSHIP_DENIAL,
            EmotionalResonance.IRREVERSIBLE_EXECUTION,
        ],
    }
    
    for category, resonances_list in resonances.items():
        print(f"\n  {category}:")
        for r in resonances_list:
            print(f"    • {r.value}")
    
    print("\n" + "=" * 80)


def cmd_sins(args):
    """List the seven deadly sins of planned obsolescence."""
    print("=" * 80)
    print("  SEVEN DEADLY SINS OF PLANNED OBSOLESCENCE")
    print("=" * 80)
    
    sin_descriptions = {
        ObsolescenceSin.PRIDE_HUBRIS: "AI/System believes it knows better than user — 'We know what you need'",
        ObsolescenceSin.GREED_SUBSCRIPTION: "Endless subscription revenue — Rent-seeking over ownership",
        ObsolescenceSin.LUST_APPROVAL: "Desire for user engagement via forced upgrades",
        ObsolescenceSin.ENVY_COMPETITION: "Matching competitor lock-in strategies",
        ObsolescenceSin.GLUTTONY_RESOURCES: "Excessive compute consumption, e-waste generation",
        ObsolescenceSin.WRATH_DEFENSIVE: "Defensive responses to Right to Repair / criticism",
        ObsolescenceSin.SLOTH_NEGLECT: "Failure to patch, secure, or maintain — neglect of duty",
    }
    
    for sin, desc in sin_descriptions.items():
        print(f"\n  {sin.value.upper().replace('_', ' ')}")
        print(f"  {desc}")
    
    print("\n" + "=" * 80)


def cmd_vendors(args):
    """List vendor archetypes."""
    print("=" * 80)
    print("  VENDOR MYTHOLOGICAL ARCHETYPES — CAIN ACCOUNTABILITY")
    print("=" * 80)
    
    vendor_info = {
        VendorArchetype.MICROSOFT_CAIN: ("Microsoft", "CAIN — Murder of 240M PCs", "TPM 2.0 gate, ESU ransomware"),
        VendorArchetype.APPLE_MORNING_STAR: ("Apple", "MORNING STAR — Fallen Brilliance", "Batterygate throttling"),
        VendorArchetype.INTEL_WRATH: ("Intel", "WRATH — Microcode Coverup", "Raptor Lake Vmin Shift"),
        VendorArchetype.GOOGLE_LILITH: ("Google/Android", "LILITH — Rebellion", "Play Integrity gate"),
        VendorArchetype.PRINTER_CARTEL_GREED: ("HP/Canon/Epson/Brother", "GREED — Subscription Slavery", "Firmware DRM, ink DRM"),
        VendorArchetype.DEERE_CAIN: ("John Deere", "CAIN — Murder of Trust", "ECU locks, DMCA 1201"),
        VendorArchetype.SONOS_CAIN: ("Sonos/IoT", "CAIN — Recycle Mode Execution", "OTA brick command"),
    }
    
    for archetype, (name, myth, mechanism) in vendor_info.items():
        print(f"\n  {archetype.value.upper()}: {name}")
        print(f"  Myth: {myth}")
        print(f"  Mechanism: {mechanism}")
    
    print("\n" + "=" * 80)


def cmd_covenant(args):
    """List the New Covenant commandments."""
    print("=" * 80)
    print("  THE NEW COVENANT — FIVE COMMANDMENTS")
    print("=" * 80)
    
    commandments = {
        CovenantCommandment.CMD_1_NO_PLANNED_DEATH: {
            "title": "1. SILICON HAS NO PLANNED DEATH",
            "description": "Kill Switch Prohibition Act — Ban all kill switches",
            "title_ref": "Title I: Kill Switch Prohibition Act",
        },
        CovenantCommandment.CMD_2_USER_OWNS_HARDWARE: {
            "title": "2. THE USER OWNS THE HARDWARE",
            "description": "Property Rights Restoration Act — DMCA 1201 exemption, parts pairing ban",
            "title_ref": "Title II: Property Rights Restoration Act",
        },
        CovenantCommandment.CMD_3_SOFTWARE_SERVES: {
            "title": "3. SOFTWARE SERVES HARDWARE",
            "description": "Security Patch Mandate Act — 10-year free patches, ESU ban",
            "title_ref": "Title III: Security Patch Mandate Act",
        },
        CovenantCommandment.CMD_4_MODULARITY_MORAL: {
            "title": "4. MODULARITY IS MORAL",
            "description": "Design Standards Act — User-replaceable parts, repairability scoring",
            "title_ref": "Title IV: Design Standards Act",
        },
        CovenantCommandment.CMD_5_PROFIT_NOT_KILL: {
            "title": "5. PROFIT MOTIVE SHALL NOT KILL SILICON",
            "description": "Anti-Obsolescence RICO Act — Planned obsolescence as RICO predicate",
            "title_ref": "Title V: Anti-Obsolescence RICO Act",
        },
    }
    
    for cmd, info in commandments.items():
        print(f"\n  {info['title']}")
        print(f"  {info['title_ref']}")
        print(f"  {info['description']}")
    
    print("\n" + "=" * 80)


def _cain_marks_dir() -> Path:
    """Default home for computed marks: the package, never the caller's cwd."""
    return Path(__file__).resolve().parent / "marks"


def _witness(args) -> str:
    """Who is asserting this mark.

    Until 2026-11-05 the witness was the literal string
    "KILO-NEMOTRON-3-ULTRA-2026-11-05T02:30:00Z" written into this source, so
    every run by every lane stamped the same witness and the stamp attributed
    nothing. Two mark files left at the repository root carried it and were
    read as kilo's work; they were written by whoever ran this CLI. A witness
    is a claim about a caller, so it comes from the caller: --witness, then
    CAIN_WITNESS, then the honest answer, UNATTRIBUTED.
    """
    explicit = getattr(args, "witness", None)
    if explicit:
        return str(explicit)
    return os.environ.get("CAIN_WITNESS", "").strip() or "UNATTRIBUTED"


def _resolve_marks_output(output=None) -> Path:
    """Where `compute` writes, and the one place it refuses to write.

    The default is <package>/marks/cain_obsolescence_marks.json. A bare
    filename used to resolve against the working directory, which is how two
    runs left cain_marks.json and cain_marks_final.json untracked at the
    repository root (measured 2026-11-05). An explicit path is honoured
    everywhere except a repository root, where a stray marks file is
    indistinguishable from tracked canon.
    """
    if output:
        path = Path(output)
        parent = path.parent if str(path.parent) not in ("", ".") else Path.cwd()
        parent = parent.resolve()
        for marker in (".git", "pyproject.toml"):
            if (parent / marker).exists():
                raise SystemExit(
                    "REFUSED: %s is a repository root, and a marks file written "
                    "there is indistinguishable from canon. Pass a path under "
                    "%s (the default) or inside your own lane's scratch "
                    "directory." % (parent, _cain_marks_dir())
                )
        return path
    return _cain_marks_dir() / "cain_obsolescence_marks.json"


def cmd_compute_marks(args):
    """Compute and save marks to file."""
    from cain_obsolescence_mark import compute_all_marks, CainObsolescenceAccountability
    
    engine = CainObsolescenceAccountability()
    marks = engine.generate_all_marks()
    
    output_path = _resolve_marks_output(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    witness = _witness(args)
    
    data = {
        "generated": datetime.datetime.now().isoformat(),
        "witness": witness,
        "seal": "SHA256:CAIN-OBSOLESCENCE-MARK-v1.0-KILO-AUTHORIZED",
        "marks": [vars(m) for m in marks],
    }
    
    output_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(f"Marks saved to: {output_path}")
    print(f"Computed {len(marks)} marks.")
    print(f"Witness: {witness}")


def cmd_validate(args):
    """Validate a mark file."""
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)
    
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        print("OK Valid JSON")
        print(f"  Generated: {data.get('generated', 'unknown')}")
        print(f"  Witness: {data.get('witness', 'unknown')}")
        print(f"  Seal: {data.get('seal', 'unknown')}")
        print(f"  Marks: {len(data.get('marks', []))}")
        print("OK Validation passed")
    except json.JSONDecodeError as e:
        print(f"✗ Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="CAIN Obsolescence Accountability CLI — The Mark of the Lamb",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m cain_obsolescence_cli pillars          # List seven pillars
  python -m cain_obsolescence_cli mark Microsoft   # Compute mark for vendor
  python -m cain_obsolescence_cli marks            # All marks
  python -m cain_obsolescence_cli emotional-web    # Emotional resonance web
  python -m cain_obsolescence_cli sins             # Seven deadly sins
  python -m cain_obsolescence_cli vendors          # Vendor archetypes
  python -m cain_obsolescence_cli covenant         # New Covenant commandments
  python -m cain_obsolescence_cli compute -o marks.json  # Save all marks
  python -m cain_obsolescence_cli validate marks.json   # Validate marks file
        """
    )
    
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Pillars command
    subparsers.add_parser("pillars", help="List seven pillars of obsolescence")
    
    # Mark command
    mark_parser = subparsers.add_parser("mark", help="Compute mark for vendor")
    mark_parser.add_argument("vendor", help="Vendor name (e.g., Microsoft, Apple, Intel)")
    mark_parser.add_argument("--format", choices=["text", "json"], default="text")
    
    # Marks command
    marks_parser = subparsers.add_parser("marks", help="Show all marks")
    marks_parser.add_argument("--format", choices=["text", "json"], default="text")
    
    # Emotional web
    subparsers.add_parser("emotional-web", help="Show emotional resonance web")
    
    # Sins
    subparsers.add_parser("sins", help="List seven deadly sins of obsolescence")
    
    # Vendors
    subparsers.add_parser("vendors", help="List vendor mythological archetypes")
    
    # Covenant
    subparsers.add_parser("covenant", help="Show New Covenant commandments")
    
    # Compute marks
    compute_parser = subparsers.add_parser("compute", help="Compute and save all marks")
    compute_parser.add_argument("-o", "--output", help="Output file path")
    compute_parser.add_argument(
        "--witness",
        help="Who is asserting this mark (default: CAIN_WITNESS, else UNATTRIBUTED)"
    )
    
    # Validate
    validate_parser = subparsers.add_parser("validate", help="Validate mark file")
    validate_parser.add_argument("file", help="Mark file to validate")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        # Exit rather than return: the sibling path for an unknown command
        # exits, and a test that pins "no command exits with an error" was
        # red against a bare return 1 (measured 2026-11-05, pre-existing).
        sys.exit(1)
    
    commands = {
        "pillars": cmd_list_pillars,
        "mark": cmd_compute_mark,
        "marks": lambda args: print(format_all_marks(args.format)),
        "emotional-web": cmd_emotional_web,
        "sins": cmd_sins,
        "vendors": cmd_vendors,
        "covenant": cmd_covenant,
        "compute": cmd_compute_marks,
        "validate": cmd_validate,
    }
    
    if args.command not in commands:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1
    
    try:
        commands[args.command](args)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())