"""PSYCHE — person psychology as an editable logical framework.

Person artifact composition (lossless JSON, correctable by the person):

  theta  traits.py        OCEAN + Honesty-Humility vector (Phase 1)
  M      PAMS episodic store (external, referenced by pointer)
  A      affect.py        PAD + OCC appraisal (Phase 1)
  pi     distill.py       distilled policy on frozen theta (Phase 3)
  G      network.py       construct graph: ridge partial correlations, one
                          GNN message-passing layer (Phase 4)
  K      intuition.py     compiled inference over G: prototypes with support
                          and a resolution ledger, so it can be graded (Phase 5)
  L      rt_agent.py      the realtime loop: risk triage first, then a stance
                          chosen by utility inside a receptivity gate (Phase 6)

Dependency order is the list order: G reads theta and A, K compiles G, L drives
K and A. Nothing here takes a demographic input, and no module names a disorder —
both are enforced by introspection in the tests, not by convention.
"""
