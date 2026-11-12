#!/usr/bin/env python3
"""L3 absent-row pins: six review rows with no host mechanism (Oct-19).

Each test asserts the mechanism symbols are absent from the named host
module AND that the adapter still reports module-granularity absence. If a
mechanism ever lands, the pin fails loudly and the row must be re-graded
to PARTIAL/wired — never silently doubled up.

- EP141: eval_utils ships calibration/forecasting utils (walk_forward,
  ece_score, brier_score); no 20-task research-lifecycle harness.
- EP153: escalation_sft ships a ledger consumer only; no rollout-agreement
  labeling of synthetic trajectories.
- EP278: redteam ships HackCritic/taxed_reward plus a rubric QUESTION about
  untrusted text (line 111) — a question string, not an injection harness;
  no error-path injection, no obey-assertion.
- EP404: worm_governance ships naming/lifecycle validators; no deception
  audit, no reasoning-vs-cheap-talk comparison.
- EP405: latent_align ships alignment primitives (norm_calibrate,
  orthogonal_align, prefix_inject); no message-replacement causal audit.
- EP402: meta_evolution ships candidate/lifecycle selection; no
  bandit/REINFORCE harness control, no six-term reward.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _absent(modname, keys):
    mod = __import__(modname, fromlist=["*"])
    return [n for n in dir(mod)
            if any(k in n.lower() for k in keys)]


def _adapter_empty(epfn):
    from CORE.technique_wiring import _l3_verify as l3
    out = getattr(l3, epfn)()
    assert out["host_imported"] is True
    assert out["host_symbol_found"] is False
    assert out["import_error"] == ""


def test_ep141_no_harness_in_eval_utils():
    assert _absent("CORE.eval_utils", ("harness", "airs", "benchmark",
                                       "lifecycle", "frontier")) == []
    _adapter_empty("_ep141")


def test_ep153_no_agreement_labeling_in_escalation_sft():
    assert _absent("CORE.escalation_sft", ("agreement", "rollout",
                                           "soft_verif", "synthetic")) == []
    _adapter_empty("_ep153")


def test_ep278_no_error_path_injection_in_redteam():
    assert _absent("CORE.redteam", ("inject", "error_path",
                                    "error_text", "obey")) == []
    _adapter_empty("_ep278")


def test_ep404_no_deception_audit_in_worm_governance():
    assert _absent("CORE.worm_governance", ("decept", "werewolf", "cheap",
                                            "reasoning_gap")) == []
    _adapter_empty("_ep404")


def test_ep405_no_causal_audit_in_latent_align():
    assert _absent("CORE.latent_align", ("replac", "counterfactual",
                                         "interven", "causal",
                                         "decompos")) == []
    _adapter_empty("_ep405")


def test_ep402_no_harness_control_in_meta_evolution():
    assert _absent("CORE.meta_evolution", ("bandit", "reinforce",
                                           "harness_action", "six")) == []
    _adapter_empty("_ep402")
