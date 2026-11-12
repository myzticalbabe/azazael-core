# Build evolution subsystem call graph (verified from source, 2026-09-11)
# Run from azazael/ root. Emits EVOLUTION_CALLGRAPH.mmd + EVOLUTION_CALLGRAPH.svg

edges = [
    # driver: evolution_cycle.py
    ("main(evolution_cycle)", "last_best_genes()"),
    ("main(evolution_cycle)", "_tpe_seeds()"),
    ("main(evolution_cycle)", "_load_qd_archive()"),
    ("main(evolution_cycle)", "_qd_suggest()"),
    ("main(evolution_cycle)", "_select_hof()"),
    ("main(evolution_cycle)", "EvolutionEngine.__init__"),
    ("main(evolution_cycle)", "EvolutionEngine.initialize_population"),
    ("main(evolution_cycle)", "EvolutionEngine.evaluate_population"),
    ("main(evolution_cycle)", "EvolutionEngine.get_best_genome"),
    ("main(evolution_cycle)", "EvolutionEngine.has_converged"),
    ("main(evolution_cycle)", "EvolutionEngine.evolve"),
    ("main(evolution_cycle)", "_run_cma_arm()"),
    ("main(evolution_cycle)", "_pareto_refine()"),
    ("main(evolution_cycle)", "_pareto_gate()"),
    ("main(evolution_cycle)", "_sprt_decide()"),
    ("main(evolution_cycle)", "_holdout_gate()"),
    ("main(evolution_cycle)", "_check_canary()"),
    ("main(evolution_cycle)", "fitness_parts()"),
    ("main(evolution_cycle)", "fitness_genes()"),
    ("main(evolution_cycle)", "production_genes()"),
    ("main(evolution_cycle)", "DirectMemory.record(evolution_result)"),
    ("main(evolution_cycle)", "DirectMemory.record(evolution_promotion)"),
    # v2.0 secondaries
    ("_run_cma_arm()", "SepCMAES.optimize"),
    ("_tpe_seeds()", "TPESurrogate.suggest"),
    ("_pareto_refine()", "EvolutionEngine.optimize_multi_objective"),
    ("_holdout_gate()", "DataPipelineWorm.extract_from_memory"),
    ("_check_canary()", "state/evolved_rl_params.json.prev"),
    ("main(evolution_cycle)", "_save_qd_archive()"),
    # fitness chain
    ("fitness(genome)", "fitness_genes()"),
    ("fitness_genes()", "genome_seed()"),
    ("fitness_genes()", "_eval_salt()"),
    ("fitness_parts()", "_eval_salt()"),
    ("_eval_salt()", "RLTrainLoop.train_from_memory(info pool)"),
    ("_eval_salt()", "RLTrainLoop.train_from_memory(alert pool)"),
    ("pool_fingerprint()", "DataPipelineWorm.extract_from_memory"),
    ("last_best_genes()", "store.query_memories(evolution_result)"),
    ("production_genes()", "state/evolved_rl_params.json"),
    ("production_genes()", "ScheduleConfig() defaults"),
    # EvolutionEngine internals
    ("EvolutionEngine.evaluate_population", "EvolutionEngine._govern_evolution_gate"),
    ("EvolutionEngine.evaluate_population", "fitness(genome)"),
    ("EvolutionEngine.evolve", "EvolutionEngine._adapt_mutation_rate"),
    ("EvolutionEngine.evolve", "EvolutionEngine._tournament_select"),
    ("EvolutionEngine.evolve", "EvolutionEngine._crossover"),
    ("EvolutionEngine.evolve", "EvolutionEngine._mutate"),
    ("EvolutionEngine.evolve", "EvolutionEngine._persist_generation"),
    ("EvolutionEngine._adapt_mutation_rate", "EvolutionEngine._compute_population_diversity"),
    ("EvolutionEngine.save_checkpoint", "store.record_event(checkpoint_saved)"),
    ("EvolutionEngine.load_checkpoint", "store.search_memories(checkpoint_saved)"),
    # RLTrainLoop
    ("RLTrainLoop.train_from_memory(info pool)", "RLTrainLoop._get_pipeline"),
    ("RLTrainLoop.train_from_memory(alert pool)", "RLTrainLoop._get_pipeline"),
    ("RLTrainLoop._get_pipeline", "DataPipelineWorm()"),
    ("RLTrainLoop.train_from_memory(info pool)", "RLTrainLoop._get_state_key"),
    ("RLTrainLoop.train_from_memory(info pool)", "RLTrainLoop._compute_reward"),
    ("RLTrainLoop.train_from_memory(info pool)", "store.record_event(training_complete)"),
    ("RLTrainLoop.train_from_memory(info pool)", "RLTrainLoop.register_to_versioner"),
    ("RLTrainLoop.register_to_versioner", "ModelVersioner.register_model"),
    ("RLTrainLoop.register_to_versioner", "RLTrainLoop._save_q_table_to_store"),
    # scheduling (production consumption path)
    ("ScheduledTrainingWorm.execute", "ScheduleConfig.apply_evolved_overrides"),
    ("ScheduleConfig.apply_evolved_overrides", "state/evolved_rl_params.json"),
    ("ScheduledTrainingWorm.execute", "RLTrainLoop.train_from_memory"),
    ("ScheduledTrainingWorm.execute", "DataPipelineWorm.extract_from_memory"),
    ("ScheduledTrainingWorm.execute", "ModelVersioner.register_model"),
    # data source
    ("DataPipelineWorm.extract_from_memory", "store.query_memories"),
    # multi-objective (present, unused by cycle)
    ("EvolutionEngine.optimize_multi_objective", "EvolutionEngine._non_dominated_sort"),
    ("EvolutionEngine.optimize_multi_objective", "EvolutionEngine._compute_crowding_distance"),
    ("GeneticOptimizer.optimize", "EvolutionEngine.evaluate_population"),
    ("GeneticOptimizer.optimize", "EvolutionEngine.evolve"),
]

nodes = {}
for a, b in edges:
    nodes.setdefault(a, {})
    nodes.setdefault(b, {})

FILE_OF = {
    "main(evolution_cycle)": "evolution_cycle.py", "last_best_genes()": "evolution_cycle.py",
    "fitness(genome)": "evolution_cycle.py", "fitness_genes()": "evolution_cycle.py",
    "fitness_parts()": "evolution_cycle.py", "genome_seed()": "evolution_cycle.py",
    "_eval_salt()": "evolution_cycle.py", "pool_fingerprint()": "evolution_cycle.py",
    "production_genes()": "evolution_cycle.py",
    "EvolutionEngine.__init__": "evolution.py", "EvolutionEngine.initialize_population": "evolution.py",
    "EvolutionEngine.evaluate_population": "evolution.py", "EvolutionEngine.get_best_genome": "evolution.py",
    "EvolutionEngine.has_converged": "evolution.py", "EvolutionEngine.evolve": "evolution.py",
    "EvolutionEngine._govern_evolution_gate": "evolution.py", "EvolutionEngine._adapt_mutation_rate": "evolution.py",
    "EvolutionEngine._compute_population_diversity": "evolution.py", "EvolutionEngine._tournament_select": "evolution.py",
    "EvolutionEngine._crossover": "evolution.py", "EvolutionEngine._mutate": "evolution.py",
    "EvolutionEngine._persist_generation": "evolution.py", "EvolutionEngine.save_checkpoint": "evolution.py",
    "EvolutionEngine.load_checkpoint": "evolution.py", "EvolutionEngine.optimize_multi_objective": "evolution.py",
    "EvolutionEngine._non_dominated_sort": "evolution.py", "EvolutionEngine._compute_crowding_distance": "evolution.py",
    "GeneticOptimizer.optimize": "evolution.py",
    "RLTrainLoop.train_from_memory(info pool)": "learning_rl.py", "RLTrainLoop.train_from_memory(alert pool)": "learning_rl.py",
    "RLTrainLoop._get_pipeline": "learning_rl.py", "RLTrainLoop._get_state_key": "learning_rl.py",
    "RLTrainLoop._compute_reward": "learning_rl.py", "RLTrainLoop.register_to_versioner": "learning_rl.py",
    "RLTrainLoop._save_q_table_to_store": "learning_rl.py",
    "ScheduledTrainingWorm.execute": "scheduling.py", "ScheduleConfig.apply_evolved_overrides": "scheduling.py",
    "ScheduleConfig() defaults": "scheduling.py", "RLTrainLoop.train_from_memory": "learning_rl.py",
    "store.record_event": "local_memory.py",
    "DataPipelineWorm()": "learning_pipeline.py", "DataPipelineWorm.extract_from_memory": "learning_pipeline.py",
    "ModelVersioner.register_model": "learning_versioner.py",
    "DirectMemory.record(evolution_result)": "direct_memory.py", "DirectMemory.record(evolution_promotion)": "direct_memory.py",
    "store.query_memories": "local_memory.py", "store.record_event": "local_memory.py",
    "store.search_memories": "local_memory.py",
    "store.record_event(training_complete)": "local_memory.py", "store.record_event(checkpoint_saved)": "local_memory.py",
    "store.query_memories(evolution_result)": "local_memory.py",
    "state/evolved_rl_params.json": "state/evolved_rl_params.json",
    "state/evolved_rl_params.json.prev": "state/evolved_rl_params.json",
    "SepCMAES.optimize": "evolution.py", "TPESurrogate.suggest": "evolution.py",
    "_run_cma_arm()": "evolution_cycle.py", "_tpe_seeds()": "evolution_cycle.py",
    "_load_qd_archive()": "evolution_cycle.py", "_qd_suggest()": "evolution_cycle.py",
    "_select_hof()": "evolution_cycle.py", "_save_qd_archive()": "evolution_cycle.py",
    "_pareto_refine()": "evolution_cycle.py", "_pareto_gate()": "evolution_cycle.py",
    "_sprt_decide()": "evolution_cycle.py", "_holdout_gate()": "evolution_cycle.py",
    "_check_canary()": "evolution_cycle.py",
}
for n in nodes:
    nodes[n]["file"] = FILE_OF.get(n, "local_memory.py")


def mangle(s):
    return s.replace("(", "_").replace(")", "_").replace("/", "_").replace(" ", "_").replace("-", "_")


# --- emit Mermaid ---
lines = ["flowchart TD"]
for a, b in edges:
    lines.append("    %s --> %s" % (mangle(a), mangle(b)))
open("EVOLUTION_CALLGRAPH.mmd", "w", encoding="utf-8").write("\n".join(lines) + "\n")

# --- emit SVG (layered layout: 4 columns by role) ---
COLORS = {
    "evolution_cycle.py": "#dbeafe", "evolution.py": "#dcfce7", "learning_rl.py": "#fef9c3",
    "scheduling.py": "#fce7f3", "learning_pipeline.py": "#e0e7ff", "learning_versioner.py": "#ffedd5",
    "local_memory.py": "#e5e7eb", "direct_memory.py": "#e5e7eb", "state/evolved_rl_params.json": "#fee2e2",
}
COL = {
    "evolution_cycle.py": 0, "evolution.py": 1, "learning_rl.py": 2,
    "scheduling.py": 2, "learning_pipeline.py": 2, "learning_versioner.py": 2,
    "local_memory.py": 3, "direct_memory.py": 3, "state/evolved_rl_params.json": 3,
}
COL_X = {0: 30, 1: 330, 2: 630, 3: 930}
W, H = 1240, 300
import collections
bycol = collections.defaultdict(list)
for n in nodes:
    bycol[COL[nodes[n]["file"]]].append(n)
maxrows = max(len(v) for v in bycol.values())
for c in bycol:
    for i, n in enumerate(sorted(bycol[c])):
        nodes[n]["row"] = i
H = max(60 * (maxrows + 1), 300)

svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" font-family="Consolas,monospace" font-size="11">' % (W, H)]
svg.append('<rect width="%d" height="%d" fill="#f8fafc"/>' % (W, H))
svg.append('<text x="20" y="20" font-size="14" font-weight="bold" fill="#0f172a">AZAZAEL evolution subsystem - call graph (verified from source, 2026-09-11)</text>')
svg.append('<text x="20" y="36" font-size="10" fill="#64748b">columns: driver script | GA engine | RL/eval + consumption | storage/config</text>')
svg.append('<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#94a3b8"/></marker></defs>')
for n, meta in nodes.items():
    x, y = COL_X[COL[meta["file"]]], 60 + meta["row"] * 52
    w = 270
    svg.append('<rect x="%d" y="%d" width="%d" height="40" rx="6" fill="%s" stroke="#94a3b8"/>' % (x, y, w, COLORS[meta["file"]]))
    label = n if len(n) <= 42 else n[:39] + "..."
    svg.append('<text x="%d" y="%d" fill="#0f172a">%s</text>' % (x + 8, y + 18, label))
    svg.append('<text x="%d" y="%d" fill="#64748b" font-size="9">%s</text>' % (x + 8, y + 32, meta["file"]))
for a, b in edges:
    if a in nodes and b in nodes:
        x1 = COL_X[COL[nodes[a]["file"]]] + 270
        y1 = 60 + nodes[a]["row"] * 52 + 20
        x2 = COL_X[COL[nodes[b]["file"]]]
        y2 = 60 + nodes[b]["row"] * 52 + 20
        mid = (x1 + x2) / 2
        svg.append('<path d="M %d %d C %d %d, %d %d, %d %d" stroke="#94a3b8" stroke-width="1" fill="none" marker-end="url(#arrow)"/>' % (x1, y1, mid, y1, mid, y2, x2, y2))
svg.append('</svg>')
open("EVOLUTION_CALLGRAPH.svg", "w", encoding="utf-8").write("\n".join(svg))

print("wrote EVOLUTION_CALLGRAPH.mmd (%d edges) and EVOLUTION_CALLGRAPH.svg (%d nodes)" % (len(edges), len(nodes)))