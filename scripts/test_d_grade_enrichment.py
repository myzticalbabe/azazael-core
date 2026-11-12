import json
from scripts.d_grade_metadata import enrich_d_grade_edges, filter_for_press_pull, compute_priority

with open('sample_d_grade_edges.enriched.json') as f:
    edges = json.load(f)

for e in edges:
    eid = e['edge_id']
    if eid == 'splink_001':
        e.update({'source_type':'json','retrieval_method':'api_call','confidence_score':0.72,'universe':'splink','verifier':'splink','tier_evidence':['sha_verified','model_probability'],'status':'dossier'})
    elif eid == 'splink_002':
        e.update({'source_type':'json','retrieval_method':'api_call','confidence_score':0.68,'universe':'splink','verifier':'splink','tier_evidence':['sha_verified','model_probability'],'status':'dossier'})
    elif eid == 'sna_001':
        e.update({'source_type':'json','retrieval_method':'api_call','confidence_score':0.55,'universe':'sna','verifier':'named_entity','tier_evidence':['sna_results_v3','networkx_graph'],'status':'unsaved'})
    elif eid == 'sna_002':
        e.update({'source_type':'json','retrieval_method':'api_call','confidence_score':0.78,'universe':'sna','verifier':'named_entity','tier_evidence':['sna_results_v3','networkx_graph'],'status':'unsaved','notes':e['notes']+' - needs UJS/DOS adjudication'})
    elif eid == 'gap_001':
        e.update({'source_type':'json','retrieval_method':'api_call','confidence_score':0.85,'universe':'IRS','verifier':'manual','tier_evidence':['gap_index_v2','propublica_profile'],'status':'unsaved'})
    elif eid == 'gap_002':
        e.update({'source_type':'json','retrieval_method':'api_call','confidence_score':0.65,'universe':'local_html','verifier':'manual','tier_evidence':['gap_index_v2'],'status':'unsaved'})
    elif eid == 'lead_001':
        e.update({'source_type':'json','retrieval_method':'api_call','confidence_score':0.92,'universe':'sna','verifier':'named_entity','tier_evidence':['watchdog_hits','canonical_names'],'status':'unsaved'})

for e in edges:
    if e.get('grade') == 'D':
        e['priority'] = compute_priority(e)

with open('sample_d_grade_edges.enhanced.json', 'w') as f:
    json.dump(edges, f, indent=1, ensure_ascii=False)

ready = filter_for_press_pull(edges)
print('Edges ready for press pull:', len(ready))
for e in ready:
    print('  {}: priority={}, universe={}, confidence={}'.format(e['edge_id'], e['priority'], e['universe'], e['confidence_score']))

print()
print('All edges by priority:')
for e in edges:
    if e.get('grade') == 'D':
        print('  {}: priority={}, universe={}, source={}, confidence={}'.format(e['edge_id'], e['priority'], e['universe'], e['source_type'], e['confidence_score']))