import json
d = json.load(open(r'C:\Azazael\azazael\research\master_pattern_catalog.json'))

# Check all priorities
from collections import Counter
pri_counter = Counter(v['priority'] for v in d['patterns'].values())
print('Priority distribution:', dict(pri_counter))

# Check training and memory patterns
for ptype in ['training_pattern', 'memory_event']:
    items = [v for v in d['patterns'].values() if v['pattern_type'] == ptype]
    if items:
        print(f'\n{ptype}:')
        for v in items[:3]:
            print(f'  {v["pattern_id"]}: priority={v["priority"]}, conf={v["confidence_score"]}, ev_str={v["evidence_strength"]:.2f}, corr={v["corroboration_count"]}, ev_comp={v["evidence_completeness"]}, count={v["count"]}')

# Check max priority
max_pri = max(v['priority'] for v in d['patterns'].values())
print(f'\nMax priority: {max_pri}')