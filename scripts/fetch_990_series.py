"""fetch_990_series.py — IRS 990 e-file financial series fetcher.

Resolved source-of-truth (2026-09-04):
  * The legacy AWS `irs-form-990` bucket is DEPRECATED (IRS discontinued
    updates 2021-12-31); its JSON indexes and `{OBJECT_ID}_public.xml` files
    now 404.
  * The IRS's official TEOS CSV indexes live at
    https://apps.irs.gov/pub/epostcard/990/xml/{YYYY}/index_{YYYY}.csv
    (columns: RETURN_ID, FILING_TYPE, EIN, TAX_PERIOD, SUB_DATE,
    TAXPAYER_NAME, RETURN_TYPE, DLN, OBJECT_ID). Those index CSVs map an EIN
    to the OBJECT_IDs whose XMLs are bundled in the monthly TEOS zips
    (e.g. 2022_TEOS_XML_01A.zip) — the XMLs are NOT individually addressable.
  * ProPublica Nonprofit Explorer maintains a live index of the same IRS
    e-file data with the core financial fields already extracted per filing
    (API v2: /nonprofits/api/v2/organizations/{ein}.json).
  * ProPublica's per-filing HTML views expose the two newest filings that
    API v2 has NOT yet extracted: the org page embeds a JSON-LD dataset for
    the latest fiscal year, and /nonprofits/full_text/{OBJECT_ID}/IRS990
    (gzip Content-Encoding — must be gunzipped) returns the full rendered
    return incl. Part VII officer rows and Part I totals. OBJECT_ID comes
    from the IRS TEOS CSV index (index_{YYYY}.csv, column OBJECT_ID).
  * The monthly TEOS zips (XML_BATCH_ID, e.g. 2025_TEOS_XML_05A.zip, ~500MB)
    use Deflate64 (zip method 9) which Python's zipfile cannot decompress;
    the zip filename ids differ from the index OBJECT_IDs. Skip them — the
    full_text route is the practical extraction path.

This fetcher therefore uses the ProPublica API v2 as the primary source and
appends the newest filings from the JSON-LD/full_text routes (see
--supplement flag). data_source + api_version recorded in the output.

Usage:
    python fetch_990_series.py 232043478 [--out bcc_990_series.json] [--supplement fy2024fy2025.json]

Output JSON:
    {"ein": ..., "fetched": ..., "data_source": ..., "api_version": ...,
     "filings": [ {tax_prd, tax_prd_yr, formtype, revenue, expenses,
                   assets_eoy, liab_eoy, officer_comp, net_assets, ...} ]}

--supplement: path to a JSON with additional filings ({tax_prd, revenue, ...})
merged into the output (the FY2024/2025 rows pulled from the JSON-LD +
full_text routes on 2026-09-04; see bcc_990_series.json).
"""
import io, json, os, sys, urllib.request

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

# Field map: ProPublica API v2 keys -> our canonical series keys
FIELD_MAP = {
    'tax_prd': 'tax_prd',
    'tax_prd_yr': 'tax_prd_yr',
    'formtype': 'formtype',
    'totrevenue': 'revenue',
    'totfuncexpns': 'expenses',
    'totassetsend': 'assets_eoy',
    'totliabend': 'liab_eoy',
    'totnetassetend': 'net_assets_eoy',
    'compnsatncurrofcr': 'officer_comp',
    'pct_compnsatncurrofcr': 'officer_comp_pct',
    'totcntrbgfts': 'contributions',
    'totprgmrevnue': 'program_revenue',
    'grsincfndrsng': 'fundraising_gross',
    'pdf_url': 'pdf_url',
}


def fetch_org(ein):
    url = 'https://projects.propublica.org/nonprofits/api/v2/organizations/%s.json' % ein
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    args = sys.argv[1:]
    ein = None
    out = 'bcc_990_series.json'
    supplement = None
    i = 0
    while i < len(args):
        if args[i] == '--out' and i + 1 < len(args):
            out = args[i + 1]; i += 2
        elif args[i] == '--supplement' and i + 1 < len(args):
            supplement = args[i + 1]; i += 2
        elif ein is None:
            ein = args[i]; i += 1
        else:
            i += 1
    if not ein:
        print('usage: python fetch_990_series.py <EIN> [--out file.json] [--supplement extra.json]')
        sys.exit(2)

    data = fetch_org(ein)
    org = data.get('organization', {})
    filings = []
    for f in data.get('filings_with_data', []):
        rec = {}
        for src_key, dst_key in FIELD_MAP.items():
            if src_key in f:
                rec[dst_key] = f[src_key]
        filings.append(rec)
    if supplement and os.path.exists(supplement):
        extra = json.load(open(supplement, encoding='utf-8'))
        if isinstance(extra, dict) and 'filings' in extra:
            extra = extra['filings']
        filings.extend(extra)
    filings.sort(key=lambda x: x.get('tax_prd') or 0)

    dataset = {
        'ein': ein,
        'name': org.get('name'),
        'fetched': '2026-09-04',
        'data_source': data.get('data_source'),
        'api_version': data.get('api_version'),
        'filings_without_data': len(data.get('filings_without_data', [])),
        'filings': filings,
        'note': ('Source: ProPublica Nonprofit Explorer API v2 (live index of IRS '
                 'e-file data). IRS TEOS CSV index fallback: '
                 'apps.irs.gov/pub/epostcard/990/xml/{YYYY}/index_{YYYY}.csv. '
                 'Officer-level Part VII detail requires the monthly TEOS zip.'),
    }
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(dataset, f, indent=1)
    print('org: %s (%s)' % (org.get('name'), ein))
    print('filings with data: %d | without data: %d' % (
        len(filings), len(data.get('filings_without_data', []))))
    for rec in filings:
        print('  %s rev=%s exp=%s assets=%s offcr_comp=%s' % (
            rec.get('tax_prd'), rec.get('revenue'), rec.get('expenses'),
            rec.get('assets_eoy'), rec.get('officer_comp')))
    print('wrote', out, '(%d filings)' % len(filings))


if __name__ == '__main__':
    main()