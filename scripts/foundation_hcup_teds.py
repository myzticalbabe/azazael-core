"""
foundation_hcup_teds.py — Foundation Directory, HCUP, TEDS data sources.
Resolves gaps: funder mapping, treatment data, program services
"""

import json
import time
from pathlib import Path

OUTPUT_DIR = Path(r"C:\Azazael\azazael\research\bcc_analysis\foundation_hcup_teds")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def foundation_directory():
    """Candid/Foundation Directory Online - funder mapping."""
    return {
        'name': 'Candid Foundation Directory Online',
        'url': 'https://fconline.foundationcenter.org/',
        'access': 'Subscription required ($1,500+/year). Some free via library.',
        'covers': '140,000+ foundations, corporate giving programs, grantmaking public charities',
        'bcc_relevance': {
            'COCA_funders': 'Search grants to EIN 231858138',
            'BCC_funders': 'Search grants to EIN 232043478',
            'TASC_funders': 'Search grants to EIN 232409777',
            'UWBERKS': 'United Way of Berks County grantmaking',
            '990_PF': 'Private foundation 990-PF filings showing BCC grants'
        },
        'api': 'Candid API available for subscribers'
    }


def hcup_data():
    """HCUP (Healthcare Cost and Utilization Project) - hospital data."""
    return {
        'name': 'HCUP - AHRQ',
        'url': 'https://hcup-us.ahrq.gov/',
        'databases': {
            'SID': 'State Inpatient Databases (PA available)',
            'SEDD': 'State Emergency Department Databases',
            'NIS': 'National Inpatient Sample',
            'NEDS': 'National Emergency Department Sample'
        },
        'bcc_relevance': 'Hospital utilization for SUD treatment, not community-based BCC directly',
        'access': 'Requires Data Use Agreement, some free (HCUPnet online query)',
        'hcupnet': 'https://hcupnet.ahrq.gov/ - free online query tool'
    }


def teds_data():
    """TEDS - Treatment Episode Data Set."""
    return {
        'name': 'SAMHSA TEDS',
        'url': 'https://www.samhsa.gov/data/data-we-collect/teds-treatment-episode-data-set',
        'description': 'Client-level admission/discharge data for SUD treatment',
        'variables': [
            'Age, gender, race/ethnicity',
            'Primary/secondary/tertiary substances',
            'Treatment setting (outpatient, residential, etc.)',
            'Length of stay',
            'Discharge reason',
            'Referral source',
            'Employment, education, living arrangement'
        ],
        'bcc_relevance': 'Aggregate PA/ Berks County treatment patterns, NOT facility-specific',
        'access': 'Public use files free; restricted use files require agreement',
        'hcupnet_equivalent': 'SAMHSA PDAS (Public-use Data Analysis System)'
    }


def main():
    output = {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'foundation_directory': foundation_directory(),
        'hcup': hcup_data(),
        'teds': teds_data(),
        'gaps_targeted': ['funder_mapping', 'treatment_patterns', 'program_services']
    }
    
    OUTPUT_DIR = Path(r"C:\Azazael\azazael\research\bcc_analysis\foundation_hcup_teds")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / 'foundation_hcup_teds.json').write_text(json.dumps(output, indent=2))
    return output


if __name__ == '__main__':
    main()