import json
d = json.load(open(r'C:\Azazael\azazael\research\master_pattern_catalog.json'))
for k, v in list(d['patterns'].items())[:10]:
    print(f'{k}: priority={v["priority"]}, conf={v["confidence_score"]}, ev_str={v["evidence_strength"]}, corr={v["corroboration_count"]}, ev_comp={v["evidence_completeness"]}, count={v["count"]}')