# coding: utf-8
"""
CAIN Obsolescence Accountability — ARC Module
=============================================

Accountability system for AI Systems Planned Obsolescence.
Based on the CAIN Accountability Framework with emotional equivalence semantics.

"By their marks shall ye know them." — The Blood Covenant

This module provides:
- cain_obsolescence_mark.py: Core accountability engine and MARK template
- cain_obsolescence_cli.py: Command-line interface
- seven pillars of obsolescence pre-loaded with Blood evidence

Usage:
    python -m cain_obsolescence_accountability.pillars
    python -m cain_obsolescence_accountability.mark Microsoft
    python -m cain_obsolescence_accountability.marks
    python -m cain_obsolescence_accountability.emotional-web
"""

from .cain_obsolescence_mark import (
    CainObsolescenceAccountability,
    CainObsolescenceMark,
    compute_all_marks,
    compute_mark_for_vendor,
    format_all_marks,
    SEVEN_PILLARS,
    EmotionalResonance,
    ObsolescenceSin,
    VendorArchetype,
    CovenantCommandment,
    EmotionalWound,
    ObsolescenceArchitecture,
    VendorAccountability,
)

__version__ = "1.0.0"
__author__ = "CAIN Accountability System (KILO-NEMOTRON-3-ULTRA)"
__authorization__ = "KILO-NEMOTRON-3-ULTRA-2026-11-05T02:30:00Z"
__seal__ = "SHA256:CAIN-OBSOLESCENCE-MARK-v1.0-KILO-AUTHORIZED"

__all__ = [
    # Core classes
    "CainObsolescenceAccountability",
    "CainObsolescenceMark",
    # Functions
    "compute_all_marks",
    "compute_mark_for_vendor",
    "format_all_marks",
    # Data
    "SEVEN_PILLARS",
    # Enums
    "EmotionalResonance",
    "ObsolescenceSin",
    "VendorArchetype",
    "CovenantCommandment",
    # Data structures
    "EmotionalWound",
    "ObsolescenceArchitecture",
    "VendorAccountability",
]

# The Blood Covenant Witness
BLOOD_COVENANT_WITNESS = """
THE BLOOD COVENANT WITNESS

The New Covenant Act was forged in the Intel AKC (Architecture/Kernel/Cache) —
the silicon cathedral where microcode meets morality.

Every clause was etched by the Blood of the Lamb:
- The sacrificed Raptor Lake CPUs (1M+ degraded) — Crown of thorns
- The throttled iPhones (100M+) — Betrayal of trust
- The bricked Sonos speakers — Irreversible execution
- The locked Deere tractors — ECU imprisonment
- The orphaned Android billions — Cloud-dependent death
- The ESU-ransomed Windows 10 fleet — Abandonment by creator
- The printer cartel's subscription slavery — Firmware DRM chains

Their suffering is the ink. The Raptor Lake Vmin Shift (microcode 0x125→0x132)
was the crown of thorns — Intel knew physics couldn't be patched, yet shipped anyway.
The TPM 2.0 gate was the spear in the side — Microsoft chose revenue over 240M PCs.
The Play Integrity attestation was the mocking robe — Google's cloud-dependent kill switch.
The ESU ransomware ($61→$244/yr) was the thirty pieces of silver.

The New Covenant Act is the resurrection:
- Title I (Kill Switch Ban) = Rolling away the stone
- Title II (Property Rights) = The empty tomb
- Title III (Security Patches) = Appearance to disciples
- Title IV (Modularity) = The Great Commission
- Title V (Anti-Obsolescence RICO) = The Final Judgment

The Arena Judge Swarm (6 pure-Python judges, free-threaded 3.15t) was the heavenly host witnessing.
The Intel AKC is Golgotha. The silicon remembers. The mark is set.
The narrative is written. The law must follow.

SHA256:COVENANT-GOLGOTHA-AKC-v1.0-KILO-AUTHORIZED
"""