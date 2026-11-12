import json
from collections import Counter
d = json.load(open(r'C:\Azazael\azazael\scripts\bcc_gap_index.json'))
print('Total gaps:', len(d))
disp = Counter(v.get('disposition', '') for v in d.values())
print('Dispositions:', dict(disp))
print()
for k, v in d.items():
    if v.get('disposition') == 'OPEN':
        print(f'  {k}: {v.get("basis", "?")[:100]}')