"""
master_catalog.py — Unified pattern catalog combining all pattern types.

Combines:
- AST patterns (852 types)
- Import patterns (679 types) 
- Decorator patterns (28 types)
- Graph motifs by relation (7 relation groups, up to 50K each)
- BCC network motifs (from true cosmos)
- All enriched with 4 metadata schemas (d_grade, gap, lead, audit_loop)
"""

import json
import sys
import time
from pathlib import Path
from collections import defaultdict, Counter
from typing import Any, Dict, List
from dataclasses import dataclass, field, asdict


# ── Unified Metadata Schema (from our 4 schemas) ────────────────────────────

@dataclass
class PatternMetadata:
    """Unified metadata for all pattern types."""
    pattern_id: str
    pattern_type: str
    source_category: str  # "code", "network", "bcc", "training", "memory"
    
    # Core metrics
    count: int = 0
    priority: int = 0
    confidence_score: float = 0.0
    
    # D-grade edge metadata
    source_type: str = ""
    retrieval_method: str = ""
    universe: str = ""
    verifier: str = ""
    tier_evidence: List[str] = field(default_factory=list)
    status: str = "unsaved"
    
    # Gap metadata
    records_action: str = ""
    unblocker: str = ""
    evidence_completeness: float = 0.0
    corroboration_count: int = 0
    
    # Lead metadata
    corroboration_layers: List[str] = field(default_factory=list)
    records_action_lead: str = ""
    verification_method: str = ""
    evidence_strength: float = 0.0
    
    # Audit loop metadata
    last_heartbeat: str = ""
    bound_violated: str = ""
    superseded_by: str = ""
    recovery_action: str = ""
    
    # Structural
    structural_data: Dict[str, Any] = field(default_factory=dict)
    
    def compute_priority(self) -> int:
        """Unified priority computation - discriminating version."""
        import math
        score = 0
        # Base confidence (0-20)
        score += self.confidence_score * 20
        # Evidence strength (0-15)
        score += self.evidence_strength * 15
        # Corroboration count (0-10, log scale)
        if self.corroboration_count > 0:
            score += min(math.log10(self.corroboration_count + 1) * 5, 10)
        # Gap urgency (0-10)
        score += (1.0 - self.evidence_completeness) * 10
        # Count influence (0-5, log scale)
        if self.count > 1:
            score += min(math.log10(self.count) * 1.5, 5)
        # Scale to 1-5 range
        # Max possible: 20+15+10+10+5 = 60 -> map to 1-5
        return max(1, min(int(score / 12), 5))
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['priority'] = self.compute_priority()
        return d


# ── Loaders ────────────────────────────────────────────────────────────────

def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def build_master_catalog() -> Dict[str, Any]:
    """Build the master unified catalog."""
    
    catalog = {
        "metadata": {
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "version": "1.0.0",
            "pattern_types": {},
            "total_patterns": 0,
        },
        "patterns": {},
        "by_type": defaultdict(list),
        "by_priority": defaultdict(list),
        "by_source_category": defaultdict(list),
    }
    
    pattern_id = 0
    
    # 1. Load AST patterns
    print("Loading AST patterns...")
    pattern_catalog = load_json(Path(r"C:\Azazael\azazael\research\pattern_catalog.json"))
    for pattern, count in pattern_catalog.get("ast_patterns", {}).items():
        # Vary metadata based on count and pattern type
        corr_count = min(int(count / 100), 10)
        evidence = min(0.5 + (count / 5000), 0.9)
        meta = PatternMetadata(
            pattern_id=f"ast_{pattern_id}",
            pattern_type="ast_pattern",
            source_category="code",
            count=count,
            source_type="code",
            retrieval_method="static_analysis",
            universe="codebase",
            verifier="ast",
            tier_evidence=["ast_parse"],
            confidence_score=0.85,
            corroboration_count=corr_count,
            evidence_strength=evidence,
            evidence_completeness=0.8,
            structural_data={"ast_pattern": pattern}
        )
        catalog["patterns"][meta.pattern_id] = meta.to_dict()
        catalog["by_type"]["ast_pattern"].append(meta.pattern_id)
        catalog["by_source_category"]["code"].append(meta.pattern_id)
        pattern_id += 1
    
    # 2. Load Import patterns
    print("Loading Import patterns...")
    for pattern, count in pattern_catalog.get("import_patterns", {}).items():
        corr_count = min(int(count / 50), 10)
        evidence = min(0.6 + (count / 3000), 0.95)
        meta = PatternMetadata(
            pattern_id=f"import_{pattern_id}",
            pattern_type="import_pattern",
            source_category="code",
            count=count,
            source_type="code",
            retrieval_method="static_analysis",
            universe="codebase",
            verifier="ast_import",
            tier_evidence=["ast_parse"],
            confidence_score=0.95,
            corroboration_count=corr_count,
            evidence_strength=evidence,
            evidence_completeness=0.85,
            structural_data={"import_pattern": pattern}
        )
        catalog["patterns"][meta.pattern_id] = meta.to_dict()
        catalog["by_type"]["import_pattern"].append(meta.pattern_id)
        catalog["by_source_category"]["code"].append(meta.pattern_id)
        pattern_id += 1
    
    # 3. Load Decorator patterns
    print("Loading Decorator patterns...")
    for pattern, count in pattern_catalog.get("decorator_patterns", {}).items():
        corr_count = min(int(count / 20), 10)
        evidence = 0.8
        meta = PatternMetadata(
            pattern_id=f"decorator_{pattern_id}",
            pattern_type="decorator_pattern",
            source_category="code",
            count=count,
            source_type="code",
            retrieval_method="static_analysis",
            universe="codebase",
            verifier="ast_decorator",
            tier_evidence=["ast_parse"],
            confidence_score=0.9,
            corroboration_count=corr_count,
            evidence_strength=evidence,
            evidence_completeness=0.9,
            structural_data={"decorator_pattern": pattern}
        )
        catalog["patterns"][meta.pattern_id] = meta.to_dict()
        catalog["by_type"]["decorator_pattern"].append(meta.pattern_id)
        catalog["by_source_category"]["code"].append(meta.pattern_id)
        pattern_id += 1
    
    # 4. Load Graph motifs by relation
    print("Loading Graph motifs by relation...")
    motif_results = load_json(Path(r"C:\Azazael\azazael\research\motif_census_by_relation.json"))
    for rel_group, data in motif_results.items():
        motifs = data.get("motifs", {}).get("motif_counts", {})
        for motif, count in motifs.items():
            corr_count = min(int(count / 1000), 10)
            evidence = 0.9
            meta = PatternMetadata(
                pattern_id=f"motif_{pattern_id}",
                pattern_type="graph_motif",
                source_category="network",
                count=count,
                source_type="code",
                retrieval_method="esu_enumeration",
                universe="codebase_graphify",
                verifier="esu",
                tier_evidence=[f"graphify:{'+'.join(data['relation_group'])}"],
                confidence_score=0.9,
                corroboration_count=corr_count,
                evidence_strength=evidence,
                evidence_completeness=0.7,
                structural_data={
                    "motif": motif,
                    "relation_group": data["relation_group"],
                    "k_size": int(motif.split(':')[0][1:]) if ':' in motif else 0
                }
            )
            catalog["patterns"][meta.pattern_id] = meta.to_dict()
            catalog["by_type"]["graph_motif"].append(meta.pattern_id)
            catalog["by_source_category"]["network"].append(meta.pattern_id)
            pattern_id += 1
    
    # 5. Load BCC network motifs
    print("Loading BCC network motifs...")
    bcc_motifs = load_json(Path(r"C:\Azazael\azazael\research\bcc_analysis\motif_census_results.json"))
    for motif, info in bcc_motifs.get("motifs", {}).items():
        meta = PatternMetadata(
            pattern_id=f"bcc_{pattern_id}",
            pattern_type="bcc_network_motif",
            source_category="bcc",
            count=info["count"],
            source_type="investigation",
            retrieval_method="esu_enumeration",
            universe="bcc_person_network",
            verifier="esu",
            tier_evidence=["bcc_true_cosmos"],
            confidence_score=0.95,
            corroboration_count=3,
            evidence_strength=0.95,
            evidence_completeness=0.9,
            structural_data={
                "motif": motif,
                "description": info.get("description", ""),
                "k_size": info.get("nodes", 0),
                "edge_count": info.get("edges", 0)
            }
        )
        catalog["patterns"][meta.pattern_id] = meta.to_dict()
        catalog["by_type"]["bcc_network_motif"].append(meta.pattern_id)
        catalog["by_source_category"]["bcc"].append(meta.pattern_id)
        pattern_id += 1
    
    # 6. Add some training pipeline patterns (from trainers_v2)
    print("Adding training pipeline patterns...")
    training_patterns = [
        ("trainer_context", "trainer_context_v2", "Training context with worm identity"),
        ("hook_aware_trainer", "hook_aware_trainer_base", "Base trainer with worm hooks"),
        ("rl_train_loop_v2", "rl_train_loop_v2", "Q-learning with worm identity"),
        ("grpo_trainer_v2", "grpo_trainer_v2", "GRPO with worm identity"),
        ("sb3_trainer_v2", "sb3_trainer_v2", "SB3 wrapper with worm identity"),
        ("trainer_factory", "create_trainer_factory", "Factory for worm-aware trainers"),
        ("intake_cycle_v2", "run_intake_cycle_v2", "Intake cycle with worm identity"),
        ("grpo_cycle_v2", "run_grpo_cycle_v2", "GRPO cycle with worm identity"),
    ]
    for pid, ptype, desc in training_patterns:
        meta = PatternMetadata(
            pattern_id=f"training_{pattern_id}",
            pattern_type=ptype,
            source_category="training",
            count=1,
            source_type="code",
            retrieval_method="static_analysis",
            universe="training_pipeline",
            verifier="code_review",
            tier_evidence=["trainers_v2"],
            confidence_score=0.9,
            corroboration_count=5,
            evidence_strength=0.85,
            evidence_completeness=0.8,
            structural_data={"description": desc}
        )
        catalog["patterns"][meta.pattern_id] = meta.to_dict()
        catalog["by_type"]["training_pattern"].append(meta.pattern_id)
        catalog["by_source_category"]["training"].append(meta.pattern_id)
        pattern_id += 1
    
    # 7. Add memory event patterns
    print("Adding memory event patterns...")
    memory_patterns = [
        ("sb3_training_complete", "sb3_training_event", "SB3 training completion event"),
        ("trainer_error", "trainer_error_event", "Trainer error recording"),
        ("episode_complete", "episode_complete_event", "RL episode completion"),
        ("q_table_snapshot", "q_table_snapshot_event", "Q-table persistence"),
        ("policy_update", "policy_update_event", "GRPO policy update"),
        ("group_generated", "group_generated_event", "GRPO group generation"),
        ("group_rewards", "group_rewards_event", "GRPO reward computation"),
        ("intake_training_orchestrated", "intake_orchestrated_event", "Intake cycle orchestration"),
    ]
    for pid, ptype, desc in memory_patterns:
        meta = PatternMetadata(
            pattern_id=f"memory_{pattern_id}",
            pattern_type=ptype,
            source_category="memory",
            count=1,
            source_type="runtime",
            retrieval_method="runtime_observation",
            universe="memory_bus",
            verifier="event_schema",
            tier_evidence=["memory_bus"],
            confidence_score=0.85,
            corroboration_count=3,
            evidence_strength=0.8,
            evidence_completeness=0.7,
            structural_data={"description": desc}
        )
        catalog["patterns"][meta.pattern_id] = meta.to_dict()
        catalog["by_type"]["memory_event"].append(meta.pattern_id)
        catalog["by_source_category"]["memory"].append(meta.pattern_id)
        pattern_id += 1
    
    # Finalize
    catalog["metadata"]["total_patterns"] = len(catalog["patterns"])
    catalog["metadata"]["pattern_types"] = {k: len(v) for k, v in catalog["by_type"].items()}
    
    # Build priority index
    for pid, pdata in catalog["patterns"].items():
        catalog["by_priority"][pdata["priority"]].append(pid)
    
    return catalog


def main():
    print("Building master unified catalog...")
    catalog = build_master_catalog()
    
    print(f"\nTotal patterns: {catalog['metadata']['total_patterns']}")
    print("By type:")
    for ptype, pids in catalog["by_type"].items():
        print(f"  {ptype}: {len(pids)}")
    print("By source category:")
    for cat, pids in catalog["by_source_category"].items():
        print(f"  {cat}: {len(pids)}")
    print("By priority:")
    for pri in sorted(catalog["by_priority"].keys()):
        print(f"  priority {pri}: {len(catalog['by_priority'][pri])}")
    
    # Save
    output = Path(r"C:\Azazael\azazael\research\master_pattern_catalog.json")
    output.write_text(json.dumps(catalog, indent=2, default=str))
    print(f"\nMaster catalog saved to {output}")
    
    # Also save a summary
    summary = {
        "metadata": catalog["metadata"],
        "by_type": {k: len(v) for k, v in catalog["by_type"].items()},
        "by_source_category": {k: len(v) for k, v in catalog["by_source_category"].items()},
        "by_priority": {k: len(v) for k, v in catalog["by_priority"].items()},
        "sample_patterns": {k: catalog["patterns"][k] for k in list(catalog["patterns"].keys())[:10]}
    }
    summary_output = Path(r"C:\Azazael\azazael\research\master_pattern_catalog_summary.json")
    summary_output.write_text(json.dumps(summary, indent=2, default=str))
    print(f"Summary saved to {summary_output}")


if __name__ == "__main__":
    main()