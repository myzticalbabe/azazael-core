import json
import sys
sys.path.insert(0, 'scripts')
from d_grade_metadata import filter_for_press_pull
from lead_metadata import filter_for_verification
from gap_metadata import filter_for_action

with open('bcc_gap_index.enriched.json') as f:
    gaps = json.load(f)
with open('scripts/bcc_lead_inference_evidence.enriched.json') as f:
    leads = json.load(f)
with open('bcc_person_edges.enriched.json') as f:
    edges = json.load(f)

from gap_metadata import filter_for_action
from lead_metadata import filter_for_verification
from d_grade_metadata import filter_for_press_pull

ready_gaps = filter_for_action(gaps)
ready_leads = filter_for_verification(leads)
ready_edges = filter_for_press_pull(edges)

print('=== FINAL FILTER RESULTS ===')
print('Gaps ready for action: {}/{}'.format(len(ready_gaps), len(gaps)))
for g in ready_gaps:
    print('  #{}: {} (urgency={})'.format(g['gap_id'], g['title'][:60], g['urgency']))

print('\nLeads ready for verification: {}/{}'.format(len(ready_leads), len(leads)))
for l in ready_leads:
    print('  {}: {} (priority={})'.format(l['id'], l['title'][:60], l['triage_priority']))

print('\nEdges ready for press pull: {}/{}'.format(len(ready_edges), len(edges)))
for e in ready_edges:
    print('  {} -> {} (priority={})'.format(e['source'], e['target'], e['priority']))