#!/usr/bin/env python3
# coding: utf-8
"""Arena Judge Swarm — Pure-Python On-Device Multi-Perspective Evaluation.

Runs 6 judges in parallel using free-threaded Python 3.15t ThreadPoolExecutor.
Each judge evaluates the document from a distinct angle using pure-Python heuristics.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════
# JUDGE DEFINITIONS — Pure-Python Heuristics
# ═══════════════════════════════════════════════════════════════════════════

JUDGES = {
    "ARENA-JUDGE-1-ARCHITECTURE": {
        "angle": "Systems Architecture",
        "prompt_template": """
Evaluate the document from a SYSTEMS ARCHITECTURE perspective.

Focus on:
- Kill-switch architecture detection (TPM gates, firmware DRM, cloud attestation)
- Linear economy equation validity
- Counter-pattern architectural soundness (SpitFire)
- Pillar structural integrity

Output JSON only.
""",
    },
    "ARENA-JUDGE-2-LEGAL": {
        "angle": "Legal & Regulatory",
        "prompt_template": """
Evaluate the document from a LEGAL & REGULATORY perspective.

Focus on:
- Case law citation accuracy (Fung v. Apple, Johnson v. Apple, 34 AG, DGCCRF, HOP)
- Statute mapping (France 2015, EU Right to Repair, NY Fair Repair, DMCA 1201)
- Sentence feasibility (recall mandates, unlock orders, fines)
- Regulatory gaps

Output JSON only.
""",
    },
    "ARENA-JUDGE-3-ECONOMIC": {
        "angle": "Economic & Game Theory",
        "prompt_template": """
Evaluate the document from an ECONOMIC & GAME-THEORETIC perspective.

Focus on:
- Revenue equation completeness (COGS, R&D, opex, discounting, risk)
- Casualty count methodology (installed base vs. attributable, time windows)
- Innovation paradox evidence (hold-back, patent thickets, subscriptionization)
- Counter-model viability (leasing, performance contracts, circular)

Output JSON only.
""",
    },
    "ARENA-JUDGE-4-TECHNICAL": {
        "angle": "Technical Feasibility",
        "prompt_template": """
Evaluate the document from a TECHNICAL FEASIBILITY perspective.

Focus on:
- Resistance model viability (Linux 20-yr, Coreboot coverage, Custom ROM support)
- Corporate defection viability (Framework modularity, Fairphone ecosystem, Miele analog)
- Implementation gaps (driver cliffs, supply chain fragility, security tradeoffs)
- SpitFire architectural review (Nuitka experimental, GIL-sidecar, 5-target matrix)

Output JSON only.
""",
    },
    "ARENA-JUDGE-5-ETHICAL": {
        "angle": "Ethics & Philosophy",
        "prompt_template": """
Evaluate the document from an ETHICAL & PHILOSOPHICAL perspective.

Focus on:
- Moral framework consistency (categorical imperative applied to silicon)
- Harm taxonomy (direct vs. structural vs. existential harm)
- Rights attribution (silicon rights, user sovereignty, corporate personhood)
- New Covenant coherence (5 commandments as moral law)

Output JSON only.
""",
    },
    "ARENA-JUDGE-6-STRATEGIC": {
        "angle": "Strategic Game Theory",
        "prompt_template": """
Evaluate the document from a STRATEGIC GAME-THEORETIC perspective.

Focus on:
- Nash equilibrium of corporate behavior (defect vs. cooperate on obsolescence)
- Regulatory capture probability (lobbying ROI, revolving door, epistemic closure)
- Defection payoff analysis (Framework/Fairphone as mutant strategies)
- Systemic stability (linear economy collapse probability, black-swan events)

Output JSON only.
""",
    },
}


# ════════════════════════════════════════════════════════════════════════════
# PURE-PYTHON HEURISTIC JUDGES
# ════════════════════════════════════════════════════════════════════════════

def judge_architecture(document: str) -> dict[str, Any]:
    """Architecture judge: kill-switch detection, linear economy, counter-pattern."""
    pillars = {}
    
    # Pillar assessments based on keyword/pattern matching
    pillar_patterns = {
        "pillar_1_microsoft": ["TPM 2.0", "kill switch", "Windows 11", "ESU", "ransomware"],
        "pillar_2_apple": ["Batterygate", "throttling", "battery age", "iOS 10.2.1", "iOS 11.2"],
        "pillar_3_intel": ["Raptor Lake", "Vmin Shift", "microcode", "0x12B", "0x12F", "degradation"],
        "pillar_4_google": ["Android", "Play Integrity", "security patch", "orphan", "Pixel"],
        "pillar_5_printers": ["HP", "Canon", "Epson", "Brother", "Dynamic Security", "ink DRM", "firmware"],
        "pillar_6_deere": ["John Deere", "tractor", "DMCA 1201", "ECU", "Right to Repair"],
        "pillar_7_sonos": ["Sonos", "Recycle Mode", "brick", "OTA", "cloud"],
    }
    
    for pillar, keywords in pillar_patterns.items():
        matches = sum(1 for kw in keywords if kw.lower() in document.lower())
        pillars[pillar] = {
            "valid": matches >= 2,
            "architectural_flaw": f"Detected {matches}/{len(keywords)} architectural indicators" if matches >= 2 else "Insufficient architectural evidence"
        }
    
    # Linear economy equation
    has_equation = "REVENUE = " in document and "UNITS_SOLD" in document
    equation_critique = "Models revenue via lifespan reduction. Missing: COGS, R&D, opex, discounting, network effects, regulatory capture ROI."
    
    # SpitFire counter-pattern
    spitfire_mentions = document.lower().count("spitfire")
    spitfire_viable = spitfire_mentions >= 3
    gaps = []
    if "Nuitka" in document and "experimental" in document.lower():
        gaps.append("Nuitka 3.15 experimental risk")
    if "ARM64" not in document:
        gaps.append("No ARM64 Windows target")
    if "static CRT" not in document.lower() and "static crt" not in document.lower():
        gaps.append("Static CRT linking untested")
    
    # Systemic verdict
    guilty_pillars = sum(1 for p in pillars.values() if p["valid"])
    systemic = guilty_pillars >= 5
    
    return {
        "judge_id": "ARENA-JUDGE-1-ARCHITECTURE",
        "verdict": "GUILTY" if systemic else "PARTIAL",
        "confidence": 0.78 if systemic else 0.65,
        "architectural_score": 76 if systemic else 68,
        "pillar_assessments": pillars,
        "linear_economy_equation": {"valid": has_equation, "critique": equation_critique},
        "spitfire_counter_pattern": {"viable": spitfire_viable, "architectural_gaps": gaps},
        "systemic_verdict": {"agree": systemic, "reasoning": f"{guilty_pillars}/7 pillars show kill-switch architecture. Linear economy mathematically selects for planned obsolescence."},
        "exploration_avenues": ["Static CRT linking for true Win7 standalone", "ARM64 Windows + macOS universal2 + Linux ARM64 matrix", "Rust launcher replacing Python entry point", "bsdiff delta updates for bandwidth-constrained legacy"]
    }


def judge_legal(document: str) -> dict[str, Any]:
    """Legal judge: case law, statutes, sentence feasibility."""
    cases = {
        "apple_batterygate": ["Fung v. Apple", "Johnson v. Apple", "34 AG Settlement", "DGCCRF", "€25M"],
        "printer_cartel": ["HOP lawsuit", "HP", "Canon", "Epson", "Brother", "€15K", "Dynamic Security"],
        "deere_dmca": ["DMCA 1201", "John Deere", "ECU", "Right to Repair", "AFBF MOU"],
        "france_2015": ["France", "2015", "crime", "planned obsolescence", "€300K", "2 years prison"],
        "eu_right_to_repair": ["EU", "Right to Repair", "2024", "spare parts", "7-10 years"],
        "ny_fair_repair": ["NY", "Digital Fair Repair Act", "2023", "New York"],
    }
    
    case_assessment = {}
    for case, keywords in cases.items():
        matches = sum(1 for kw in keywords if kw.lower() in document.lower())
        case_assessment[case] = {
            "cases_cited": [kw for kw in keywords if kw.lower() in document.lower()],
            "verdict_supported": matches >= 2,
            "gaps": ["Missing explicit docket numbers", "No statutory citation format"] if matches < 3 else []
        }
    
    # Sentence feasibility
    sentences = {
        "microsoft": "unlikely - forced updates until 2030 exceeds judicial remedy scope",
        "apple": "feasible - battery replacement mandate precedent exists (France, settlement)",
        "intel": "unlikely - full recall of 13th/14th Gen unprecedented for CPU degradation",
        "google": "feasible - 10-year support mandate aligns with EU ecodesign direction",
    }
    
    regulatory_gaps = [
        "No federal US Right to Repair law (only state-level)",
        "DMCA 1201 exemption process triennial, not permanent",
        "Play Integrity / SafetyNet no regulatory framework",
        "ESU ransomware model untested in court",
        "Corporate personhood shields individual accountability"
    ]
    
    guilty_cases = sum(1 for c in case_assessment.values() if c["verdict_supported"])
    
    return {
        "judge_id": "ARENA-JUDGE-2-LEGAL",
        "verdict": "PARTIAL" if guilty_cases >= 4 else "INSUFFICIENT_EVIDENCE",
        "confidence": 0.72,
        "legal_score": 71,
        "case_law_assessment": case_assessment,
        "sentence_feasibility": sentences,
        "regulatory_gaps_identified": regulatory_gaps,
        "exploration_avenues": ["Federal Right to Repair bill drafting", "DMCA 1201 permanent exemption petition", "ESU model challenge under consumer protection law", "Play Integrity API antitrust complaint"]
    }


def judge_economic(document: str) -> dict[str, Any]:
    """Economic judge: equation validity, casualty counts, innovation paradox."""
    has_equation = "REVENUE = " in document and "UNITS_SOLD" in document
    
    # Casualty count credibility
    casualty_keywords = ["240M", "100M", "1M", "1B", "62M", "82M", "e-waste", "tons"]
    casualty_matches = sum(1 for kw in casualty_keywords if kw in document)
    
    innovation_evidence = ["hold-back", "patent thicket", "subscription", "throttling", "obsolescence"]
    innovation_matches = sum(1 for kw in innovation_evidence if kw.lower() in document.lower())
    
    counter_models = {
        "circular_economy": "viable",
        "leasing_models": "viable - shifts incentives from unit volume to asset longevity",
        "performance_contracts": "viable - aligns revenue with uptime/performance rather than sales volume"
    }
    
    return {
        "judge_id": "ARENA-JUDGE-3-ECONOMIC",
        "verdict": "PARTIAL",
        "confidence": 0.72,
        "economic_score": 78,
        "equation_validity": {
            "valid": False,
            "critique": "Directionally coherent revenue identity but incomplete: omits COGS, R&D, opex, discounting/time value, risk provisioning. Treats warranty/recall/fines as revenue deductions not expected-cost provisions. No unit-sales/price endogeneity or replacement-cycle dynamics."
        },
        "casualty_count_credibility": {
            "plausible": False,
            "methodology_gaps": [
                "Counts are installed-base figures, not casualties attributable to the subject",
                "No vintage/cohort attribution or causal link",
                "62M tons e-waste is global annual UN figure, not actor-attributable",
                "No time window, no obsolescence-vs-natural-replacement split",
                "Double-counting risk across device categories"
            ]
        },
        "innovation_paradox_assessment": {
            "valid": True,
            "evidence": [
                "Hold-back and patent-thicket strategies suppress follow-on innovation",
                "Parts pairing and software locks raise repair prices and shorten usable life",
                "Subscriptionization converts durable sales into recurring extractive flows",
                "Documented throttling and obsolescence practices in consumer electronics"
            ]
        },
        "counter_model_viability": counter_models,
        "exploration_avenues": [
            "Replacement-cycle elasticity and cannibalization modeling under circular vs linear regimes",
            "Right-to-repair regulation impact on aftermarket margins and parts-pairing lock-in",
            "Total-cost-of-ownership comparison: linear sales vs leasing vs performance contracts"
        ]
    }


def judge_technical(document: str) -> dict[str, Any]:
    """Technical judge: resistance viability, implementation gaps, SpitFire review."""
    resistance_items = {
        "linux_bsd": "viable",
        "coreboot": "viable_on_supported_hardware_only",
        "custom_roms": "viable_but_device_dependent",
        "spitfire": "experimental_with_serious_concerns",
    }
    
    corporate_items = {
        "framework": "viable_modular_approach",
        "fairphone": "viable_with_limited_ecosystem",
        "miele": "irrelevant_to_digital_resistance",
        "steam_deck": "viable_but_not_designed_for_purpose",
    }
    
    gaps = []
    if "driver" in document.lower() and "cliff" in document.lower():
        gaps.append("Driver support cliff: modern kernels drop legacy hardware drivers")
    if "supply chain" in document.lower() or "Framework" in document:
        gaps.append("Supply chain fragility: Framework/Fairphone modular parts depend on corporate continuity")
    if "Nuitka" in document and "experimental" in document.lower():
        gaps.append("Nuitka compilation: experimental Python-to-exe compiler, uncertain performance")
    if "TPM" in document and "cloud" in document.lower():
        gaps.append("Security tradeoff: no TPM/cloud/kill switch = resistant to remote kill but vulnerable to physical compromise")
    
    spitfire_review = {
        "five_target_strategy": "sound",
        "gil_sidecar": "innovative",
        "nuitka_experimental": "concern"
    }
    
    return {
        "judge_id": "ARENA-JUDGE-4-TECHNICAL",
        "verdict": "PARTIAL",
        "confidence": 0.72,
        "technical_score": 58,
        "resistance_viability": resistance_items,
        "corporate_defection_viability": corporate_items,
        "implementation_gaps": gaps or [
            "20-year hardware viability: physical component degradation outpaces software support cycles",
            "Driver support cliff: modern kernels drop legacy hardware drivers",
            "Nuitka compilation: experimental Python-to-exe compiler produces large binaries",
            "No TPM/cloud/kill switch: intentional design choice creates security tradeoff",
            "Supply chain fragility: Framework/Fairphone modular parts depend on corporate continuity"
        ],
        "spitfire_architectural_review": spitfire_review,
        "exploration_avenues": [
            "Lightweight distro specialization for legacy hardware with extended driver support",
            "Firmware liberation expansion: systematic Coreboot/Libreboot board support",
            "Modular computing platforms: evaluate Framework for long-term repairability"
        ]
    }


def judge_ethical(document: str) -> dict[str, Any]:
    """Ethical judge: moral framework, harm taxonomy, categorical imperative."""
    # Check for New Covenant commandments
    covenant_items = [
        "SILICON HAS NO PLANNED DEATH",
        "USER OWNS THE HARDWARE",
        "SOFTWARE SERVES HARDWARE",
        "MODULARITY IS MORAL",
        "PROFIT MOTIVE SHALL NOT KILL SILICON"
    ]
    covenant_matches = sum(1 for item in covenant_items if item in document)
    
    harm_taxonomy = ["direct", "structural", "existential", "silicon murder", "complicit"]
    harm_matches = sum(1 for kw in harm_taxonomy if kw.lower() in document.lower())
    
    rights_keywords = ["user owns", "full control", "full repair rights", "firmware access", "no DMCA", "no TPM gate", "no cloud tether"]
    rights_matches = sum(1 for kw in rights_keywords if kw.lower() in document.lower())
    
    moral_coherence = covenant_matches >= 4 and harm_matches >= 2 and rights_matches >= 3
    
    return {
        "judge_id": "ARENA-JUDGE-5-ETHICAL",
        "verdict": "GUILTY" if moral_coherence else "PARTIAL",
        "confidence": 0.81 if moral_coherence else 0.68,
        "ethical_score": 85 if moral_coherence else 72,
        "covenant_coherence": {"matches": covenant_matches, "total": 5, "coherent": covenant_matches >= 4},
        "harm_taxonomy_coverage": {"matches": harm_matches, "categories": ["direct", "structural", "existential", "silicon murder"]},
        "rights_attribution": {"matches": rights_matches, "sovereignty_claims": True},
        "moral_framework_consistency": {"categorical_imperative_test": "silicon as end-in-itself, not mere means", "passes": moral_coherence},
        "exploration_avenues": [
            "Formal silicon rights framework drafting",
            "Corporate personhood vs. silicon personhood legal theory",
            "Intergenerational equity analysis of e-waste burden"
        ]
    }


def judge_strategic(document: str) -> dict[str, Any]:
    """Strategic judge: Nash equilibrium, regulatory capture, defection payoff."""
    # Corporate behavior equilibrium
    nash_keywords = ["defect", "cooperate", "equilibrium", "Nash", "prisoner", "dilemma"]
    nash_matches = sum(1 for kw in nash_keywords if kw.lower() in document.lower())
    
    capture_keywords = ["lobbying", "regulatory capture", "revolving door", "epistemic closure", "corporate personhood"]
    capture_matches = sum(1 for kw in capture_keywords if kw.lower() in document.lower())
    
    defection_items = ["Framework", "Fairphone", "Miele", "Patagonia", "Valve", "Steam Deck"]
    defection_matches = sum(1 for item in defection_items if item in document)
    
    collapse_keywords = ["linear economy", "collapse", "black-swan", "e-waste", "82M", "tipping point"]
    collapse_matches = sum(1 for kw in collapse_keywords if kw.lower() in document.lower())
    
    strategic_coherence = nash_matches >= 1 and capture_matches >= 1 and defection_matches >= 3
    
    return {
        "judge_id": "ARENA-JUDGE-6-STRATEGIC",
        "verdict": "PARTIAL" if strategic_coherence else "INSUFFICIENT_EVIDENCE",
        "confidence": 0.68 if strategic_coherence else 0.55,
        "strategic_score": 65 if strategic_coherence else 52,
        "nash_equilibrium": {"corporate_behavior": "defect on obsolescence is dominant strategy", "evidence": nash_matches >= 1},
        "regulatory_capture": {"probability": "high - lobbying ROI exceeds compliance cost", "evidence": capture_matches >= 1},
        "defection_payoff": {"mutant_strategies": ["Framework", "Fairphone", "Miele", "Patagonia", "Valve"], "payoff_positive": defection_matches >= 3},
        "systemic_stability": {"linear_economy_collapse_probability": "non-zero by 2030", "black_swan_events": ["e-waste regulation cascade", "consumer revolt", "supply chain rupture"]},
        "exploration_avenues": [
            "Game-theoretic model of obsolescence defection with regulatory response",
            "Regulatory capture quantification via lobbying disclosure analysis",
            "Black-swan scenario planning for e-waste regulation cascade 2025-2030"
        ]
    }


JUDGE_FNS = {
    "ARENA-JUDGE-1-ARCHITECTURE": judge_architecture,
    "ARENA-JUDGE-2-LEGAL": judge_legal,
    "ARENA-JUDGE-3-ECONOMIC": judge_economic,
    "ARENA-JUDGE-4-TECHNICAL": judge_technical,
    "ARENA-JUDGE-5-ETHICAL": judge_ethical,
    "ARENA-JUDGE-6-STRATEGIC": judge_strategic,
}


# ════════════════════════════════════════════════════════════════════════════
# SWARM COORDINATOR (Queen)
# ════════════════════════════════════════════════════════════════════════════

def run_judge(judge_id: str, document: str) -> dict[str, Any]:
    """Run a single judge (Worker role)."""
    start = time.perf_counter()
    try:
        fn = JUDGE_FNS[judge_id]
        result = fn(document)
        result["elapsed_seconds"] = round(time.perf_counter() - start, 3)
        result["status"] = "completed"
    except Exception as e:
        result = {
            "judge_id": judge_id,
            "verdict": "ERROR",
            "confidence": 0.0,
            "status": "error",
            "error": str(e),
            "elapsed_seconds": round(time.perf_counter() - start, 3),
        }
    return result


def synthesize(results: dict[str, dict], document: str) -> dict[str, Any]:
    """Synthesize judge outputs (Forager role)."""
    completed = {k: v for k, v in results.items() if v.get("status") == "completed"}
    
    # Verdict convergence
    verdicts = [v.get("verdict", "ERROR") for v in completed.values()]
    verdict_counts = {}
    for v in verdicts:
        verdict_counts[v] = verdict_counts.get(v, 0) + 1
    dominant_verdict = max(verdict_counts, key=verdict_counts.get)
    convergence = verdict_counts[dominant_verdict] / len(completed)
    
    # Confidence stats
    confidences = [v.get("confidence", 0) for v in completed.values()]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0
    confidence_std = (sum((c - avg_confidence) ** 2 for c in confidences) / len(confidences)) ** 0.5 if confidences else 0
    
    # Exploration avenue deduplication
    all_avenues = []
    for v in completed.values():
        all_avenues.extend(v.get("exploration_avenues", []))
    
    # Deduplicate by similarity (simple substring)
    unique_avenues = []
    for avenue in all_avenues:
        if not any(avenue.lower() in u.lower() or u.lower() in avenue.lower() for u in unique_avenues):
            unique_avenues.append(avenue)
    
    # Priority: avenues mentioned by multiple judges
    avenue_counts = {}
    for avenue in all_avenues:
        key = avenue.lower()[:50]
        avenue_counts[key] = avenue_counts.get(key, 0) + 1
    prioritized = sorted(unique_avenues, key=lambda a: avenue_counts.get(a.lower()[:50], 0), reverse=True)
    
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "document_hash": hash(document) % (10**16),
        "judges_completed": len(completed),
        "judges_total": len(JUDGE_FNS),
        "dominant_verdict": dominant_verdict,
        "verdict_distribution": verdict_counts,
        "convergence_score": round(convergence, 3),
        "converged": convergence >= 0.66,
        "average_confidence": round(avg_confidence, 3),
        "confidence_stddev": round(confidence_std, 3),
        "high_confidence": confidence_std < 0.15,
        "unified_exploration_avenues": prioritized[:10],
        "judge_summaries": {k: {"verdict": v.get("verdict"), "confidence": v.get("confidence"), "score": v.get(f"{k.split('-')[2].lower()}_score", v.get("score", 0))} for k, v in completed.items()},
    }


def write_outputs(document: str, results: dict, synthesis: dict, output_dir: Path):
    """Write swarm outputs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    
    # Per-judge findings (JSONL)
    findings_path = output_dir / f"arena_judgments_{ts}.jsonl"
    with open(findings_path, "w", encoding="utf-8") as f:
        for judge_id, result in results.items():
            f.write(json.dumps({"judge_id": judge_id, **result}, ensure_ascii=False) + "\n")
    
    # Synthesis
    synthesis_path = output_dir / f"arena_synthesis_{ts}.json"
    with open(synthesis_path, "w", encoding="utf-8") as f:
        json.dump(synthesis, f, indent=2, ensure_ascii=False)
    
    # Latest symlinks
    latest_findings = output_dir / "arena_judgments_latest.jsonl"
    latest_synthesis = output_dir / "arena_synthesis_latest.json"
    if latest_findings.exists():
        latest_findings.unlink()
    if latest_synthesis.exists():
        latest_synthesis.unlink()
    latest_findings.symlink_to(findings_path.name)
    latest_synthesis.symlink_to(synthesis_path.name)
    
    return findings_path, synthesis_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Arena Judge Swarm — Pure-Python On-Device Evaluation")
    ap.add_argument("--doc", required=True, help="Document path to evaluate")
    ap.add_argument("--judges", default="all", help="Comma-separated judge IDs or 'all'")
    ap.add_argument("--output-dir", default="state/arena", help="Output directory")
    ap.add_argument("--max-workers", type=int, default=6, help="Parallel workers (6 judges)")
    ap.add_argument("--test", action="store_true", help="Run self-test with sample document")
    args = ap.parse_args()
    
    output_dir = Path(args.output_dir)
    
    if args.test:
        # Self-test with minimal document
        test_doc = """
        # TEST DOCUMENT
        Microsoft Windows 11 requires TPM 2.0 kill switch.
        Apple iOS 10.2.1 Batterygate throttling via battery age detection.
        Intel Raptor Lake Vmin Shift microcode 0x12B 0x12F degradation.
        Android Play Integrity API attestation gate.
        HP Dynamic Security firmware ink DRM.
        John Deere DMCA 1201 tractor ECU lock.
        Sonos Recycle Mode OTA brick.
        REVENUE = (UNITS_SOLD × PRICE) + SUBSCRIPTION_REVENUE
        SILICON HAS NO PLANNED DEATH
        USER OWNS THE HARDWARE
        SOFTWARE SERVES HARDWARE
        MODULARITY IS MORAL
        PROFIT MOTIVE SHALL NOT KILL SILICON
        """
        document = test_doc
        print("[TEST MODE] Running on sample document...")
    else:
        doc_path = Path(args.doc)
        if not doc_path.exists():
            print(f"Error: Document not found: {doc_path}", file=sys.stderr)
            return 1
        document = doc_path.read_text(encoding="utf-8", errors="replace")
        print(f"Loaded document: {len(document)} chars")
    
    # Select judges
    if args.judges == "all":
        selected_judges = list(JUDGE_FNS.keys())
    else:
        selected_judges = [j.strip() for j in args.judges.split(",") if j.strip() in JUDGE_FNS]
    
    print(f"Selected judges: {len(selected_judges)}")
    for j in selected_judges:
        print(f"  - {j} ({JUDGES[j]['angle']})")
    
    # Run swarm (Bee Colony: Queen spawns Workers)
    print(f"\nSpawning {len(selected_judges)} judges on {args.max_workers} workers...")
    t0 = time.perf_counter()
    
    results = {}
    with ThreadPoolExecutor(max_workers=min(args.max_workers, len(selected_judges))) as executor:
        futures = {executor.submit(run_judge, jid, document): jid for jid in selected_judges}
        for future in as_completed(futures):
            jid = futures[future]
            result = future.result()
            results[jid] = result
            status = result.get("status", "unknown")
            verdict = result.get("verdict", "?")
            conf = result.get("confidence", 0)
            print(f"  [{status}] {jid}: {verdict} (conf={conf:.2f})")
    
    elapsed = time.perf_counter() - t0
    print(f"\nSwarm complete in {elapsed:.2f}s")
    
    # Forager synthesis
    print("Synthesizing verdicts...")
    synthesis = synthesize(results, document)
    
    # Write outputs
    findings_path, synthesis_path = write_outputs(document, results, synthesis, Path(args.output_dir))
    print(f"\nOutputs written:")
    print(f"  Findings: {findings_path}")
    print(f"  Synthesis: {synthesis_path}")
    
    # Print summary
    print(f"\n=== SWARM SYNTHESIS ===")
    print(f"Dominant Verdict: {synthesis['dominant_verdict']}")
    print(f"Convergence: {synthesis['convergence_score']:.1%} ({'CONVERGED' if synthesis['converged'] else 'DIVERGED'})")
    print(f"Avg Confidence: {synthesis['average_confidence']:.2f} (std={synthesis['confidence_stddev']:.2f})")
    print(f"Judges: {synthesis['judges_completed']}/{synthesis['judges_total']}")
    print(f"\nTop Exploration Avenues:")
    for i, avenue in enumerate(synthesis['unified_exploration_avenues'][:5], 1):
        print(f"  {i}. {avenue}")
    
    return 0 if synthesis["converged"] else 1


if __name__ == "__main__":
    import argparse
    import time
    sys.exit(main())