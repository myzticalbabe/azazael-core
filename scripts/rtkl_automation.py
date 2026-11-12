"""
rtkl_automation.py — Automate PA Right-to-Know Law requests for blocked gaps.
Resolves gaps: 18, 34, 38, 50, 51, 52, 53, 54, 57, 58, 60, 61, 62
"""

import json
import time
from pathlib import Path
from datetime import datetime

# RTKL request templates for each gap
RTKL_REQUESTS = {
    18: {
        'agency': 'Berks County District Attorney',
        'subject': 'RTKL Request - 15-14644 CCP Berks (Leinbach/MCO)',
        'records': [
            'Judgment amount and disposition for CP-06-CR-15-14644',
            'All filings, orders, and transcripts in CP-06-CR-15-14644',
            'Plea agreement or sentencing order if applicable'
        ],
        'citation': '65 P.S. § 67.101 et seq.'
    },
    34: {
        'agency': 'PA Courts / AOPC',
        'subject': 'RTKL Request - PACFile Access for BCC Dockets',
        'records': [
            'PACFile access policy for non-attorney researchers',
            'List of dockets involving Berks Counseling Center / Community Care / COCA / TASC',
            'Procedure for public access to civil dockets via PACFile'
        ],
        'citation': '65 P.S. § 67.101 et seq.; 204 Pa. Code § 213.81'
    },
    38: {
        'agency': 'Berks County Prothonotary',
        'subject': 'RTKL Request - UCS Docket Search for BCC Entities',
        'records': [
            'All civil dockets naming Berks Counseling Center, Community Care, COCA, or TASC as party',
            'Search results for entities in UCS docket system (CP-Civil)',
            'Count of dockets by entity and year (2015-present)'
        ],
        'citation': '65 P.S. § 67.101 et seq.'
    },
    50: {
        'agency': 'DDAP',
        'subject': 'RTKL Request - DDAP Event YQM512 (709.34(c)(2))',
        'records': [
            'Full DDAP inspection report for Event YQM512',
            'Corrective action plan and compliance status',
            'Any enforcement actions against BCC/Community Care'
        ],
        'citation': '65 P.S. § 67.101 et seq.; 28 Pa. Code § 709.34'
    },
    51: {
        'agency': 'DDAP',
        'subject': 'RTKL Request - 523 vs 350 Authorized Beds (709.14(b)(5))',
        'records': [
            'DDAP licensing records showing authorized bed count for BCC facilities',
            '90-day notice documentation for bed count changes',
            'Current licensed capacity vs operational capacity'
        ],
        'citation': '65 P.S. § 67.101 et seq.; 28 Pa. Code § 709.14'
    },
    52: {
        'agency': 'DDAP',
        'subject': 'RTKL Request - 2023 Statutory Revocation Ground Citation',
        'records': [
            'DDAP enforcement action citing 2023 revocation ground',
            'Current license status and any pending revocation proceedings',
            'Correspondence with BCC/Community Care regarding revocation'
        ],
        'citation': '65 P.S. § 67.101 et seq.'
    },
    53: {
        'agency': 'DDAP / CCBH',
        'subject': 'RTKL Request - Facility Out of Compliance (709.31(b))',
        'records': [
            'Inspection reports citing 709.31(b) non-compliance',
            'Supervising physician documentation for BCC facilities',
            'CCBH oversight records for BCC/Community Care'
        ],
        'citation': '65 P.S. § 67.101 et seq.; 28 Pa. Code § 709.31'
    },
    57: {
        'agency': 'Reading Eagle / WFMZ',
        'subject': 'RTKL Request - Media Coverage of BCC/Community Care',
        'records': [
            'All articles mentioning Berks Counseling Center, Community Care, COCA, TASC (2015-present)',
            'Investigative reports on DDAP licensing or bed count issues',
            'FOIA logs for requests about these entities'
        ],
        'citation': '65 P.S. § 67.101 et seq.'
    },
    58: {
        'agency': 'DDAP / Managed Care Organizations',
        'subject': 'RTKL Request - Care-Gap Alert Thresholds / Treatment Duration Settings',
        'records': [
            'Care-gap alert threshold policies for SUD treatment',
            'Treatment-duration settings and authorization criteria',
            'Vendor documentation for alert/treatment systems used by DDAP'
        ],
        'citation': '65 P.S. § 67.101 et seq.'
    },
    60: {
        'agency': 'DDAP',
        'subject': 'RTKL Request - Unretrieved DDAP Records',
        'records': [
            'All DDAP inspection reports for BCC/Community Care (2018-present)',
            'Licensing renewal applications and correspondence',
            'Any denied or conditional licenses'
        ],
        'citation': '65 P.S. § 67.101 et seq.'
    },
    61: {
        'agency': 'DDAP / CCBH',
        'subject': 'RTKL Request - 2020 Survey Citing 709.26(b)(3) - 2018 Evaluation',
        'records': [
            'July 2020 survey citing 709.26(b)(3)',
            '2018-07-16 evaluation documentation',
            'Current evaluation status and next scheduled review'
        ],
        'citation': '65 P.S. § 67.101 et seq.; 28 Pa. Code § 709.26'
    },
    62: {
        'agency': 'DDAP',
        'subject': 'RTKL Request - Unobtained DDAP Records',
        'records': [
            'All outstanding DDAP records for BCC/Community Care not yet retrieved',
            'Status of pending records requests',
            'Estimated completion dates for pending requests'
        ],
        'citation': '65 P.S. § 67.101 et seq.'
    },
}

OUTPUT_DIR = Path(r"C:\Azazael\azazael\research\bcc_analysis\rtkl_requests")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def generate_request(gap_id, template):
    """Generate formal RTKL request letter."""
    agency = template['agency']
    subject = template['subject']
    records = template['records']
    citation = template.get('citation', '65 P.S. § 67.101 et seq.')
    
    letter = f"""RIGHT-TO-KNOW LAW REQUEST
Date: {datetime.now().strftime('%B %d, %Y')}

To: {agency}
Re: {subject}

Pursuant to the Pennsylvania Right-to-Know Law (RTKL), 65 P.S. § 67.101 et seq., 
I hereby request access to the following public records:

"""
    for i, record in enumerate(records, 1):
        letter += f"{i}. {record}\n"
    
    letter += f"""
This request is made under the legal authority of {citation}. 
I am a Pennsylvania resident requesting these records in my individual capacity.

Please provide the records in electronic format (PDF or searchable text) if available. 
If any records are exempt from disclosure, please provide a written explanation 
citing the specific statutory exemption for each withheld record.

I am willing to pay reasonable duplication fees up to $25.00. Please notify me 
if fees will exceed this amount.

Please respond within five (5) business days as required by 65 P.S. § 67.702.

Respectfully submitted,

[Requester Name]
[Address]
[Email]
[Phone]

Gap Reference: BCC-GAP-{gap_id}
"""
    return letter


def generate_all():
    """Generate all RTKL request letters."""
    output = {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'requests': {},
        'gaps_targeted': list(RTKL_REQUESTS.keys())
    }
    
    for gap_id, template in RTKL_REQUESTS.items():
        letter = generate_request(gap_id, template)
        filename = f"RTKL_BCC_GAP_{gap_id}_{template['agency'].replace(' ', '_').replace('/', '_')}.txt"
        (OUTPUT_DIR / filename).write_text(letter)
        output['requests'][str(gap_id)] = {
            'agency': template['agency'],
            'subject': template['subject'],
            'records_count': len(template['records']),
            'filename': filename
        }
        print(f"Generated: {filename}")
    
    (OUTPUT_DIR / 'rtkl_requests_index.json').write_text(json.dumps(output, indent=2))
    return output


if __name__ == '__main__':
    generate_all()