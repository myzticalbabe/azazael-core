# coding: utf-8
"""Probe: is the Phase E bool gene actually polymorphic in the population?

Why this exists: the first four live Morph cycles all ended with the extra gene
at False. Two very different explanations fit that observation —

  (a) selection: True genuinely scores worse, so False wins;
  (b) the gene is being FLATTENED by the engine (sampled as float, or dropped
      by crossover/mutation), so True is never really searched.

(b) would mean Morph runs, logs, and searches a dimension that does not exist.
Only a direct count of the population separates the two.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))

from CORE.WORMS.expansion.evolution import EvolutionEngine  # noqa: E402
from CORE.WORMS.expansion.proteus_hooks import morph_gene_specs  # noqa: E402

BASE = {'learning_rate': (0.01, 0.9), 'epsilon': (0.0, 0.5),
        'discount': (0.5, 0.999), 'epsilon_decay': (0.0, 0.3)}


def census(evo, name):
    vals = [g.genes.get(name) for g in evo._population]
    types = sorted({type(v).__name__ for v in vals})
    n_true = sum(1 for v in vals if v is True)
    n_false = sum(1 for v in vals if v is False)
    n_other = len(vals) - n_true - n_false
    return {'n': len(vals), 'true': n_true, 'false': n_false,
            'other': n_other, 'types': types}


def main():
    for cycle in range(2):
        specs, gene = morph_gene_specs(cycle, BASE)
        name = gene['name']
        evo = EvolutionEngine(population_size=24)
        evo.initialize_population(dict(BASE), gene_specs=specs)
        print('cycle %d  gene=%s  spec=%s' % (cycle, name, specs[name]))
        print('  gen 0        ', census(evo, name))
        # Deterministic fitness: rewards whichever value the gene holds, so the
        # engine has a gradient to follow. If the gene is real, the population
        # should move toward True and STAY bool-typed.
        for gen in range(6):
            evo.evaluate_population(lambda g: 1.0 if g.genes.get(name) else 0.0)
            evo.evolve()
            if gen in (0, 2, 5):
                print('  gen %-8d ' % (gen + 1), census(evo, name))
        final = census(evo, name)
        print('  final        ', final)
        ok = final['types'] == ['bool'] and final['other'] == 0
        print('  bool-typed   : %s' % ('YES' if ok else 'NO <-- FLATTENED'))
        # Selection pressure check: with a True-favouring fitness the winner
        # must be True.
        best = evo.get_best_genome()
        print('  winner=%r  -> gene is %s' % (
            best.genes.get(name) if best else None,
            'SEARCHABLE' if (best and best.genes.get(name) is True) else 'INERT'))
        print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
