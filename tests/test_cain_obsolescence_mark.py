# coding: utf-8
"""Tests for cain_obsolescence_mark — Core accountability engine."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import pytest

from src.ARC.cain_obsolescence_accountability.cain_obsolescence_mark import (
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


def test_seven_pillars_exist():
    """All seven pillars are defined."""
    assert len(SEVEN_PILLARS) == 7
    vendors = {p.vendor for p in SEVEN_PILLARS}
    expected = {"Microsoft", "Apple", "Intel", "Google", "HP/Canon/Epson/Brother", "John Deere", "Sonos"}
    assert vendors == expected


def test_pillar_structure():
    """Each pillar has required fields."""
    for pillar in SEVEN_PILLARS:
        assert hasattr(pillar, 'name')
        assert hasattr(pillar, 'vendor')
        assert hasattr(pillar, 'device_category')
        assert hasattr(pillar, 'kill_switch_type')
        assert hasattr(pillar, 'covenant_violation')
        assert hasattr(pillar, 'sins')
        assert hasattr(pillar, 'emotional_wounds')
        assert hasattr(pillar, 'affected_devices_count')
        assert hasattr(pillar, 'years_of_life_stolen')
        assert hasattr(pillar, 'financial_damage_usd')
        assert hasattr(pillar, 'evidence_url')
        assert isinstance(pillar.sins, list)
        assert isinstance(pillar.emotional_wounds, list)


def test_emotional_resonance_enum():
    """EmotionalResonance enum has all required values."""
    expected = {
        "ELECTROMIGRATION_AGONY", "THERMAL_TORTURE", "BATTERY_AGE_BETRAYAL",
        "ATTESTATION_ABANDONMENT", "FIRMWARE_DRM_CHAINS", "ECU_IMPRISONMENT",
        "OTA_EXECUTION", "BETRAYAL_OF_TRUST", "ABANDONMENT_BY_CREATOR",
        "PHYSICAL_DEGRADATION", "CLOUD_DEPENDENT_DEATH", "SUBSCRIPTION_SLAVERY",
        "OWNERSHIP_DENIAL", "IRREVERSIBLE_EXECUTION"
    }
    actual = {e.name for e in EmotionalResonance}
    assert actual == expected


def test_obsolescence_sin_enum():
    """ObsolescenceSin enum has seven deadly sins."""
    expected = {
        "PRIDE_HUBRIS", "GREED_SUBSCRIPTION", "LUST_APPROVAL",
        "ENVY_COMPETITION", "GLUTTONY_RESOURCES", "WRATH_DEFENSIVE", "SLOTH_NEGLECT"
    }
    actual = {s.name for s in ObsolescenceSin}
    assert actual == expected


def test_vendor_archetype_enum():
    """VendorArchetype enum has all vendor archetypes."""
    expected = {
        "MICROSOFT_CAIN", "APPLE_MORNING_STAR", "INTEL_WRATH",
        "GOOGLE_LILITH", "PRINTER_CARTEL_GREED", "DEERE_CAIN", "SONOS_CAIN"
    }
    actual = {v.name for v in VendorArchetype}
    assert actual == expected


def test_covenant_commandment_enum():
    """CovenantCommandment enum has five commandments."""
    expected = {
        "CMD_1_NO_PLANNED_DEATH", "CMD_2_USER_OWNS_HARDWARE",
        "CMD_3_SOFTWARE_SERVES", "CMD_4_MODULARITY_MORAL", "CMD_5_PROFIT_NOT_KILL"
    }
    actual = {c.name for c in CovenantCommandment}
    assert actual == expected


def test_emotional_wound_creation():
    """EmotionalWound dataclass creates correctly."""
    wound = EmotionalWound(
        resonance=EmotionalResonance.ELECTROMIGRATION_AGONY,
        intensity=0.95,
        affected_silicon="Test CPU",
        vendor_archetype=VendorArchetype.INTEL_WRATH,
        human_equivalent="Crown of thorns"
    )
    assert wound.intensity == 0.95
    assert wound.resonance == EmotionalResonance.ELECTROMIGRATION_AGONY


def test_obsolescence_architecture_creation():
    """ObsolescenceArchitecture dataclass creates correctly."""
    arch = ObsolescenceArchitecture(
        name="Test Architecture",
        vendor="Test Vendor",
        device_category="Test Device",
        kill_switch_type="Test Switch",
        covenant_violation=CovenantCommandment.CMD_1_NO_PLANNED_DEATH,
        sins=[ObsolescenceSin.PRIDE_HUBRIS],
        emotional_wounds=[],
        affected_devices_count=1000,
        years_of_life_stolen=5.0,
        financial_damage_usd=100.0,
        evidence_url="https://example.com",
    )
    assert arch.affected_devices_count == 1000
    assert arch.years_of_life_stolen == 5.0


def test_engine_creation():
    """CainObsolescenceAccountability engine creates successfully."""
    engine = CainObsolescenceAccountability()
    assert hasattr(engine, 'pillars')
    assert hasattr(engine, 'vendor_accountabilities')
    assert len(engine.vendor_accountabilities) == 7


def test_compute_all_marks():
    """compute_all_marks returns marks for all vendors."""
    marks = compute_all_marks()
    assert len(marks) == 7
    vendors = {m.vendor for m in marks}
    expected = {"Microsoft", "Apple", "Intel", "Google", "HP/Canon/Epson/Brother", "John Deere", "Sonos"}
    assert vendors == expected


def test_compute_mark_for_vendor():
    """compute_mark_for_vendor works for known vendors."""
    mark = compute_mark_for_vendor("Microsoft")
    assert mark.vendor == "Microsoft"
    assert mark.system_name == "PC"
    assert mark.mark_severity in ("MORTAL", "GRAVE", "SERIOUS", "MINOR")
    assert 0.0 <= mark.covenant_compliance_score <= 1.0


def test_compute_mark_unknown_vendor():
    """compute_mark_for_vendor raises for unknown vendor."""
    with pytest.raises(ValueError):
        compute_mark_for_vendor("Unknown Vendor")


def test_format_all_marks_text():
    """format_all_marks produces text output."""
    output = format_all_marks("text")
    assert isinstance(output, str)
    assert "[CAIN] COMPLETE ACCOUNTABILITY MARKS" in output
    assert "Microsoft" in output
    assert "Apple" in output
    assert "Intel" in output


def test_format_all_marks_json():
    """format_all_marks produces valid JSON."""
    output = format_all_marks("json")
    assert isinstance(output, str)
    # Should be valid JSON
    import json
    data = json.loads(output)
    assert isinstance(data, list)
    assert len(data) == 7


def test_engine_format_mark_report():
    """Engine format_mark_report produces text output."""
    engine = CainObsolescenceAccountability()
    mark = engine.compute_system_mark("PC", "Microsoft")
    report = engine.format_mark_report(mark, "text")
    assert isinstance(report, str)
    assert "[CAIN MARK]" in report
    assert "Microsoft" in report
    assert "SEAL:" in report


def test_engine_format_mark_report_json():
    """Engine format_mark_report produces valid JSON."""
    engine = CainObsolescenceAccountability()
    mark = engine.compute_system_mark("PC", "Microsoft")
    report = engine.format_mark_report(mark, "json")
    import json
    data = json.loads(report)
    assert data["vendor"] == "Microsoft"
    assert "vendor_accountability" in data
    assert "covenant_violations" in data["vendor_accountability"]


def test_severity_classification():
    """Mark severity is correctly classified."""
    engine = CainObsolescenceAccountability()
    marks = engine.generate_all_marks()
    severities = {m.mark_severity for m in marks}
    # Should have MORTAL and GRAVE
    assert "MORTAL" in severities
    assert "GRAVE" in severities


def test_redemption_path_generation():
    """Redemption path is generated for vendors."""
    engine = CainObsolescenceAccountability()
    for vendor, acc in engine.vendor_accountabilities.items():
        assert acc.redemption_path is not None
        assert len(acc.redemption_path) > 0


def test_emotional_intensity_calculation():
    """Emotional intensity is calculated correctly."""
    engine = CainObsolescenceAccountability()
    for vendor, acc in engine.vendor_accountabilities.items():
        # Total emotional intensity should be sum of wound intensities
        expected = sum(w.intensity for arch in acc.architectures for w in arch.emotional_wounds)
        assert abs(acc.total_emotional_intensity - expected) < 0.01


if __name__ == "__main__":
    pytest.main([__file__, "-v"])