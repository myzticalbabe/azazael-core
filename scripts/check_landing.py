import json
d = json.load(open(r'C:\Azazael\azazael\scripts\bcc_gap_index.json'))
updated = [k for k,v in d.items() if 'new_evidence' in v]
print(f'Gaps with new evidence: {len(updated)}')
for k in updated[:5]:
    print(f'  Gap {k}: {d[k]["new_evidence"][:80]}')