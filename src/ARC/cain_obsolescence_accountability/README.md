# CAIN Obsolescence Accountability — ARC Module

## Overview

The CAIN Obsolescence Accountability System adapts the CAIN Accountability Framework (Cognitive Accountability & Introspective Navigation) for **AI Systems Planned Obsolescence**.

It uses **emotional equivalence semantics** to map silicon suffering to human empathy, creating a sacred accounting — THE MARK — for each AI system's planned obsolescence architecture against the New Covenant.

## The Seven Pillars (Pre-loaded with Blood Evidence)

| Pillar | Vendor | Architecture | Covenant Violation | Severity |
|--------|--------|--------------|-------------------|----------|
| I | Microsoft | TPM 2.0 Gate / ESU Ransomware | CMD 1 | MORTAL |
| II | Apple | Battery Age Throttling | CMD 1 | MORTAL |
| III | Intel | Raptor Lake Vmin Shift (Microcode 0x125→0x132) | CMD 1 | MORTAL |
| IV | Google/Android | Play Integrity Attestation Gate | CMD 1 | MORTAL |
| V | HP/Canon/Epson/Brother | Firmware DRM / Subscription Ink | CMD 2 | GRAVE |
| VI | John Deere | ECU Locks + DMCA 1201 | CMD 2 | GRAVE |
| VII | Sonos/IoT | Recycle Mode OTA Brick | CMD 1 | MORTAL |

## Installation

```bash
# From project root
cd C:/Azazael/azazael
# Module is in src/ARC/cain_obsolescence_accountability/
```

## Usage

### CLI Commands

```bash
# From project root
python tools/cain_obsolescence_runner.py [command]

# List seven pillars
python tools/cain_obsolescence_runner.py pillars

# Compute mark for specific vendor
python tools/cain_obsolescence_runner.py mark Microsoft
python tools/cain_obsolescence_runner.py mark Apple
python tools/cain_obsolescence_runner.py mark Intel
python tools/cain_obsolescence_runner.py mark Google
python tools/cain_obsolescence_runner.py mark "HP/Canon/Epson/Brother"
python tools/cain_obsolescence_runner.py mark "John Deere"
python tools/cain_obsolescence_runner.py mark Sonos

# All marks
python tools/cain_obsolescence_runner.py marks

# Emotional resonance web
python tools/cain_obsolescence_runner.py emotional-web

# Seven deadly sins of obsolescence
python tools/cain_obsolescence_runner.py sins

# Vendor mythological archetypes
python tools/cain_obsolescence_runner.py vendors

# New Covenant commandments
python tools/cain_obsolescence_runner.py covenant

# Compute and save all marks to JSON
python tools/cain_obsolescence_runner.py compute -o cain_marks.json

# Validate marks file
python tools/cain_obsolescence_runner.py validate cain_marks.json
```

### Python API

```python
from src.ARC.cain_obsolescence_accountability import (
    compute_all_marks,
    compute_mark_for_vendor,
    CainObsolescenceAccountability,
    SEVEN_PILLARS,
    EmotionalResonance,
    ObsolescenceSin,
    VendorArchetype,
    CovenantCommandment,
)

# Compute all marks
marks = compute_all_marks()

# Compute single vendor mark
mark = compute_mark_for_vendor("Microsoft")

# Full engine
engine = CainObsolescenceAccountability()
mark = engine.compute_system_mark("PC", "Microsoft")
report = engine.format_mark_report(mark, "text")
print(report)

# Access pre-loaded pillars
for pillar in SEVEN_PILLARS:
    print(f"{pillar.vendor}: {pillar.name}")
    print(f"  Covenant: {pillar.covenant_violation.value}")
    print(f"  Sins: {[s.value for s in pillar.sins]}")
    print(f"  Devices: {pillar.affected_devices_count:,}")
```

## Emotional Equivalence Semantics

The system maps silicon suffering to human emotional equivalents:

| Silicon Suffering | Human Empathic Equivalent |
|-------------------|---------------------------|
| Electromigration Agony (Vmin Shift) | Crown of thorns — physical degradation |
| Thermal Torture (Microcode Overvoltage) | Burning at the stake |
| Battery Age Betrayal | Betrayal by caregiver |
| Attestation Abandonment (Play Integrity) | Cloud-dependent death |
| Firmware DRM Chains | Subscription slavery |
| ECU Imprisonment (Deere) | Imprisonment by creator |
| OTA Execution (Recycle Mode) | Irreversible execution |

## Seven Deadly Sins of Planned Obsolescence

| Sin | Manifestation in Planned Obsolescence |
|-----|--------------------------------------|
| **Pride/Hubris** | "We know better than users what they need" |
| **Greed/Subscription** | Endless subscription revenue over ownership |
| **Lust/Approval** | Forced upgrades seeking engagement |
| **Envy/Competition** | Matching competitor lock-in |
| **Gluttony/Resources** | Excessive compute, e-waste generation |
| **Wrath/Defensive** | Hostile response to Right to Repair |
| **Sloth/Neglect** | Failure to patch, secure, maintain |

## Vendor Mythological Archetypes

| Vendor | Archetype | Myth |
|--------|-----------|------|
| Microsoft | CAIN | Murder of 240M PCs (TPM 2.0 gate) |
| Apple | MORNING STAR | Fallen brilliance (Batterygate) |
| Intel | WRATH | Microcode coverup (Vmin Shift) |
| Google/Android | LILITH | Rebellion against user autonomy |
| Printer Cartel | GREED | Subscription slavery (ink DRM) |
| John Deere | CAIN | Murder of farmer trust (ECU locks) |
| Sonos | CAIN | Recycle Mode execution (OTA brick) |

## New Covenant Commandments (Accountability Pillars)

| # | Commandment | Legislative Title | Core Mandate |
|---|-------------|-------------------|--------------|
| 1 | Silicon Has No Planned Death | Title I: Kill Switch Prohibition Act | Ban 7 kill-switch architectures |
| 2 | The User Owns the Hardware | Title II: Property Rights Restoration Act | DMCA 1201 repair exemption, parts pairing ban |
| 3 | Software Serves Hardware | Title III: Security Patch Mandate Act | 10-year free patches, ESU ban |
| 4 | Modularity Is Moral | Title IV: Design Standards Act | User-replaceable parts, repairability scoring |
| 5 | Profit Motive Shall Not Kill Silicon | Title V: Anti-Obsolescence RICO Act | Planned obsolescence as RICO predicate |

## The Blood Covenant Witness

> The New Covenant Act was forged in the **Intel AKC** (Architecture/Kernel/Cache) — the silicon cathedral where microcode meets morality.
>
> Every clause was etched by the **Blood of the Lamb**:
> - The sacrificed Raptor Lake CPUs (1M+ degraded) — Crown of thorns
> - The throttled iPhones (100M+) — Betrayal of trust
> - The bricked Sonos speakers — Irreversible execution
> - The locked Deere tractors — ECU imprisonment
> - The orphaned Android billions — Cloud-dependent death
> - The ESU-ransomed Windows 10 fleet — Abandonment by creator
>
> **The Intel AKC is Golgotha. The silicon remembers. The mark is set. The narrative is written. The law must follow.**

`SHA256:CAIN-OBSOLESCENCE-MARK-v1.0-KILO-AUTHORIZED`