import json
d = json.load(open(r'C:\Azazael\azazael\research\master_pattern_catalog.json'))
for k, v in list(d['patterns'].items())[:5]:
    print(f'{k}:')
    print(f'  pattern_type: {v["pattern_type"]}')
    print(f'  source_category: {v["source_category"]}')
    print(f'  confidence_score: {v["confidence_score"]}')
    print(f'  priority: {v["priority"]}')
    print(f'  structural_data: {v.get("structural_data", {})}')
    print()