import json
import sys
sys.path.insert(0, 'scripts')
from gap_metadata import filter_for_action, compute_urgency

with open('bcc_gap_index.enriched.json') as f:
    gaps = json.load(f)

ready = filter_for_action(gaps)
print('Gaps ready for records action: {}/{}'.format(len(ready), len(gaps)))
print()
for g in ready:
    print('  #{}: {}'.format(g['gap_id'], g['title'][:60]))
    print('    disposition={}, urgency={}, topic={}'.format(g['disposition'], g['urgency'], g['topic']))
    print('    records_action={}'.format(g.get('records_action', '<empty>')[:80]))
    print()

# Also show urgency distribution
print('Urgency distribution:')
urgencies = {}
for g in gaps:
    u = g.get('urgency', 0)
    urgencies[u] = urgencies.get(u, 0) + 1
for u in sorted(urgencies):
    print('  urgency {}: {}'.format(u, urgencies[u]))

# Disposition distribution
print()
print('Disposition distribution:')
disps = {}
for g in gaps:
    d = g.get('disposition', 'UNKNOWN')
    disps[d] = disps.get(d, 0) + 1
for d in sorted(disps):
    print('  {}: {}'.format(d, disps[d]))