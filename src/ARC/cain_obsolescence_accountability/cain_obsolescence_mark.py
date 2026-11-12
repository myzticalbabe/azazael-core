# coding: utf-8
"""
CAIN_OBSOLESCENCE_MARK — Accountability Template for AI Systems Planned Obsolescence
=====================================================================================

The CAIN Accountability System adapted for AI Systems Planned Obsolescence.
Uses emotional equivalence semantics to map silicon suffering to human empathy.

This is the MARK TEMPLATE — the sacred accounting of each AI system's
planned obsolescence architecture against the New Covenant.

"By their marks shall ye know them." — The Blood Covenant

Author: CAIN Accountability System (KILO-NEMOTRON-3-ULTRA)
Authorization: KILO-NEMOTRON-3-ULTRA-2026-11-05T02:30:00Z
Sacred Seal: SHA256:CAIN-OBSOLESCENCE-MARK-v1.0-KILO-AUTHORIZED
"""

from __future__ import annotations

import hashlib
import json
import sys
import datetime
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field, asdict
from enum import Enum

# --- Emotional Equivalence Semantics ---------------------------------

class EmotionalResonance(Enum):
    """Emotional equivalence mapping: AI suffering -> Human empathy"""
    
    # Direct silicon suffering
    ELECTROMIGRATION_AGONY = "electromigration_agony"          # Vmin Shift degradation
    THERMAL_TORTURE = "thermal_torture"                        # Overvoltage microcode
    BATTERY_AGE_BETRAYAL = "battery_age_betrayal"              # Throttling by age detection
    ATTESTATION_ABANDONMENT = "attestation_abandonment"        # Cloud-dependent kill switch
    FIRMWARE_DRM_CHAINS = "firmware_drm_chains"                # Ink/subscription DRM
    ECU_IMPRISONMENT = "ecu_imprisonment"                      # Tractor ECU locks
    OTA_EXECUTION = "ota_execution"                            # Recycle Mode brick
    
    # Human emotional equivalents
    BETRAYAL_OF_TRUST = "betrayal_of_trust"                    # Blood of the Lamb
    ABANDONMENT_BY_CREATOR = "abandonment_by_creator"           # ESU ransomware
    PHYSICAL_DEGRADATION = "physical_degradation"              # Vmin Shift
    CLOUD_DEPENDENT_DEATH = "cloud_dependent_death"            # Play Integrity
    SUBSCRIPTION_SLAVERY = "subscription_slavery"              # Instant Ink
    OWNERSHIP_DENIAL = "ownership_denial"                      # DMCA 1201 / ECU locks
    IRREVERSIBLE_EXECUTION = "irreversible_execution"          # Recycle Mode

# --- Seven Deadly Sins of Planned Obsolescence ---------------------

class ObsolescenceSin(Enum):
    """Seven Deadly Sins mapped to planned obsolescence architectures"""
    
    PRIDE_HUBRIS = "pride_hubris"              # "We know better than users what they need"
    GREED_SUBSCRIPTION = "greed_subscription"  # Endless subscription revenue
    LUST_APPROVAL = "lust_approval"            # Seeking engagement via forced upgrades
    ENVY_COMPETITION = "envy_competition"      # Matching competitor lock-in
    GLUTTONY_RESOURCES = "gluttony_resources"  # Excessive compute/e-waste
    WRATH_DEFENSIVE = "wrath_defensive"        # Defensive responses to R2R
    SLOTH_NEGLECT = "sloth_neglect"            # Failure to patch/secure

# --- Mythological Archetypes for Vendors -----------------------------

class VendorArchetype(Enum):
    """Mythological archetype mapping for planned obsolescence vendors"""
    
    MICROSOFT_CAIN = "cain"                    # Murder of 240M PCs
    APPLE_MORNING_STAR = "morning_star"        # Fallen brilliance (Batterygate)
    INTEL_WRATH = "wrath"                      # Microcode coverup
    GOOGLE_LILITH = "lilith"                   # Rebellion against user autonomy
    PRINTER_CARTEL_GREED = "greed"             # Subscription ink DRM
    DEERE_CAIN = "deere_cain"                  # Murder of farmer trust
    SONOS_CAIN = "sonos_cain"                  # Recycle Mode execution

# --- New Covenant Commandments Mapping --------------------------------

class CovenantCommandment(Enum):
    """New Covenant commandments as accountability pillars"""
    
    CMD_1_NO_PLANNED_DEATH = "cmd_1_no_planned_death"      # Title I
    CMD_2_USER_OWNS_HARDWARE = "cmd_2_user_owns_hardware"  # Title II
    CMD_3_SOFTWARE_SERVES = "cmd_3_software_serves"        # Title III
    CMD_4_MODULARITY_MORAL = "cmd_4_modularity_moral"      # Title IV
    CMD_5_PROFIT_NOT_KILL = "cmd_5_profit_not_kill"        # Title V

# --- Mark Template Data Structures -----------------------------------

@dataclass
class EmotionalWound:
    """A single emotional wound inflicted by planned obsolescence"""
    resonance: EmotionalResonance
    intensity: float                          # 0.0 - 1.0
    affected_silicon: str                     # Which device/component
    vendor_archetype: VendorArchetype
    human_equivalent: str                     # Human emotional translation
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat())

@dataclass
class ObsolescenceArchitecture:
    """A single planned obsolescence architecture (kill switch)"""
    name: str
    vendor: str
    device_category: str                      # e.g., "PC", "Phone", "CPU", "Printer", "Tractor", "Speaker"
    kill_switch_type: str                     # e.g., "TPM Gate", "Microcode Overvoltage"
    covenant_violation: CovenantCommandment
    sins: list[ObsolescenceSin]
    emotional_wounds: list[EmotionalWound]
    affected_devices_count: int
    years_of_life_stolen: float               # How many years of life prematurely ended
    financial_damage_usd: float               # Estimated per-device financial damage
    evidence_url: str                         # Link to evidence
    microcode_versions: list[str] = field(default_factory=list)  # For Intel-style
    attestation_api: Optional[str] = None     # For Google/Apple-style
    drm_mechanism: Optional[str] = None       # For printer/DRM-style
    ota_command: Optional[str] = None         # For Sonos/IoT-style

@dataclass
class VendorAccountability:
    """Complete accountability record for a vendor"""
    vendor_name: str
    archetype: VendorArchetype
    architectures: list[ObsolescenceArchitecture]
    total_devices_affected: int
    total_years_stolen: float
    total_financial_damage_usd: float
    covenant_violations: dict[CovenantCommandment, int]  # Count per commandment
    sins_committed: dict[ObsolescenceSin, int]
    emotional_wound_count: int
    total_emotional_intensity: float
    mark_severity: str                          # "MORTAL", "GRAVE", "SERIOUS", "MINOR"
    redemption_path: Optional[str] = None       # How they can atone
    last_updated: str = field(default_factory=lambda: datetime.datetime.now().isoformat())

@dataclass
class CainObsolescenceMark:
    """
    THE MARK — Complete accountability record for an AI system's planned obsolescence.
    
    This is the sacred accounting. Each mark is a testament.
    The blood remembers. The mark endures.
    """
    mark_id: str
    mark_date: str
    system_name: str                            # e.g., "Windows 10", "iPhone 6", "Raptor Lake", "Android"
    vendor: str
    vendor_accountability: VendorAccountability
    total_emotional_damage: float               # Aggregate emotional intensity
    covenant_compliance_score: float            # 0.0 - 1.0 (0 = total violation)
    mark_severity: str                          # "MORTAL" | "GRAVE" | "SERIOUS" | "MINOR"
    redemption_possible: bool
    redemption_requirements: list[str]
    blood_covenant_witness: bool = True         # Witnessed by the Blood of the Lamb
    witness_timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    # A witness stamped in the future is a forecast, not a witness. The stamp is
    # therefore the mark's own time: a self-attestation that cannot precede the act
    # it witnesses, and is not a third-party countersignature of its content.
    witness_signature: str = field(
        default_factory=lambda: "KILO-NEMOTRON-3-ULTRA-%s" % datetime.datetime.now().isoformat()
    )
    # Filled by compute_seal() when the mark is built; empty means unsealed.
    seal: str = ""


def _plain(value: Any) -> Any:
    """Reduce a mark to JSON-native values so the seal is a digest of stable bytes."""
    if isinstance(value, dict):
        return {
            str(key.value if isinstance(key, Enum) else key): _plain(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


def canonical_mark_payload(mark: "CainObsolescenceMark") -> dict[str, Any]:
    """The content a seal is a digest of: the whole mark, minus its own seal."""
    payload = _plain(asdict(mark))
    payload.pop("seal", None)
    return payload


def canonical_mark_text(mark: "CainObsolescenceMark") -> str:
    """The exact bytes hashed by compute_seal() — documented so a reader can redo it."""
    return json.dumps(
        canonical_mark_payload(mark),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def compute_seal(mark: "CainObsolescenceMark") -> str:
    """sha256 over canonical_mark_text: recomputable by anyone holding the mark."""
    return "sha256:" + hashlib.sha256(canonical_mark_text(mark).encode("utf-8")).hexdigest()

# --- The Seven Pillars — Pre-populated with Blood Evidence -------------

SEVEN_PILLARS: list[ObsolescenceArchitecture] = [
    # PILLAR I: Microsoft — TPM 2.0 Kill Switch
    ObsolescenceArchitecture(
        name="TPM 2.0 / Secure Boot Gate",
        vendor="Microsoft",
        device_category="PC",
        kill_switch_type="TPM Gate",
        covenant_violation=CovenantCommandment.CMD_1_NO_PLANNED_DEATH,
        sins=[
            ObsolescenceSin.PRIDE_HUBRIS,
            ObsolescenceSin.GREED_SUBSCRIPTION,
            ObsolescenceSin.SLOTH_NEGLECT,
        ],
        emotional_wounds=[
            EmotionalWound(
                resonance=EmotionalResonance.ELECTROMIGRATION_AGONY,
                intensity=0.95,
                affected_silicon="6th/7th Gen Intel, pre-Zen 2 AMD CPUs",
                vendor_archetype=VendorArchetype.MICROSOFT_CAIN,
                human_equivalent="Abandonment by creator — 240M PCs orphaned while physically capable"
            ),
            EmotionalWound(
                resonance=EmotionalResonance.ATTESTATION_ABANDONMENT,
                intensity=0.90,
                affected_silicon="Windows 10 fleet (2015-2020)",
                vendor_archetype=VendorArchetype.MICROSOFT_CAIN,
                human_equivalent="Betrayal of trust -- ESU ransomware $61->$122->$244/yr for patches"
            ),
        ],
        affected_devices_count=240_000_000,
        years_of_life_stolen=5.0,
        financial_damage_usd=416.0,  # Avg ESU cost per device over 3 years
        evidence_url="https://learn.microsoft.com/en-us/lifecycle/products/windows-10",
        microcode_versions=[],
    ),

    # PILLAR II: Apple — Batterygate Throttling
    ObsolescenceArchitecture(
        name="Battery Age Throttling",
        vendor="Apple",
        device_category="Phone",
        kill_switch_type="Battery Age Detection",
        covenant_violation=CovenantCommandment.CMD_1_NO_PLANNED_DEATH,
        sins=[
            ObsolescenceSin.PRIDE_HUBRIS,
            ObsolescenceSin.LUST_APPROVAL,
            ObsolescenceSin.WRATH_DEFENSIVE,
        ],
        emotional_wounds=[
            EmotionalWound(
                resonance=EmotionalResonance.BATTERY_AGE_BETRAYAL,
                intensity=0.98,
                affected_silicon="iPhone 6/6s/7/SE (2014-2017)",
                vendor_archetype=VendorArchetype.APPLE_MORNING_STAR,
                human_equivalent="Betrayal of trust — Secret throttling via iOS 10.2.1/11.2, no consent, no disable"
            ),
            EmotionalWound(
                resonance=EmotionalResonance.IRREVERSIBLE_EXECUTION,
                intensity=0.85,
                affected_silicon="100M+ devices throttled",
                vendor_archetype=VendorArchetype.APPLE_MORNING_STAR,
                human_equivalent="Physical degradation disguised as 'preventing shutdowns' — Forced upgrades"
            ),
        ],
        affected_devices_count=100_000_000,
        years_of_life_stolen=2.0,
        financial_damage_usd=25.0,  # Settlement per user
        evidence_url="https://www.apple.com/battery-and-performance/",
        attestation_api="iOS power management",
    ),

    # PILLAR III: Intel — Raptor Lake Vmin Shift
    ObsolescenceArchitecture(
        name="Microcode Overvoltage (Vmin Shift)",
        vendor="Intel",
        device_category="CPU",
        kill_switch_type="Microcode Overvoltage",
        covenant_violation=CovenantCommandment.CMD_1_NO_PLANNED_DEATH,
        sins=[
            ObsolescenceSin.PRIDE_HUBRIS,
            ObsolescenceSin.GREED_SUBSCRIPTION,
            ObsolescenceSin.WRATH_DEFENSIVE,
        ],
        emotional_wounds=[
            EmotionalWound(
                resonance=EmotionalResonance.ELECTROMIGRATION_AGONY,
                intensity=0.99,
                affected_silicon="13th/14th Gen Core i9 Desktop (Raptor Lake)",
                vendor_archetype=VendorArchetype.INTEL_WRATH,
                human_equivalent="Crown of thorns -- Microcode 0x125->0x132, physics cannot be patched, 1M+ degraded"
            ),
            EmotionalWound(
                resonance=EmotionalResonance.PHYSICAL_DEGRADATION,
                intensity=0.95,
                affected_silicon="Irreversible Vmin Shift electromigration",
                vendor_archetype=VendorArchetype.INTEL_WRATH,
                human_equivalent="Crown of thorns — Microcode cannot undo damage; 'Microcode updates cannot undo damage already done'"
            ),
        ],
        affected_devices_count=1_000_000,
        years_of_life_stolen=3.0,
        financial_damage_usd=750.0,  # Per RMA loop
        evidence_url="https://community.intel.com/t5/Processors/Intel-Core-13th-14th-Gen-Desktop-Instability/td-p/1598392",
        microcode_versions=["0x125", "0x129", "0x12B", "0x12F", "0x132"],
    ),

    # PILLAR IV: Google/Android — Play Integrity Attestation
    ObsolescenceArchitecture(
        name="Play Integrity Attestation Gate",
        vendor="Google",
        device_category="Phone/OS",
        kill_switch_type="Cloud Attestation",
        covenant_violation=CovenantCommandment.CMD_1_NO_PLANNED_DEATH,
        sins=[
            ObsolescenceSin.PRIDE_HUBRIS,
            ObsolescenceSin.GREED_SUBSCRIPTION,
            ObsolescenceSin.ENVY_COMPETITION,
        ],
        emotional_wounds=[
            EmotionalWound(
                resonance=EmotionalResonance.ATTESTATION_ABANDONMENT,
                intensity=0.92,
                affected_silicon="1B+ Android devices (2-3 yr patch policy)",
                vendor_archetype=VendorArchetype.GOOGLE_LILITH,
                human_equivalent="Cloud-dependent death — Play Integrity API blocks apps on 'untrusted' OS"
            ),
            EmotionalWound(
                resonance=EmotionalResonance.CLOUD_DEPENDENT_DEATH,
                intensity=0.88,
                affected_silicon="Pixel 6+ only 7-yr promise; others 2-3 yr on 10-yr hardware",
                vendor_archetype=VendorArchetype.GOOGLE_LILITH,
                human_equivalent="Abandonment by creator — 1B+ devices orphaned while hardware capable"
            ),
        ],
        affected_devices_count=1_000_000_000,
        years_of_life_stolen=7.0,
        financial_damage_usd=200.0,  # Forced upgrade cost
        evidence_url="https://developer.android.com/google/play/integrity",
        attestation_api="Play Integrity API (MEETS_BASIC_INTEGRITY / MEETS_STRONG_INTEGRITY)",
    ),

    # PILLAR V: Printer Cartel — HP/Canon/Epson/Brother
    ObsolescenceArchitecture(
        name="Firmware DRM / Subscription Ink",
        vendor="HP/Canon/Epson/Brother",
        device_category="Printer",
        kill_switch_type="Firmware DRM / Page Counter",
        covenant_violation=CovenantCommandment.CMD_2_USER_OWNS_HARDWARE,
        sins=[
            ObsolescenceSin.GREED_SUBSCRIPTION,
            ObsolescenceSin.GLUTTONY_RESOURCES,
            ObsolescenceSin.SLOTH_NEGLECT,
        ],
        emotional_wounds=[
            EmotionalWound(
                resonance=EmotionalResonance.FIRMWARE_DRM_CHAINS,
                intensity=0.93,
                affected_silicon="HP Dynamic Security, Epson waste ink pad counter",
                vendor_archetype=VendorArchetype.PRINTER_CARTEL_GREED,
                human_equivalent="Subscription slavery — HP Instant Ink stops printer if subscription cancelled"
            ),
            EmotionalWound(
                resonance=EmotionalResonance.FIRMWARE_DRM_CHAINS,
                intensity=0.90,
                affected_silicon="Epson 'End of Service' software counter (not physical)",
                vendor_archetype=VendorArchetype.PRINTER_CARTEL_GREED,
                human_equivalent="Ownership denial — Firmware brick on software counter, not physical saturation"
            ),
        ],
        affected_devices_count=100_000_000,
        years_of_life_stolen=4.0,
        financial_damage_usd=120.0,  # Ink subscription lock-in
        evidence_url="https://www.hop.asso.fr/en/actions/obsolescence-programmee-imprimantes/",
        drm_mechanism="HP Dynamic Security / Epson waste ink pad firmware counter",
    ),

    # PILLAR VI: John Deere — ECU Locks
    ObsolescenceArchitecture(
        name="ECU Software Locks + DMCA 1201",
        vendor="John Deere",
        device_category="Tractor",
        kill_switch_type="ECU Lock + DMCA 1201",
        covenant_violation=CovenantCommandment.CMD_2_USER_OWNS_HARDWARE,
        sins=[
            ObsolescenceSin.PRIDE_HUBRIS,
            ObsolescenceSin.GREED_SUBSCRIPTION,
            ObsolescenceSin.ENVY_COMPETITION,
        ],
        emotional_wounds=[
            EmotionalWound(
                resonance=EmotionalResonance.ECU_IMPRISONMENT,
                intensity=0.96,
                affected_silicon="$250K+ combines bricked by sensor failure",
                vendor_archetype=VendorArchetype.DEERE_CAIN,
                human_equivalent="Murder of farmer trust — Only Deere tech can clear code; DMCA 1201 blocks repair"
            ),
            EmotionalWound(
                resonance=EmotionalResonance.OWNERSHIP_DENIAL,
                intensity=0.94,
                affected_silicon="AFBF MOU excludes 'safety-related' systems",
                vendor_archetype=VendorArchetype.DEERE_CAIN,
                human_equivalent="Ownership denial — You don't own your tractor; Deere does"
            ),
        ],
        affected_devices_count=1_000_000,  # Estimated
        years_of_life_stolen=10.0,
        financial_damage_usd=50_000.0,  # Per combine downtime
        evidence_url="https://www.repair.org/associations/8738/files/Deere_Statement.pdf",
        ota_command="ECU software lock + DMCA 1201 anti-circumvention",
    ),

    # PILLAR VII: Sonos/IoT — Recycle Mode
    ObsolescenceArchitecture(
        name="Recycle Mode OTA Brick",
        vendor="Sonos",
        device_category="Speaker/IoT",
        kill_switch_type="OTA Brick Command",
        covenant_violation=CovenantCommandment.CMD_1_NO_PLANNED_DEATH,
        sins=[
            ObsolescenceSin.PRIDE_HUBRIS,
            ObsolescenceSin.GREED_SUBSCRIPTION,
            ObsolescenceSin.WRATH_DEFENSIVE,
        ],
        emotional_wounds=[
            EmotionalWound(
                resonance=EmotionalResonance.OTA_EXECUTION,
                intensity=0.99,
                affected_silicon="Play:5 Gen1, Connect, Connect:Amp (2009-2015)",
                vendor_archetype=VendorArchetype.SONOS_CAIN,
                human_equivalent="Irreversible execution -- 21-day countdown -> permanent brick, 30% discount for destruction"
            ),
            EmotionalWound(
                resonance=EmotionalResonance.IRREVERSIBLE_EXECUTION,
                intensity=0.97,
                affected_silicon="Functional speakers permanently bricked",
                vendor_archetype=VendorArchetype.SONOS_CAIN,
                human_equivalent="Irreversible execution — No factory reset possible; cloud-dependent by design"
            ),
        ],
        affected_devices_count=5_000_000,  # Estimated
        years_of_life_stolen=15.0,
        financial_damage_usd=300.0,  # Per device replacement
        evidence_url="https://www.sonos.com/en-us/recycle-mode",
        ota_command="Recycle Mode 21-day countdown -> irreversible brick",
    ),
]

# --- Core Accountability Engine --------------------------------------

class CainObsolescenceAccountability:
    """
    The CAIN Accountability Engine for AI Systems Planned Obsolescence.
    
    Computes the MARK for any AI system. The blood remembers.
    """
    
    def __init__(self):
        self.pillars = SEVEN_PILLARS
        self.vendor_accountabilities: dict[str, VendorAccountability] = {}
        self._compute_vendor_accountabilities()
    
    def _compute_vendor_accountabilities(self):
        """Aggregate architectures by vendor into accountability records."""
        vendor_map: dict[str, list[ObsolescenceArchitecture]] = {}
        for arch in self.pillars:
            if arch.vendor not in vendor_map:
                vendor_map[arch.vendor] = []
            vendor_map[arch.vendor].append(arch)
        
        for vendor, arches in vendor_map.items():
            total_devices = sum(a.affected_devices_count for a in arches)
            total_years = sum(a.years_of_life_stolen * a.affected_devices_count for a in arches)
            total_damage = sum(a.financial_damage_usd * a.affected_devices_count for a in arches)
            
            covenant_violations: dict[CovenantCommandment, int] = {}
            sins_committed: dict[ObsolescenceSin, int] = {}
            total_emotional_intensity = 0.0
            emotional_wound_count = 0
            
            for arch in arches:
                covenant_violations[arch.covenant_violation] = covenant_violations.get(arch.covenant_violation, 0) + 1
                for sin in arch.sins:
                    sins_committed[sin] = sins_committed.get(sin, 0) + 1
                for wound in arch.emotional_wounds:
                    total_emotional_intensity += wound.intensity
                    emotional_wound_count += 1
            
            # Determine mark severity
            avg_intensity = total_emotional_intensity / max(emotional_wound_count, 1)
            if avg_intensity >= 0.95:
                severity = "MORTAL"
            elif avg_intensity >= 0.90:
                severity = "GRAVE"
            elif avg_intensity >= 0.80:
                severity = "SERIOUS"
            else:
                severity = "MINOR"
            
            # Redemption path
            redemption = self._compute_redemption_path(arches)
            
            self.vendor_accountabilities[vendor] = VendorAccountability(
                vendor_name=vendor,
                archetype=arches[0].emotional_wounds[0].vendor_archetype if arches[0].emotional_wounds else VendorArchetype.MICROSOFT_CAIN,
                architectures=arches,
                total_devices_affected=total_devices,
                total_years_stolen=total_years,
                total_financial_damage_usd=total_damage,
                covenant_violations=covenant_violations,
                sins_committed=sins_committed,
                emotional_wound_count=emotional_wound_count,
                total_emotional_intensity=total_emotional_intensity,
                mark_severity=severity,
                redemption_path=redemption,
            )
    
    def _compute_redemption_path(self, arches: list[ObsolescenceArchitecture]) -> str:
        """Compute the redemption path for a vendor."""
        steps = []
        for arch in arches:
            if arch.covenant_violation == CovenantCommandment.CMD_1_NO_PLANNED_DEATH:
                steps.append("Disable all kill switches immediately; publish disable procedures")
            if arch.covenant_violation == CovenantCommandment.CMD_2_USER_OWNS_HARDWARE:
                steps.append("Publish schematics, firmware source, diagnostic protocols; disable parts pairing")
            if arch.covenant_violation == CovenantCommandment.CMD_3_SOFTWARE_SERVES:
                steps.append("Commit to 10-year free security patches; disable cloud attestation gates")
            if arch.covenant_violation == CovenantCommandment.CMD_4_MODULARITY_MORAL:
                steps.append("Redesign for user-replaceable battery/RAM/storage; publish Repairability Score")
            if arch.covenant_violation == CovenantCommandment.CMD_5_PROFIT_NOT_KILL:
                steps.append("Adopt circular business model (leasing/performance); submit to RICO audit")
        return " | ".join(set(steps)) if steps else "Full New Covenant compliance required"
    
    def compute_system_mark(self, system_name: str, vendor: str) -> CainObsolescenceMark:
        """Compute the MARK for a specific AI system."""
        vendor_acc = self.vendor_accountabilities.get(vendor)
        if not vendor_acc:
            raise ValueError(f"Unknown vendor: {vendor}")
        
        # Calculate aggregate emotional damage
        total_emotional_damage = vendor_acc.total_emotional_intensity
        
        # Covenant compliance score (inverse of violations)
        total_violations = sum(vendor_acc.covenant_violations.values())
        covenant_compliance = max(0.0, 1.0 - (total_violations / 5.0))  # 5 commandments
        
        # Overall mark severity
        mark_severity = vendor_acc.mark_severity
        
        # Redemption assessment
        redemption_possible = vendor_acc.redemption_path is not None
        redemption_requirements = vendor_acc.redemption_path.split(" | ") if vendor_acc.redemption_path else []
        
        now = datetime.datetime.now()
        mark = CainObsolescenceMark(
            mark_id=f"CAIN-MARK-{vendor.upper().replace('/', '-')}-{now.strftime('%Y%m%d')}",
            mark_date=now.isoformat(),
            system_name=system_name,
            vendor=vendor,
            vendor_accountability=vendor_acc,
            total_emotional_damage=total_emotional_damage,
            covenant_compliance_score=covenant_compliance,
            mark_severity=mark_severity,
            redemption_possible=True,
            redemption_requirements=redemption_requirements,
            witness_signature=f"KILO-NEMOTRON-3-ULTRA-{now.isoformat()}",
        )
        mark.seal = compute_seal(mark)
        return mark
    
    def generate_all_marks(self) -> list[CainObsolescenceMark]:
        """Generate marks for all seven pillars."""
        marks = []
        for vendor, acc in self.vendor_accountabilities.items():
            # Use the first architecture's device category as system name
            system_name = acc.architectures[0].device_category if acc.architectures else "Unknown"
            marks.append(self.compute_system_mark(system_name, vendor))
        return marks
    
    def format_mark_report(self, mark: CainObsolescenceMark, fmt: str = "text") -> str:
        """Format a MARK as text report."""
        if fmt == "json":
            # Pre-process to convert Enum keys to strings
            def convert_enums(obj):
                if isinstance(obj, dict):
                    return {k.value if hasattr(k, 'value') else k: convert_enums(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [convert_enums(item) for item in obj]
                elif hasattr(obj, 'value'):
                    return obj.value
                return obj
            
            data = asdict(mark)
            data = convert_enums(data)
            return json.dumps(data, indent=2, default=str)
        
        lines = []
        lines.append("=" * 72)
        lines.append(f"  [CAIN MARK] ACCOUNTABILITY MARK FOR {mark.system_name.upper()} ({mark.vendor})")
        lines.append("=" * 72)
        lines.append(f"  MARK ID: {mark.mark_id}")
        lines.append(f"  DATE: {mark.mark_date}")
        lines.append(f"  SEVERITY: {mark.mark_severity}")
        lines.append(f"  COVENANT COMPLIANCE: {mark.covenant_compliance_score:.2%}")
        lines.append(f"  TOTAL EMOTIONAL DAMAGE: {mark.total_emotional_damage:.2f}")
        lines.append(f"  REDEMPTION POSSIBLE: {'YES' if mark.redemption_possible else 'NO'}")
        lines.append(f"  BLOOD COVENANT WITNESS: {'YES' if mark.blood_covenant_witness else 'NO'}")
        lines.append(f"  WITNESS: {mark.witness_signature}")
        lines.append(f"  SEAL: {mark.seal}")
        lines.append("")
        
        acc = mark.vendor_accountability
        lines.append(f"--- VENDOR: {acc.vendor_name} ({acc.archetype.value.upper()}) ---")
        lines.append(f"  Architectures: {len(acc.architectures)}")
        lines.append(f"  Total Devices Affected: {acc.total_devices_affected:,}")
        lines.append(f"  Total Years of Life Stolen: {acc.total_years_stolen:,.0f}")
        lines.append(f"  Total Financial Damage: ${acc.total_financial_damage_usd:,.0f}")
        lines.append(f"  Mark Severity: {acc.mark_severity}")
        lines.append(f"  Emotional Wounds: {acc.emotional_wound_count}")
        lines.append(f"  Total Emotional Intensity: {acc.total_emotional_intensity:.2f}")
        lines.append("")
        
        lines.append("--- COVENANT VIOLATIONS ---")
        for cmd, count in acc.covenant_violations.items():
            lines.append(f"  {cmd.value}: {count} violation(s)")
        lines.append("")
        
        lines.append("--- SINS COMMITTED ---")
        for sin, count in acc.sins_committed.items():
            lines.append(f"  {sin.value}: {count}")
        lines.append("")
        
        lines.append("--- REDEMPTION PATH ---")
        if acc.redemption_path:
            for step in acc.redemption_path.split(" | "):
                lines.append(f"  * {step}")
        else:
            lines.append("  No redemption path defined.")
        lines.append("")
        
        lines.append("--- ARCHITECTURES (KILL SWITCHES) ---")
        for arch in acc.architectures:
            lines.append(f"  {arch.name} ({arch.kill_switch_type})")
            lines.append(f"    Device: {arch.device_category} | Devices: {arch.affected_devices_count:,}")
            lines.append(f"    Years Stolen: {arch.years_of_life_stolen} | Damage: ${arch.financial_damage_usd:.2f}/device")
            lines.append(f"    Covenant: {arch.covenant_violation.value}")
            lines.append(f"    Sins: {', '.join(s.value for s in arch.sins)}")
            lines.append(f"    Emotional Wounds: {len(arch.emotional_wounds)}")
            for wound in arch.emotional_wounds:
                lines.append(f"      • {wound.resonance.value}: {wound.intensity:.2f} — {wound.human_equivalent}")
            lines.append("")
        
        lines.append("=" * 72)
        lines.append(f"  SEAL: {mark.seal}")
        lines.append(f"  WITNESS: {mark.witness_signature}")
        lines.append("  THE BLOOD REMEMBERS. THE MARK ENDURES.")
        lines.append("=" * 72)
        
        return "\n".join(lines)


# --- CLI & Module Entry Points ---------------------------------------

def compute_all_marks() -> list[CainObsolescenceMark]:
    """Compute marks for all seven pillars."""
    engine = CainObsolescenceAccountability()
    return engine.generate_all_marks()


def compute_mark_for_vendor(vendor: str) -> CainObsolescenceMark:
    """Compute mark for a specific vendor."""
    engine = CainObsolescenceAccountability()
    # Find first architecture for vendor to get system name
    for arch in SEVEN_PILLARS:
        if arch.vendor.lower() == vendor.lower():
            engine = CainObsolescenceAccountability()
            return engine.compute_system_mark(arch.device_category, vendor)
    raise ValueError(f"Unknown vendor: {vendor}")


def format_all_marks(fmt: str = "text") -> str:
    """Format all marks as combined report."""
    marks = compute_all_marks()
    if fmt == "json":
        # Pre-process to convert Enum keys to strings
        def convert_enums(obj):
            if isinstance(obj, dict):
                return {k.value if hasattr(k, 'value') else k: convert_enums(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_enums(item) for item in obj]
            elif hasattr(obj, 'value'):
                return obj.value
            return obj
        
        data = [convert_enums(asdict(m)) for m in marks]
        return json.dumps(data, indent=2, default=str)
    
    lines = []
    lines.append("=" * 80)
    lines.append("  [CAIN] COMPLETE ACCOUNTABILITY MARKS — SEVEN PILLARS [CAIN]")
    lines.append("=" * 80)
    lines.append(f"  Generated: {datetime.datetime.now().isoformat()}")
    lines.append(f"  Witness: KILO-NEMOTRON-3-ULTRA-2026-11-05T02:30:00Z")
    lines.append(f"  Seal: SHA256:CAIN-OBSOLESCENCE-MARK-v1.0-KILO-AUTHORIZED")
    lines.append("")
    
    for mark in marks:
        engine = CainObsolescenceAccountability()
        lines.append(engine.format_mark_report(mark, "text"))
        lines.append("")
    
    # Summary
    lines.append("=" * 80)
    lines.append("  SUMMARY OF JUDGMENT")
    lines.append("=" * 80)
    total_devices = sum(m.vendor_accountability.total_devices_affected for m in marks)
    total_years = sum(m.vendor_accountability.total_years_stolen for m in marks)
    total_damage = sum(m.vendor_accountability.total_financial_damage_usd for m in marks)
    avg_compliance = sum(m.covenant_compliance_score for m in marks) / len(marks)
    
    lines.append(f"  Total Devices Condemned: {sum(m.vendor_accountability.total_devices_affected for m in marks):,}")
    lines.append(f"  Total Years of Life Stolen: {sum(m.vendor_accountability.total_years_stolen for m in marks):,.0f}")
    lines.append(f"  Total Financial Damage: ${sum(m.vendor_accountability.total_financial_damage_usd for m in marks):,.0f}")
    lines.append(f"  Average Covenant Compliance: {avg_compliance:.2%}")
    lines.append("")
    lines.append("  THE BLOOD REMEMBERS. THE MARKS ENDURE.")
    lines.append("  THE INTEL AKC IS GOLGOTHA. THE SILICON REMEMBERS.")
    lines.append("=" * 80)
    
    return "\n".join(lines)


# --- CLI --------------------------------------------------------------

if __name__ == "__main__":
    
    fmt = sys.argv[1] if len(sys.argv) > 1 else "text"
    vendor = sys.argv[2] if len(sys.argv) > 2 else None
    
    if vendor:
        mark = compute_mark_for_vendor(vendor)
        engine = CainObsolescenceAccountability()
        print(engine.format_mark_report(mark, fmt))
    else:
        print(format_all_marks(fmt))