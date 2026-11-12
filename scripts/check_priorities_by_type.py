import json
d = json.load(open(r'C:\Azazael\azazael\research\master_pattern_catalog.json'))
# Check a few patterns from each type
for ptype in ['ast_pattern', 'import_pattern', 'graph_motif', 'bcc_network_motif', 'training_pattern', 'memory_event']:
    items = [v for v in d['patterns'].values() if v['pattern_type'] == ptype]
    if items:
        print(f'\n{ptype}:')
        for v in items[:3]:
            print(f'  {v["pattern_id"]}: priority={v["priority"]}, conf={v["confidence_score"]}, ev_str={v["evidence_strength"]:.2f}, corr={v["corroboration_count"]}, ev_comp={v["evidence_completeness"]}, count={v["count"]}')