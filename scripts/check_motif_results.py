import json
d = json.load(open(r'C:\Azazael\azazael\research\motif_census_by_relation.json'))
print('Relation groups analyzed:', len(d))
for k, v in d.items():
    print(f'  {k}: {v["full_edges"]} edges, {v["motifs"]["total"]} motifs')