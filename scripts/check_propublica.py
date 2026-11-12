import json
d = json.load(open(r'C:\Azazael\azazael\research\bcc_analysis\propublica_refresh\propublica_refresh.json'))
for name, data in d['organizations'].items():
    fin = data.get('financials', {})
    print(f'{name}: revenue={fin.get("revenue")}, expenses={fin.get("expenses")}, assets={fin.get("assets")}, officers={len(fin.get("officers", []))}')