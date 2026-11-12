import json
d = json.load(open(r'C:\Azazael\azazael\research\bcc_analysis\dmarc_check\dmarc_check.json'))
for dom in d['domains']:
    dmarc = dom['dmarc']
    spf = dom['spf']
    dkim = dom['dkim']
    print(f'{dom["domain"]}: DMARC={dmarc["found"]} SPF={spf["found"]} DKIM={dkim["found"]}')