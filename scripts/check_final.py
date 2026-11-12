import json
d = json.load(open(r'C:\Azazael\azazael\scripts\bcc_gap_index.json'))
from collections import Counter
disp = Counter(v.get('disposition', '') for v in d.values())
print('Final dispositions:', dict(disp))
updated = [k for k,v in d.items() if 'new_evidence' in v]
print(f'Total updated: {len(updated)}')
for k in sorted(updated, key=int):
    v = d[k]
    print(f'  Gap {k}: {v.get("disposition", "?")} - {v.get("new_evidence", "no update")[:60]}')