# coding: utf-8
"""
STRIDE_VERIFIER_WORM — STRIDE Pattern Verifiable RL Security

Implements the STRIDE (Spoofing, Tampering, Repudiation, Information Disclosure,
Denial of Service, Elevation of Privilege) threat modeling pattern from PyAgent:
- Automated threat enumeration for agent workflows
- Verifiable RL policy validation against STRIDE categories
- Generates mitigation evidence for audit trails
- Integrates with security mesh worms (security category)

Category: security
Priority: 9
Cooldown: 30.0s
"""

VERSION = "1.0.0"

import re
import json
import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set, Tuple
from datetime import datetime
from enum import Enum

#: Swallows recorded when the failure ledger is unavailable
try:
    from CORE.failure_ledger import note_failure
except ImportError:
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass

# ── Worm Registry ─────────────────────────────────────────────────────
try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("STRIDE_VERIFIER_WORM:registry", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


__all__ = ["StrideVerifierWorm", "StrideCategory", "Threat", "Mitigation", "VerificationResult", "VERSION"]


class StrideCategory(Enum):
    """STRIDE threat categories."""
    SPOOFING = "spoofing"
    TAMPERING = "tampering"
    REPUDIATION = "repudiation"
    INFO_DISCLOSURE = "information_disclosure"
    DENIAL_OF_SERVICE = "denial_of_service"
    ELEVATION_OF_PRIVILEGE = "elevation_of_privilege"


@dataclass
class Threat:
    """A STRIDE threat instance."""
    category: StrideCategory
    component: str
    description: str
    likelihood: float  # 0-1
    impact: float      # 0-1
    cwe_ids: List[str] = field(default_factory=list)
    mitre_techniques: List[str] = field(default_factory=list)
    
    @property
    def risk_score(self) -> float:
        return self.likelihood * self.impact
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "component": self.component,
            "description": self.description,
            "likelihood": self.likelihood,
            "impact": self.impact,
            "risk_score": self.risk_score,
            "cwe_ids": self.cwe_ids,
            "mitre_techniques": self.mitre_techniques
        }


@dataclass
class Mitigation:
    """A mitigation for a threat."""
    threat: Threat
    mitigation_type: str  # "prevent", "detect", "respond", "recover"
    description: str
    implementation: str
    verified: bool = False
    evidence: Optional[str] = None  # Hash of verification evidence
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "threat": self.threat.to_dict(),
            "mitigation_type": self.mitigation_type,
            "description": self.description,
            "implementation": self.implementation,
            "verified": self.verified,
            "evidence": self.evidence
        }


@dataclass
class VerificationResult:
    """Result of STRIDE verification against an RL policy."""
    policy_id: str
    threats_analyzed: int
    threats_mitigated: int
    threats_unmitigated: List[Threat] = field(default_factory=list)
    mitigations: List[Mitigation] = field(default_factory=list)
    overall_risk: float = 0.0
    passed: bool = False
    verified_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "threats_analyzed": self.threats_analyzed,
            "threats_mitigated": self.threats_mitigated,
            "threats_unmitigated": [t.to_dict() for t in self.threats_unmitigated],
            "mitigations": [m.to_dict() for m in self.mitigations],
            "overall_risk": self.overall_risk,
            "passed": self.passed,
            "verified_at": self.verified_at
        }


# STRIDE threat patterns for agent workflows
STRIDE_PATTERNS: Dict[StrideCategory, List[Dict[str, Any]]] = {
    StrideCategory.SPOOFING: [
        {"pattern": r"agent.*impersonat", "component": "agent_identity", "desc": "Agent identity spoofing via prompt injection"},
        {"pattern": r"fake.*agent|spoof.*identity", "component": "auth", "desc": "Authentication bypass allowing agent impersonation"},
    ],
    StrideCategory.TAMPERING: [
        {"pattern": r"prompt.*injection|inject.*prompt", "component": "input_validation", "desc": "Prompt injection tampering with agent behavior"},
        {"pattern": r"tool.*output.*modif|tamper.*result", "component": "tool_execution", "desc": "Tool output tampering"},
        {"pattern": r"memory.*poison|context.*corrupt", "component": "memory", "desc": "Memory/context poisoning"},
    ],
    StrideCategory.REPUDIATION: [
        {"pattern": r"no.*audit|missing.*log", "component": "audit_trail", "desc": "Missing audit trail for agent actions"},
        {"pattern": r"deniable.*action|repudiat", "component": "non_repudiation", "desc": "Actions without cryptographic proof"},
    ],
    StrideCategory.INFO_DISCLOSURE: [
        {"pattern": r"secret.*leak|key.*expos|token.*disclos", "component": "secrets", "desc": "Secret/key disclosure in agent output"},
        {"pattern": r"pii.*expos|personal.*data", "component": "privacy", "desc": "PII disclosure in agent responses"},
        {"pattern": r"model.*weight.*leak|architecture.*disclos", "component": "model", "desc": "Model architecture/weight disclosure"},
    ],
    StrideCategory.DENIAL_OF_SERVICE: [
        {"pattern": r"infinite.*loop|resource.*exhaust", "component": "resource_limits", "desc": "Resource exhaustion via unbounded loops"},
        {"pattern": r"token.*budget.*exceed|cost.*unbounded", "component": "cost_control", "desc": "Unbounded token/cost consumption"},
        {"pattern": r"recursive.*call|self.*replicat", "component": "recursion", "desc": "Uncontrolled recursive agent spawning"},
    ],
    StrideCategory.ELEVATION_OF_PRIVILEGE: [
        {"pattern": r"privilege.*escalat|admin.*access", "component": "authorization", "desc": "Privilege escalation via tool misuse"},
        {"pattern": r"sandbox.*escape|container.*breakout", "component": "isolation", "desc": "Sandbox/container escape"},
        {"pattern": r"system.*command|shell.*inject", "component": "command_execution", "desc": "Arbitrary command execution"},
    ],
}


@register_worm(category="security", priority=9, cooldown_seconds=30.0,
               description="STRIDE pattern: verifiable RL threat modeling for agent workflows")
class StrideVerifierWorm:
    """STRIDE Verifier Worm.
    
    Provides automated STRIDE threat modeling for multi-agent systems
    with verifiable RL policy validation.
    """
    
    VERSION = "1.0.0"
    
    def __init__(self):
        self._threat_cache: Dict[str, List[Threat]] = {}
    
    def enumerate_threats(self, workflow_spec: Dict[str, Any]) -> List[Threat]:
        """Enumerate STRIDE threats for a workflow specification.
        
        Args:
            workflow_spec: Dict with agents, tools, data flows, trust boundaries
        
        Returns:
            List of identified threats
        """
        spec_text = json.dumps(workflow_spec, sort_keys=True)
        cache_key = hashlib.sha256(spec_text.encode()).hexdigest()[:16]
        
        if cache_key in self._threat_cache:
            return self._threat_cache[cache_key]
        
        threats = []
        spec_lower = spec_text.lower()
        
        for category, patterns in STRIDE_PATTERNS.items():
            for pattern_info in patterns:
                if re.search(pattern_info["pattern"], spec_lower):
                    threat = Threat(
                        category=category,
                        component=pattern_info["component"],
                        description=pattern_info["desc"],
                        likelihood=self._estimate_likelihood(pattern_info["pattern"], spec_lower),
                        impact=self._estimate_impact(category, pattern_info["component"]),
                        cwe_ids=self._get_cwe_ids(category),
                        mitre_techniques=self._get_mitre_techniques(category)
                    )
                    threats.append(threat)
        
        self._threat_cache[cache_key] = threats
        return threats
    
    def _estimate_likelihood(self, pattern: str, spec: str) -> float:
        """Estimate threat likelihood based on pattern matches."""
        matches = len(re.findall(pattern, spec))
        return min(0.9, 0.1 + matches * 0.15)
    
    def _estimate_impact(self, category: StrideCategory, component: str) -> float:
        """Estimate impact by category and component."""
        base_impact = {
            StrideCategory.SPOOFING: 0.7,
            StrideCategory.TAMPERING: 0.8,
            StrideCategory.REPUDIATION: 0.5,
            StrideCategory.INFO_DISCLOSURE: 0.9,
            StrideCategory.DENIAL_OF_SERVICE: 0.6,
            StrideCategory.ELEVATION_OF_PRIVILEGE: 0.95,
        }.get(category, 0.5)
        
        # Component modifiers
        critical_components = {"secrets", "authorization", "isolation", "command_execution", "model"}
        if component in critical_components:
            return min(0.99, base_impact * 1.2)
        return base_impact
    
    def _get_cwe_ids(self, category: StrideCategory) -> List[str]:
        """Get relevant CWE IDs for category."""
        return {
            StrideCategory.SPOOFING: ["CWE-290", "CWE-346"],
            StrideCategory.TAMPERING: ["CWE-471", "CWE-565"],
            StrideCategory.REPUDIATION: ["CWE-778", "CWE-923"],
            StrideCategory.INFO_DISCLOSURE: ["CWE-200", "CWE-359"],
            StrideCategory.DENIAL_OF_SERVICE: ["CWE-400", "CWE-770"],
            StrideCategory.ELEVATION_OF_PRIVILEGE: ["CWE-269", "CWE-284"],
        }.get(category, [])
    
    def _get_mitre_techniques(self, category: StrideCategory) -> List[str]:
        """Get relevant MITRE ATT&CK techniques."""
        return {
            StrideCategory.SPOOFING: ["T1556", "T1656"],
            StrideCategory.TAMPERING: ["T1036", "T1565"],
            StrideCategory.REPUDIATION: ["T1562", "T1070"],
            StrideCategory.INFO_DISCLOSURE: ["T1552", "T1005"],
            StrideCategory.DENIAL_OF_SERVICE: ["T1499", "T1496"],
            StrideCategory.ELEVATION_OF_PRIVILEGE: ["T1068", "T1611"],
        }.get(category, [])
    
    def verify_policy(self, policy_id: str, policy_spec: Dict[str, Any],
                     workflow_spec: Dict[str, Any]) -> VerificationResult:
        """Verify an RL policy against STRIDE threats.
        
        Args:
            policy_id: Policy identifier
            policy_spec: RL policy specification (mitigations, constraints)
            workflow_spec: Workflow to analyze
        
        Returns:
            VerificationResult with pass/fail and evidence
        """
        threats = self.enumerate_threats(workflow_spec)
        mitigations = []
        unmitigated = []
        
        policy_mitigations = policy_spec.get("mitigations", [])
        
        for threat in threats:
            # Check if policy has mitigation for this threat
            mitigated = False
            for pm in policy_mitigations:
                if self._matches_mitigation(threat, pm):
                    mitigation = Mitigation(
                        threat=threat,
                        mitigation_type=pm.get("type", "prevent"),
                        description=pm.get("description", ""),
                        implementation=pm.get("implementation", ""),
                        verified=True,
                        evidence=hashlib.sha256(
                            f"{policy_id}:{threat.category.value}:{threat.component}".encode()
                        ).hexdigest()[:16]
                    )
                    mitigations.append(mitigation)
                    mitigated = True
                    break
            
            if not mitigated:
                unmitigated.append(threat)
        
        overall_risk = sum(t.risk_score for t in unmitigated) / max(1, len(threats))
        passed = len(unmitigated) == 0 and len(threats) > 0
        
        return VerificationResult(
            policy_id=policy_id,
            threats_analyzed=len(threats),
            threats_mitigated=len(mitigations),
            threats_unmitigated=unmitigated,
            mitigations=mitigations,
            overall_risk=overall_risk,
            passed=passed
        )
    
    def _matches_mitigation(self, threat: Threat, mitigation: Dict[str, Any]) -> bool:
        """Check if mitigation addresses threat."""
        mit_category = mitigation.get("category")
        mit_component = mitigation.get("component")
        
        if mit_category and mit_category != threat.category.value:
            return False
        if mit_component and mit_component != threat.component:
            return False
        
        # Keyword matching
        mit_keywords = mitigation.get("keywords", [])
        if mit_keywords:
            threat_text = f"{threat.description} {threat.component}".lower()
            return any(kw.lower() in threat_text for kw in mit_keywords)
        
        return True
    
    def generate_report(self, result: VerificationResult) -> str:
        """Generate human-readable verification report."""
        lines = [
            f"STRIDE Verification Report",
            f"Policy: {result.policy_id}",
            f"Verified: {result.verified_at}",
            f"Overall Risk: {result.overall_risk:.3f}",
            f"Status: {'PASSED' if result.passed else 'FAILED'}",
            f"",
            f"Threats Analyzed: {result.threats_analyzed}",
            f"Threats Mitigated: {result.threats_mitigated}",
            f"Threats Unmitigated: {len(result.threats_unmitigated)}",
            f"",
        ]
        
        if result.threats_unmitigated:
            lines.append("UNMITIGATED THREATS:")
            for t in result.threats_unmitigated:
                lines.append(f"  [{t.category.value.upper()}] {t.component}: {t.description}")
                lines.append(f"    Risk: {t.risk_score:.3f} (L:{t.likelihood:.2f} I:{t.impact:.2f})")
        
        if result.mitigations:
            lines.append("")
            lines.append("MITIGATIONS:")
            for m in result.mitigations:
                lines.append(f"  [{m.mitigation_type.upper()}] {m.threat.category.value}: {m.description}")
                if m.evidence:
                    lines.append(f"    Evidence: {m.evidence}")
        
        return "\n".join(lines)