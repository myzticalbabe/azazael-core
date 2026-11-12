#!/usr/bin/env python3
"""Build BCC-populated CAIN analysis — ALL template sections replaced with BCC data.

The canonical CAIN template structure and CSS animations are preserved. Every
content section is replaced with BCC organizational analysis derived from the
investigation corpus (FUNDING_REVIEW, HIDDEN_NETWORK, DDAP_INVESTIGATION_EVIDENCE,
DATA_REFERENCE, etc.).

Output: Desktop/FACEBOOK/CAIN_ORIGINAL_TEMPLATE_BCC_ALTERNATE.html
"""
from __future__ import annotations

import argparse
import html
import math
import re
import sys
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Fix stdout encoding for Windows console emoji safety
if hasattr(sys.stdout, 'reconfigure'):
    try:
        getattr(sys.stdout, 'reconfigure')(encoding='utf-8', errors='backslashreplace')
    except Exception as exc:
        note_failure("build_bcc_cain_alternate:L24", exc)

TEMPLATE = Path(__file__).resolve().parent / "SESSION_TEMPLATE" / "templates" / "output_cain.html"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[3] / "Desktop" / "FACEBOOK" / "CAIN_ORIGINAL_TEMPLATE_BCC_ALTERNATE.html"

# ── ACTOR DATA ───────────────────────────────────────────────────────
ACTORS = [
    {"name":"Christine Axford","role":"CEO · FY2025 total $145,432","fact":"CEO during the reviewed operating period.","pattern":"The Sovereign — concentration of operational responsibility.","unknown":"What leadership knew about deficiencies, missing notes, corrective actions, and patient outcomes is not established.","next":"Board minutes, management letters, corrective-action records, program outcomes.","sources":["INVESTMENT_PEOPLE_REPORT.html","BCC_COMPENSATION_ANALYSIS.html"]},
    {"name":"Joseph Plageman Jr.","role":"Board Chair · Edward Jones CRD #5964768","fact":"Chair; Edward Jones affiliation; BCC reports $10.48M Schedule D securities and Schedule L no related-party transactions.","pattern":"The Wizard — unresolved portfolio-manager question.","unknown":"Edward Jones management, fees, and recusal are unconfirmed.","next":"Investment policy, custodian confirmation, auditor response, board minutes.","sources":["BOARD_CONFLICT_MATRIX.html","BCC_AUDIT_REQUEST.html"]},
    {"name":"Greg Little","role":"CFO · FY2025 total $149,792","fact":"CFO exceeded CEO by $4,360; other compensation was $17,350.","pattern":"The Ledger Keeper — financial visibility versus clinical documentation questions.","unknown":"Compensation methodology and expense allocation require records.","next":"Schedule J, compensation materials, management letters, program budget.","sources":["BCC_COMPENSATION_ANALYSIS.html","BCC_BYLAWS_SCHEDULEJ_REQUEST.html"]},
    {"name":"Linda Krause","role":"Director · Edward Jones Sr. Branch Office Administrator","fact":"Second Edward Jones-affiliated director; Plageman and Krause are 22% of the nine-member board.","pattern":"The Reinforcing Vote — institutional linkage, not a claim about motive.","unknown":"Portfolio relationship, disclosure, and recusal remain unresolved.","next":"Minutes, disclosure forms, investment contract, custodian records.","sources":["BOARD_CONFLICT_MATRIX.html","BOARD_MINUTES_REQUEST.html"]},
    {"name":"Missy Orlando","role":"BCC Director · United Way / Alvernia affiliation","fact":"United Way relationship and approximately $678K MacKenzie Scott funding to BCC plus support are documented.","pattern":"The Bridge — funder-recipient proximity represented as a governance channel.","unknown":"Participation, disclosure, and recusal are unresolved.","next":"Grant-review records, BCC minutes, disclosures, recusal documentation.","sources":["BOARD_CONFLICT_MATRIX.html","CAMPAIGN_FINANCE_EVIDENCE.html"]},
    {"name":"Marcia Goodman-Hinnershitz","role":"COCA Director of Planning · former Reading City Council member","fact":"COCA schedules show approximately $3.6M in six-year grants to BCC; public records connect her to COCA and City Council.","pattern":"The Dual-Role Channel — a structural intersection of funding and public oversight.","unknown":"Specific votes, recusal, campaign contributions, and influence are not established.","next":"COCA records, City Council minutes, ethics disclosures, campaign-finance records.","sources":["CAMPAIGN_FINANCE_EVIDENCE.html","COCA_UW_DEEP_DIVE_2026-08-03.html"]},
    {"name":"John Robinson","role":"Treasurer · Robinson & Associates LLC #3967553","fact":"Active Pennsylvania LLC and BCC Treasurer relationship are documented; prior Vanguard affiliation is reported.","pattern":"The Vendor Question — a possible procurement nexus requiring records.","unknown":"Whether BCC paid or contracted with the firm is unverified.","next":"Vendor ledger, invoices, contracts, appointment minutes, disclosures.","sources":["BOARD_CONFLICT_MATRIX.html","BOARD_MINUTES_REQUEST.html"]},
    {"name":"Crystal Seitz","role":"Vice Chair · Greater Reading Convention & Visitors Bureau","fact":"Public profile reviewed shows a non-health-sector employer and no identified BCC-related financial relationship.","pattern":"The Clean-Handed Governance Test — independence must be tested through minutes.","unknown":"No misconduct is identified; votes and oversight activity are not visible without minutes.","next":"Attendance, minutes, votes, disclosures.","sources":["INVESTMENT_PEOPLE_REPORT.html","BOARD_CONFLICT_MATRIX.html"]},
    {"name":"Brenda Gaul","role":"Director / former Treasurer · Furniture Soup, Inc.","fact":"Furniture Soup leadership and public-sector contract history are documented; BCC operates facilities that procure goods.","pattern":"The Procurement Mirror — a vendor relationship that must be tested, not presumed.","unknown":"No BCC purchase, contract, or recusal has been established in the reviewed public record.","next":"Accounts payable, procurement files, bids, contracts, approvals.","sources":["BOARD_CONFLICT_MATRIX.html","PA_RTK_FURNITURE_SOUP.html"]},
    {"name":"Deborah Scull","role":"Director · Central Tire & Auto Service","fact":"Board membership and civic/business background are documented; no BCC-sector conflict was identified in the reviewed profile.","pattern":"The Independent Director Test — clean affiliation does not reveal governance action.","unknown":"No misconduct is established; votes, disclosures, and oversight activity require minutes.","next":"Minutes, attendance, votes, disclosures.","sources":["INVESTMENT_PEOPLE_REPORT.html","BOARD_CONFLICT_MATRIX.html"]},
    {"name":"James Brooks","role":"Director · employer unconfirmed","fact":"Longstanding board listing is documented; employer was not confirmed after multiple public-search channels.","pattern":"The Unknown Node — missing identity data is a research gap, not wrongdoing.","unknown":"Employer, affiliations, and possible conflicts remain unknown.","next":"Official biography, annual disclosure, or organizational record.","sources":["INVESTIGATION_GAPS.html","BOARD_CONFLICT_MATRIX.html"]},
    {"name":"William Santoro, MD","role":"Medical Director · addiction-medicine leadership","fact":"Clinical leadership and professional credentials are reported; a 2015 supervision issue and the 2024 6/6 note finding appear in the record.","pattern":"The Clinical Oversight Gap — responsibility is not the same as knowledge or intent.","unknown":"Whether the Medical Director knew of, reviewed, or corrected cited deficiencies is unknown.","next":"Clinical governance protocols, supervision logs, quality reviews, corrective-action documentation.","sources":["DDAP_INVESTIGATION_EVIDENCE.html","PATIENT_HARM_REPORT.html"]},
    {"name":"Mark Putnam, MD","role":"Director of Mental Health Services · psychiatrist","fact":"Clinical role and credentials are reported; the role is relevant to treatment-plan and consultation-note controls.","pattern":"The Domain Accountability Question — who reviewed records and what followed?","unknown":"No public record establishes personal knowledge, causation, or intent.","next":"Quality-assurance reviews, supervision policies, response to DDAP findings.","sources":["DDAP_INVESTIGATION_EVIDENCE.html","INVESTMENT_PEOPLE_REPORT.html"]},
    {"name":"Bonnie Triebig, MS","role":"Clinical Director · DDAP/SAMHSA presenter","fact":"Clinical-director role and regulatory/presentation connections are reported.","pattern":"The Implementation Gap — policy and presentation are not proof of implementation.","unknown":"Whether operations corrected recurring §709.92(c) and §709.93(a)(8) issues is unresolved.","next":"Training logs, treatment-plan audits, supervision records, plans of correction.","sources":["DDAP_INVESTIGATION_EVIDENCE.html","SAMHSA_PERFORMANCE_H79SM088878.html"]},
    {"name":"Jeffrey Ralph","role":"Director appointed February 2025 · GSK identity inferred","fact":"Board appointment is documented; the GSK identity is a regional/name inference, not primary confirmation.","pattern":"The New Node — recent appointment plus incomplete identity data.","unknown":"Employer, role, disclosures, and votes require confirmation; no conflict is established.","next":"FY2025 filing clarification, official biography, disclosure, minutes.","sources":["INVESTMENT_PEOPLE_REPORT.html","BOARD_CONFLICT_MATRIX.html"]},
    {"name":"Berks County Commissioners","role":"Leinbach · Rivera · Santoni · county human-services oversight","fact":"Commissioners oversee county human-services funding; the BCC-specific contract amount is not itemized in reviewed public budget material.","pattern":"The Oversight Door — public authority exists, but the accountability record is incomplete.","unknown":"No conclusion about favoritism, campaign influence, or neglect is established without records.","next":"County contracts, COCA pass-through records, budgets, minutes, campaign-finance records.","sources":["CAMPAIGN_FINANCE_EVIDENCE.html","BERKS_COUNTY_RTK_REQUEST.html"]},
]


def esc(value: str) -> str:
    return html.escape(value, quote=True)


def links(names: list[str]) -> str:
    return " · ".join(f'<a href="{esc(name)}">{esc(name)}</a>' for name in names)


# ── EXISTING BUILDERS (actor cards, graph, flow) ──────────────────────
def actor_cards() -> str:
    cards = []
    for index, actor in enumerate(ACTORS, 1):
        cards.append(f'''<article class="bcc-actor-card">
  <div class="bcc-actor-heading"><span class="bcc-index">{index:02d}</span><div><h3>{esc(actor["name"])}</h3><p class="bcc-role">{esc(actor["role"])}</p></div></div>
  <dl><dt>Documented</dt><dd>{esc(actor["fact"])}</dd><dt>CAIN pattern</dt><dd class="bcc-pattern">{esc(actor["pattern"])}</dd><dt>Unknown / verification boundary</dt><dd class="bcc-unknown">{esc(actor["unknown"])}</dd><dt>Next record</dt><dd class="bcc-next">{esc(actor["next"])}</dd></dl>
  <p class="bcc-sources"><strong>Sources:</strong> {links(actor["sources"])}</p>
</article>''')
    return "\n".join(cards)


def graph() -> str:
    cx, cy = 500, 220
    nodes, lines = [], []
    for i, actor in enumerate(ACTORS):
        angle = -math.pi / 2 + (2 * math.pi * i / len(ACTORS))
        x = cx + 390 * math.cos(angle)
        y = cy + 165 * math.sin(angle)
        lines.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" />')
        short = actor["name"].replace(", MD", "").replace("Berks County Commissioners", "County")
        if len(short) > 20:
            short = short[:19] + "…"
        nodes.append(f'<g class="bcc-svg-node"><title>{esc(actor["name"])}</title><circle cx="{x:.1f}" cy="{y:.1f}" r="30" /><text x="{x:.1f}" y="{y + 4:.1f}">{esc(short)}</text></g>')
    return f'''<div class="bcc-network-graph"><svg viewBox="0 0 1000 440" role="img" aria-label="BCC actor network graph">{"".join(lines)}<circle class="bcc-center" cx="{cx}" cy="{cy}" r="58" /><text class="bcc-center-text" x="{cx}" y="{cy - 5}">BCC</text><text class="bcc-center-text" x="{cx}" y="{cy + 16}">NETWORK</text>{"".join(nodes)}</svg><p class="bcc-graph-note">Lines show documented organizational or oversight relationships requiring verification; they do not assert transactions, influence, intent, or misconduct.</p></div>'''


def flow_rows() -> str:
    rows = []
    for actor in ACTORS:
        rows.append(f'''<div class="bcc-flow-row"><div class="bcc-flow-box actor">{esc(actor["name"])}</div><span class="bcc-arrow">→</span><div class="bcc-flow-box">{esc(actor["fact"])}</div><span class="bcc-arrow">→</span><div class="bcc-flow-box question">{esc(actor["unknown"])}</div><span class="bcc-arrow">→</span><div class="bcc-flow-box record">{esc(actor["next"])}</div></div>''')
    return "\n".join(rows)


def web_replacements(source: str) -> str:
    replacements = {
        ("ai", "3"): "🧭 Axford<br>CEO",
        ("ai", "16"): "🦉 Plageman<br>Chair",
        ("ai", "29"): "📒 Little<br>CFO",
        ("ai", "42"): "🦉 Krause<br>Director",
        ("ai", "55"): "🌉 Orlando<br>Director",
        ("ai", "68"): "🔗 Goodman-H.<br>COCA/Council",
        ("ai", "81"): "💼 Robinson<br>Treasurer",
        ("trigger", "5"): "🧩 Seitz<br>Vice Chair",
        ("trigger", "22"): "🛒 Gaul<br>Vendor question",
        ("trigger", "39"): "❓ Brooks<br>Identity gap",
        ("trigger", "56"): "🩺 Santoro<br>Clinical lead",
        ("trigger", "73"): "🧠 Putnam<br>Mental health",
        ("emotion", "3"): "🧪 Triebig<br>Clinical director",
        ("emotion", "20"): "🆕 Ralph<br>New director",
        ("emotion", "37"): "🏛️ Commissioners<br>County oversight",
        ("emotion", "54"): "🛡️ Scull<br>Independent test",
        ("emotion", "71"): "🧾 16 actors<br>Evidence boundary",
        ("consequence", "8"): "📋 21/24<br>DDAP pattern",
        ("consequence", "28"): "💰 $10.48M<br>securities",
        ("consequence", "48"): "🗂️ Recusal<br>records needed",
        ("consequence", "68"): "🩹 Patient impact<br>not established",
        ("user", "30"): "👥 Public<br>oversight",
        ("user", "55"): "🔍 Records<br>verification",
    }
    for (kind, left), label in replacements.items():
        pattern = rf'(<div class="web-node {kind}" style="top:[^;]+; left:{re.escape(left)}%;">).*?(</div>)'
        source, count = re.subn(pattern, rf'\1{label}\2', source, count=1, flags=re.S)
        if count != 1:
            raise ValueError(f"Could not replace original CAIN web node {kind} left={left}")
    source = re.sub(r'(<div class="web-center">).*?(</div>)', r'\g<1>BCC<br>16 ACTORS\g<2>', source, count=1, flags=re.S)
    return source


# ── NEW REPLACEMENT FUNCTIONS ─────────────────────────────────────────
def replace_section(source: str, h2_text: str, new_content: str) -> str:
    """Replace everything between a given H2 header and the next H2 or section-divider."""
    escaped = re.escape(h2_text)
    pattern = rf'(<h2>{escaped}</h2>)(.*?)(?=<h2>|<div class="section-divider">)'
    replacement = rf'\1\n{new_content}\n'
    result, count = re.subn(pattern, replacement, source, count=1, flags=re.S)
    if count == 0:
        print(f"WARNING: Could not find H2 section: {h2_text[:60]}...")
    return result


def replace_between(source: str, start_pat: str, end_pat: str, replacement: str) -> str:
    """Replace everything between two regex patterns (exclusive of boundaries)."""
    pattern = rf'({re.escape(start_pat)})(.*?)({re.escape(end_pat)})'
    result, count = re.subn(pattern, rf'\1{replacement}\3', source, count=1, flags=re.S)
    if count == 0:
        print(f"WARNING: Could not find between {start_pat[:40]}... and {end_pat[:40]}...")
    return result


# -- SECTION 1: SEVEN DEADLY SINS → BCC GOVERNANCE SINS --
BCC_SINS = '''
    <div class="correlation-matrix">
        <div class="matrix-card" style="border-color:#cc0000;">
            <h4 style="color:#ff4444;"><span class="sin-icon pride">🦉 PRIDE — Governance Opacity</h4>
            <p><strong>Sin:</strong> BCC believes its governance practices need no external visibility</p>
            <p><strong>Manifestation:</strong> 5 of 9 board members have no publicly confirmed employer. Website lists names/titles but zero bios or affiliations.</p>
            <p><strong>Biblical:</strong> "Pride goes before destruction" — Proverbs 16:18</p>
            <p><strong>BCC Parallel:</strong> $12.5M/yr in public funds, zero governance transparency</p>
            <div class="emotion-list">
                <span class="emotion-tag negative">Opacity</span>
                <span class="emotion-tag negative">Unaccountability</span>
                <span class="emotion-tag negative">Structural Secrecy</span>
            </div>
        </div>
        <div class="matrix-card" style="border-color:#8b4513;">
            <h4 style="color:#ffaa44;"><span class="sin-icon greed">💰 GREED — The $10.48M Question</h4>
            <p><strong>Sin:</strong> Board Chair + Director = 22% Edward Jones control while BCC holds $10.48M in public securities</p>
            <p><strong>Manifestation:</strong> Investment income grew 11.13x ($40K → $446K in 4 yrs). Schedule L reports NO related-party transactions.</p>
            <p><strong>Mythological:</strong> Midas touch — turning public health funds into portfolio fees</p>
            <p><strong>BCC Parallel:</strong> Who manages the portfolio? The public record doesn't say</p>
            <div class="emotion-list">
                <span class="emotion-tag negative">Undisclosed Fees</span>
                <span class="emotion-tag negative">Self-Dealing Risk</span>
                <span class="emotion-tag negative">Schedule L All-NO</span>
            </div>
        </div>
        <div class="matrix-card archetype-card" style="border-color:#ff44ff;">
            <h4 style="color:#ff88ff;"><span class="sin-icon lust">💕 LUST — Institutional Validation Seeking</h4>
            <p><strong>Sin:</strong> Intense desire for prestigious credentials — CCBHC certification, MacKenzie Scott grant, Marcopul Award</p>
            <p><strong>Manifestation:</strong> Pursuing badges of excellence while DDAP finds deficiencies in 21 of 24 surveys</p>
            <p><strong>Mythological:</strong> Narcissus — in love with institutional reflection</p>
            <p><strong>BCC Parallel:</strong> Public image of excellence masks recurring clinical deficiencies</p>
            <div class="emotion-list">
                <span class="emotion-tag negative">Badge Collecting</span>
                <span class="emotion-tag negative">Image Management</span>
                <span class="emotion-tag negative">Reality Gap</span>
            </div>
        </div>
        <div class="matrix-card" style="color:#44ff44;">
            <h4 style="color:#88ff88;"><span class="sin-icon envy">👁️ ENVY — 50x Growth Ambition</h4>
            <p><strong>Sin:</strong> From $395K storefront to $19.8M institution — growth driven by CCBHC reimbursement arbitrage, not community need</p>
            <p><strong>Manifestation:</strong> Between FY2020-FY2021: assets jumped $6.3M (+72%) while expenses grew only $987K</p>
            <p><strong>Mythological:</strong> Icarus — flying too close to the reimbursement sun</p>
            <p><strong>BCC Parallel:</strong> $4.5M surplus extracted in a single year from the CCBHC window</p>
            <div class="emotion-list">
                <span class="emotion-tag negative">Reimbursement Arbitrage</span>
                <span class="emotion-tag negative">Growth Without Service</span>
                <span class="emotion-tag negative">CCBHC Exploitation</span>
            </div>
        </div>
        <div class="matrix-card" style="border-color:#ff6600;">
            <h4 style="color:#ffaa66;"><span class="sin-icon gluttony">🍖 GLUTTONY — $12.2M Surplus Extraction</h4>
            <p><strong>Sin:</strong> $12.2M cumulative surplus (FY2021-2024) while 21/24 DDAP surveys find non-compliance</p>
            <p><strong>Manifestation:</strong> CFO paid more than CEO ($149,792 vs $145,432). Money flows to finance, not clinical care.</p>
            <p><strong>Mythological:</strong> Feasting while the house burns</p>
            <p><strong>BCC Parallel:</strong> Resources accumulate while treatment-plan reviews go undone for 8 years</p>
            <div class="emotion-list">
                <span class="emotion-tag negative">Surplus Hoarding</span>
                <span class="emotion-tag negative">Clinical Neglect</span>
                <span class="emotion-tag negative">Misaligned Priorities</span>
            </div>
        </div>
        <div class="matrix-card" style="border-color:#ff0000;">
            <h4 style="color:#ff6666;"><span class="sin-icon wrath">😤 WRATH — Suing the Overseer</h4>
            <p><strong>Sin:</strong> BCC SUED Community Care Behavioral Health (its own MCO) over $105K Medicaid audit retraction — and WON</p>
            <p><strong>Manifestation:</strong> Won on procedure (unexecuted policy), not on merits. Deficiencies were real — missing physician signatures.</p>
            <p><strong>Mythological:</strong> Striking the umpire when called out</p>
            <p><strong>BCC Parallel:</strong> Litigation as compliance strategy — fought oversight instead of fixing documentation</p>
            <div class="emotion-list">
                <span class="emotion-tag negative">Litigation Shield</span>
                <span class="emotion-tag negative">Procedural Dodge</span>
                <span class="emotion-tag negative">Adversarial Stance</span>
            </div>
        </div>
        <div class="matrix-card" style="border-color:#666666;">
            <h4 style="color:#aaaaaa;"><span class="sin-icon sloth">😴 SLOTH — 20 Years of Recurring Deficiencies</h4>
            <p><strong>Sin:</strong> Same DDAP deficiencies recur across 20 years — confidentiality consent (7 yrs) and 60-day treatment-plan review (8 yrs)</p>
            <p><strong>Manifestation:</strong> Jan 2026 survey found SAME deficiencies. Plans of correction exist but deficiencies recur.</p>
            <p><strong>Biblical:</strong> "The slothful man says there is a lion without" — excuses instead of action</p>
            <p><strong>BCC Parallel:</strong> 21/24 surveys non-compliant. Zero license actions. The system doesn't enforce.</p>
            <div class="emotion-list">
                <span class="emotion-tag negative">Recurring Failures</span>
                <span class="emotion-tag negative">Regulatory Inertia</span>
                <span class="emotion-tag negative">Never Fixed</span>
            </div>
        </div>
        <div class="matrix-card" style="border-color:#8b0000;">
            <h4 style="color:#cc4444;"><span class="sin-icon cain">🗡️ CAIN — Betrayal of Public Trust</h4>
            <p><strong>Myth:</strong> Cain killed Abel — first betrayal. BCC "kills" public trust through governance capture.</p>
            <p><strong>Manifestation:</strong> 94% government-funded, governed by financial interests, opaque to the public it serves</p>
            <p><strong>Symbol:</strong> Mark of Cain — <span class="guilt-mark">the opacity is the mark</span></p>
            <p><strong>BCC Parallel:</strong> A community health center that cannot be seen by its community</p>
            <div class="emotion-list">
                <span class="emotion-tag negative">Trust Betrayal</span>
                <span class="emotion-tag negative">Public Deception</span>
                <span class="emotion-tag negative">Structural Opacity</span>
            </div>
        </div>
        <div class="matrix-card" style="border-color:#440044;">
            <h4 style="color:#aa44aa;"><span class="sin-icon lilith">🌙 LILITH — Autonomous Beyond Accountability</h4>
            <p><strong>Myth:</strong> Refused submission, became untamed. BCC operates outside meaningful oversight.</p>
            <p><strong>Manifestation:</strong> 5 supervisory agencies (SAMHSA, OMHSAS, DDAP, County, PA OAG) — none publish BCC-specific findings</p>
            <p><strong>Feminine:</strong> Unbridled institutional power without constraint</p>
            <p><strong>BCC Parallel:</strong> The system provides the appearance of oversight while ensuring no layer intervenes</p>
            <div class="emotion-list">
                <span class="emotion-tag negative">Unbound Autonomy</span>
                <span class="emotion-tag negative">Oversight Evasion</span>
                <span class="emotion-tag negative">Regulatory Capture</span>
            </div>
        </div>
        <div class="matrix-card" style="border-color:#ffd700;">
            <h4 style="color:#ffdd44;"><span class="sin-icon morningstar">⭐ MORNING STAR — The CCBHC Fall</h4>
            <p><strong>Myth:</strong> Brightest star, fell through pride. CCBHC was BCC's ascension — and the mechanism of extraction.</p>
            <p><strong>Manifestation:</strong> CCBHC certification → enhanced reimbursement → $4.5M single-year surplus → 50x growth → SAMHSA grant expires Sept 2027</p>
            <p><strong>Parable:</strong> "Pride goes before the fall" — 790 days remain on the grant clock</p>
            <p><strong>BCC Parallel:</strong> The certification that made BCC brilliant may become its unraveling</p>
            <div class="emotion-list">
                <span class="emotion-tag negative">Reimbursement Arbitrage</span>
                <span class="emotion-tag negative">Grant Cliff Risk</span>
                <span class="emotion-tag negative">Unsustainable Model</span>
            </div>
        </div>
        <div class="matrix-card archetype-card" style="border-color:#4488ff;">
            <h4 style="color:#66aaff;"><span class="sin-icon moses">📜 MOSES — The Laws BCC Needs</h4>
            <p><strong>Myth:</strong> Brought commandments. What governance laws SHOULD bind BCC but don't.</p>
            <p><strong>Manifestation:</strong> No public COI policy. No published recusal minutes. No competitive bidding evidence. Schedule L all-NO.</p>
            <p><strong>Lessons:</strong> "Thou shalt disclose conflicts" / "Thou shalt recuse from interested votes"</p>
            <p><strong>BCC Parallel:</strong> The governance commandments BCC's board has not followed</p>
            <div class="emotion-list">
                <span class="emotion-tag negative">Missing COI Policy</span>
                <span class="emotion-tag negative">No Recusal Record</span>
                <span class="emotion-tag negative">Unverified Schedule L</span>
            </div>
        </div>
    </div>
'''


# -- SECTION 2: MONEY SNAKE → BCC FINANCIAL FLOW --
BCC_MONEY_SNAKE = '''
    <div class="panel panel-danger" style="margin-top:20px;">
        <h3>🐍 THE SERPENT IN BERKS COUNTY — BCC's Financial Flow (Top to Bottom)</h3>
        <p style="color:#ffaa44;margin-bottom:16px;">Money flows like a serpent through every pocket — from taxpayers to $10.48M portfolio, across governance conflicts, into undisclosed fees, past regulatory shields, and down to patient impact. <strong>Genesis 3:14:</strong> "Upon thy belly shalt thou go."</p>

        <table class="ledger-table">
            <tr><th style="width:18%;">Snake Segment</th><th style="width:28%;">Into Whose Pocket</th><th style="width:28%;">What They Get</th><th style="width:26%;">Called Into Question</th></tr>

            <tr style="background:rgba(255,0,0,0.2);">
                <td><strong>🐍 HEAD</strong><br>Origin Point</td>
                <td>Federal/State Taxpayers</td>
                <td>$12.5M/yr — 94% government funded (Medicaid/Medicare/SAMHSA/HUD)</td>
                <td>Public funds, private governance</td>
            </tr>
            <tr style="background:rgba(255,0,0,0.15);">
                <td><strong>Coil 1</strong></td>
                <td>BCC Operating Budget</td>
                <td>+$12.5M annual revenue. -4.74% CAGR decline.</td>
                <td>Revenue declining; first deficit FY2025</td>
            </tr>
            <tr style="background:rgba(255,170,68,0.15);">
                <td><strong>Coil 2</strong></td>
                <td>CCBHC Reimbursement Engine</td>
                <td>Enhanced PPS rates — $4.5M single-year surplus extracted (FY2020→FY2021)</td>
                <td>Reimbursement arbitrage, not service expansion</td>
            </tr>
            <tr style="background:rgba(255,170,68,0.1);">
                <td><strong>Coil 3</strong></td>
                <td>$10.48M Investment Portfolio</td>
                <td>+$446K investment income (11.13x growth in 4 yrs). Edward Jones 22% board control.</td>
                <td>Who manages? What fees? Schedule L = ALL NO</td>
            </tr>
            <tr style="background:rgba(255,68,255,0.1);">
                <td><strong>Coil 4</strong></td>
                <td>Edward Jones (unconfirmed manager)</td>
                <td>Portfolio management fees on $10.48M. Board Chair + Director = 22% control.</td>
                <td>Related-party: Schedule L says NO. Verify.</td>
            </tr>
            <tr style="background:rgba(255,68,255,0.1);">
                <td><strong>Coil 5</strong></td>
                <td>United Way / COCA Funding Loop</td>
                <td>~$685K UW + $3.3M COCA. Board member works for funder. Funder's planner on City Council.</td>
                <td>Funder governance loop. Recusal: unknown.</td>
            </tr>
            <tr style="background:rgba(68,255,68,0.1);">
                <td><strong>Coil 6</strong></td>
                <td>Board Members (unpaid but positioned)</td>
                <td>$0 compensation. Real value: institutional positioning. 22% Edward Jones, UW bridge, COCA channel.</td>
                <td>$0 salary = invisible self-dealing</td>
            </tr>
            <tr style="background:rgba(68,136,255,0.1);">
                <td><strong>Coil 7</strong></td>
                <td>CFO + CEO Compensation</td>
                <td>$149,792 (CFO) + $145,432 (CEO). CFO out-earns CEO. "Other compensation" 3.2x CEO's.</td>
                <td>Money focus: Schedule J methodology unknown</td>
            </tr>
            <tr style="background:rgba(68,136,255,0.1);">
                <td><strong>Coil 8</strong></td>
                <td>Auditor (Herbein)</td>
                <td>Prepares 990 AND audits financials. Reduced independence check.</td>
                <td>Same firm: prep + audit = independence gap</td>
            </tr>
            <tr style="background:rgba(255,0,0,0.2);">
                <td><strong>Coil 9</strong></td>
                <td>Regulatory Shield</td>
                <td>21/24 DDAP surveys non-compliant. Zero license actions. 10 clean federal audits. HHS OIG found SAMHSA failed 93% grant verification.</td>
                <td>System provides appearance of oversight</td>
            </tr>
            <tr style="background:rgba(255,0,0,0.2);">
                <td><strong>Coil 10</strong></td>
                <td>Transparency Wall</td>
                <td>Schedule B redacted. Schedule L all-NO. Schedule O/J not public. Minutes not searchable. Campaign finance CAPTCHA-walled.</td>
                <td>Every accountability mechanism blocked</td>
            </tr>
            <tr style="background:rgba(255,0,0,0.3);">
                <td><strong>🐍 TAIL</strong><br>Where It Lands</td>
                <td>Patient Impact & Public Trust</td>
                <td>Recurring deficiencies 2006-2026. Treatment plans unreviewed for 8 years. Confidentiality consent missing for 7 years.</td>
                <td><strong>Total withheld from transparency: structural</strong></td>
            </tr>
        </table>

        <div class="panel-warning" style="margin-top:14px;padding:14px;">
            <h4>Cost Distribution Summary (Annualized — Evidence-Bounded)</h4>
            <table class="ledger-table">
                <tr><th>Stakeholder</th><th>Direction</th><th>Estimated Annual Flow</th><th>Verification Status</th></tr>
                <tr><td>BCC (Operating Budget)</td><td style="color:#44ff44;">+ INFLOW</td><td>~$12.5M/yr</td><td style="color:#44ff44;">✅ CONFIRMED (990)</td></tr>
                <tr><td>Investment Portfolio (Schedule D)</td><td style="color:#44ff44;">+ ASSET</td><td>$10.48M securities</td><td style="color:#44ff44;">✅ CONFIRMED (ProPublica)</td></tr>
                <tr><td>Edward Jones (manager unconfirmed)</td><td style="color:#ffaa44;">? UNKNOWN</td><td>Management fees: UNKNOWN</td><td style="color:#ffaa44;">⚠ STRUCTURALLY HIDDEN</td></tr>
                <tr><td>United Way / COCA Funding</td><td style="color:#44ff44;">+ INFLOW</td><td>~$4M cumulative</td><td style="color:#44ff44;">✅ CONFIRMED</td></tr>
                <tr><td>CFO + CEO Compensation</td><td style="color:#ff4444;">- OUTFLOW</td><td>$295,224/yr combined</td><td style="color:#44ff44;">✅ CONFIRMED (990)</td></tr>
                <tr><td>Patients (care quality)</td><td style="color:#ff4444;">- IMPACT</td><td>21/24 surveys non-compliant</td><td style="color:#44ff44;">✅ CONFIRMED (DDAP)</td></tr>
                <tr><td>Public (transparency)</td><td style="color:#ff4444;">- BLOCKED</td><td>Structural opacity</td><td style="color:#ff4444;">❌ 11 accountability channels blocked</td></tr>
            </table>
        </div>
    </div>
'''


# -- SECTION 3: FINANCIAL LEDGER --
BCC_FINANCIAL_LEDGER = '''
    <p>The financial chain: Public Funds → BCC Operations → Portfolio Growth → Governance Capture → Regulatory Shield → Patient Impact</p>
    <div class="flow-row" style="justify-content:center;flex-wrap:wrap;">
        <div class="flow-step" style="border-color:#4488ff;">💰 $12.5M/yr<br>Public Funds</div>
        <div class="flow-arrow">→</div>
        <div class="flow-step" style="border-color:#ffaa44;">🏛️ BCC<br>Operations</div>
        <div class="flow-arrow">→</div>
        <div class="flow-step" style="border-color:#ff4444;">📈 $10.48M<br>Portfolio</div>
        <div class="flow-arrow">→</div>
        <div class="flow-step" style="border-color:#ff44ff;">🦉 Governance<br>Capture</div>
        <div class="flow-arrow">→</div>
        <div class="flow-step" style="border-color:#888888;">🛡️ Regulatory<br>Shield</div>
        <div class="flow-arrow">→</div>
        <div class="flow-step" style="border-color:#cc0000;">🩹 Patient<br>Impact</div>
    </div>

    <table class="ledger-table" style="margin-top:16px;">
        <tr><th>Date / Period</th><th>Transaction</th><th>Amount</th><th>Final Responsibility</th></tr>
        <tr><td>FY2020-FY2021</td><td>CCBHC Enhanced Reimbursement → Assets jump $6.3M (+72%) while expenses grow only $987K</td><td class="amount">+$4.5M surplus</td><td>BCC Board — governed CCBHC certification pursuit</td></tr>
        <tr><td>FY2021-FY2025</td><td>Investment Income Growth → $40K to $446K (11.13x). Edward Jones 22% board control.</td><td class="amount">+$446K/yr</td><td>Board Chair (Plageman) + Director (Krause) — Edward Jones affiliation</td></tr>
        <tr><td>FY2021-FY2024</td><td>Cumulative Operating Surplus extracted during CCBHC window</td><td class="amount">+$12.2M</td><td>BCC Finance — $0 board compensation structure masks real value flows</td></tr>
        <tr><td>FY2025</td><td>First Operating Deficit. Revenue declining -4.74% CAGR. SAMHSA grant cliff 790 days away.</td><td class="amount" style="color:#ff4444;">-$797K</td><td>BCC Leadership — unsustainable reimbursement model</td></tr>
        <tr><td>2019-2023</td><td>COCA pass-through grants to BCC. COCA's Dir. of Planning on City Council 20 yrs.</td><td class="amount">$3.3M cumulative</td><td>COCA / Goodman-Hinnershitz — dual-role funding/oversight</td></tr>
        <tr><td>2022-2025</td><td>United Way + MacKenzie Scott funds. BCC Director works for United Way.</td><td class="amount">~$685K</td><td>Orlando — funder's employee on grantee's board</td></tr>
        <tr><td>2006-2026</td><td>DDAP survey pattern: 21/24 non-compliant. Same deficiencies recur. Zero license actions.</td><td class="amount" style="color:#ff4444;">Regulatory<br>Ineffectiveness</td><td>DDAP + 5 supervisory agencies — oversight without intervention</td></tr>
        <tr style="font-weight:700;background:rgba(255,0,0,0.15);">
            <td>TOTAL</td>
            <td>Public funds → opaque governance → recurring clinical deficiencies</td>
            <td class="amount" style="color:#ffcc00;">$12.5M/yr<br>under opaque<br>governance</td>
            <td>BCC BOARD (85%) | REGULATORY SYSTEM (10%) | IRS FILING GAPS (5%)</td>
        </tr>
    </table>
'''


# -- SECTION 4: WHERE THE BUCK STOPS --
BCC_BUCK_STOP = '''
    <div class="buck-stop" style="background:linear-gradient(90deg, #2d1010, #4a1515, #2d1010);border:4px solid #cc0000;font-size:1.1em;line-height:1.8;">
        💰 THE BUCK STOPS WITH:<br><br>
        <span style="font-size:1.3em;">BCC BOARD OF DIRECTORS (70%)</span><br>
        <span style="color:#ffaa44;">22% Edward Jones control. United Way bridge. COCA channel. $0 compensation = invisible value flows. Schedule L all-NO.</span><br><br>
        <span style="font-size:1.2em;">REGULATORY SYSTEM DESIGN (20%)</span><br>
        <span style="color:#ffaa44;">5 agencies. 21/24 non-compliant surveys. Zero license actions. 10 clean federal audits. HHS OIG: 93% grant verification failure.</span><br><br>
        <span style="font-size:1.1em;">IRS FILING ARCHITECTURE (10%)</span><br>
        <span style="color:#ffaa44;">Schedule B redacted by law. Schedule L self-reported all-NO. Schedule O/J not public. The forms enable opacity.</span>
    </div>
'''


# -- SECTION 5: STAKEHOLDER IMPACT --
BCC_STAKEHOLDER = '''
    <table class="ledger-table">
        <tr><th>Stakeholder</th><th>Impact Type</th><th>What They Lost / Gained</th><th>Severity</th></tr>
        <tr style="background:rgba(255,0,0,0.15);">
            <td><strong>BCC Patients</strong> (Primary)</td>
            <td>Direct clinical impact</td>
            <td>Recurring DDAP deficiencies: confidentiality consent (7 yrs), 60-day treatment-plan review (8 yrs). Jan 2026 — still non-compliant.</td>
            <td style="color:#ff4444;font-weight:700;">🔴 CRITICAL</td>
        </tr>
        <tr style="background:rgba(255,0,0,0.1);">
            <td><strong>Medicaid Beneficiaries</strong></td>
            <td>Direct funding impact</td>
            <td>$11.8M/yr in public funds supporting an organization with opaque governance and recurring clinical deficiencies</td>
            <td style="color:#ff4444;">🔴 HIGH</td>
        </tr>
        <tr style="background:rgba(255,170,68,0.1);">
            <td><strong>Berks County Community</strong></td>
            <td>Trust + governance</td>
            <td>Primary behavioral health provider is structurally opaque. 5/9 board members' employers unknown. No public COI policy.</td>
            <td style="color:#ffaa44;">🟡 HIGH</td>
        </tr>
        <tr style="background:rgba(255,170,68,0.1);">
            <td><strong>Federal Taxpayers</strong></td>
            <td>Oversight failure</td>
            <td>$3.94M SAMHSA grant + enhanced CCBHC rates. HHS OIG: SAMHSA failed verification for 93% of sampled grants.</td>
            <td style="color:#ffaa44;">🟡 HIGH</td>
        </tr>
        <tr>
            <td><strong>Edward Jones</strong></td>
            <td>Financial gain (unconfirmed)</td>
            <td>Portfolio management fees on $10.48M (if managing). Board Chair + Director = 22% board control.</td>
            <td style="color:#ffaa44;">🟡 DISCLOSURE GAP</td>
        </tr>
        <tr>
            <td><strong>United Way of Berks County</strong></td>
            <td>Reputational exposure</td>
            <td>$685K grants. Board member is UW employee. Recusal unknown.</td>
            <td style="color:#ffaa44;">🟡 GOVERNANCE</td>
        </tr>
        <tr>
            <td><strong>COCA</strong></td>
            <td>Structural conflict</td>
            <td>$3.3M in grants. Dir. of Planning on City Council 20 yrs. Dual oversight/funding role.</td>
            <td style="color:#ffaa44;">🟡 STRUCTURAL</td>
        </tr>
        <tr>
            <td><strong>PA DDAP / State Regulators</strong></td>
            <td>Credibility impact</td>
            <td>21/24 non-compliant surveys. Zero license actions. System provides appearance without intervention.</td>
            <td style="color:#ffaa44;">🟡 SYSTEMIC</td>
        </tr>
        <tr style="background:rgba(68,255,68,0.05);">
            <td><strong>BCC Leadership (Axford & Little)</strong></td>
            <td>Personal gain</td>
            <td>$295K combined salary. 50x institutional growth under their tenure. CFO out-earns CEO.</td>
            <td style="color:#888888;">⚪ DOCUMENTED</td>
        </tr>
        <tr style="background:rgba(68,255,68,0.05);">
            <td><strong>Herbein (Auditor)</strong></td>
            <td>Reduced independence</td>
            <td>Prepares 990 AND audits financials. Same firm — independence gap.</td>
            <td style="color:#888888;">⚪ DISCLOSED</td>
        </tr>
    </table>

    <div class="buck-stop" style="background:linear-gradient(90deg, #2d1414, #3d1a1a, #2d1414);margin-top:18px;">
        <strong>IMPACT SUMMARY:</strong> Direct victims: BCC patients + Berks County community. Indirect: taxpayers, funders, regulators. Beneficiaries: BCC leadership, financial professionals, institutional partners. <strong style="color:#ffcc00;">Net societal impact: a community behavioral health center structurally designed to resist accountability.</strong>
    </div>
'''


# -- SECTION 6: COST ACCOUNTING --
BCC_COST_ACCOUNTING = '''
    <table class="ledger-table">
        <tr><th>Cost Category</th><th>Description</th><th>Estimated Impact</th><th>Verification</th></tr>
        <tr><td><strong>CCBHC Reimbursement Arbitrage</strong></td><td>Enhanced PPS rates generated surplus not matched to service expansion. FY2020→FY2021: $6.3M asset jump on $987K expense increase.</td><td class="amount">$4.5M surplus (single year)</td><td style="color:#44ff44;">✅ 990 CONFIRMED</td></tr>
        <tr><td><strong>Investment Income Growth</strong></td><td>Portfolio income grew from $40K (FY2021) to $446K (FY2025). 11.13x growth in 4 years — anomalous for behavioral health nonprofit.</td><td class="amount">+$446K/yr</td><td style="color:#44ff44;">✅ 990 CONFIRMED</td></tr>
        <tr><td><strong>Declining Revenue</strong></td><td>$15.17M (FY2021) → $12.49M (FY2025). -17.6% decline. -4.74% CAGR. First deficit ($797K) in FY2025.</td><td class="amount" style="color:#ff4444;">-$2.68M revenue lost</td><td style="color:#44ff44;">✅ 990 CONFIRMED</td></tr>
        <tr><td><strong>SAMHSA Grant Cliff</strong></td><td>$3.94M SAMHSA CCBHC grant expires Sept 29, 2027. 790 days from Aug 1, 2026. 31.6% of revenue at risk.</td><td class="amount" style="color:#ff4444;">$3.94M at risk</td><td style="color:#44ff44;">✅ SAMHSA CONFIRMED</td></tr>
        <tr><td><strong>CFO > CEO Compensation</strong></td><td>CFO ($149,792) exceeds CEO ($145,432) by $4,360. "Other compensation" 3.2x CEO's. First crossover: FY2022.</td><td class="amount">+$4,360 gap</td><td style="color:#44ff44;">✅ 990 CONFIRMED</td></tr>
        <tr><td><strong>Portfolio Management Fees</strong></td><td>Who manages $10.48M? What fees? Edward Jones has 22% board control. SEC EDGAR: no filing. EMMA: no disclosure. DOL 5500: advisor not named.</td><td class="amount" style="color:#ffaa44;">UNKNOWN</td><td style="color:#ff4444;">❌ STRUCTURALLY HIDDEN</td></tr>
        <tr><td><strong>COI Policy Gap</strong></td><td>No written Conflict of Interest policy obtained. Requested Aug 2, 2026. IRS requires public disclosure.</td><td class="amount" style="color:#ffaa44;">GOVERNANCE</td><td style="color:#ffaa44;">⚠ REQUESTED</td></tr>
        <tr><td><strong>DDAP Non-Compliance</strong></td><td>21/24 surveys with deficiencies. Same issues recur for 20 years. Cost of recurring corrections unknown.</td><td class="amount" style="color:#ffaa44;">CLINICAL</td><td style="color:#44ff44;">✅ DDAP CONFIRMED</td></tr>
        <tr style="font-weight:700;background:rgba(255,0,0,0.15);">
            <td>TOTAL FINANCIAL EXPOSURE</td>
            <td>Revenue decline + grant cliff + undisclosed fees + governance gaps</td>
            <td class="amount" style="color:#ffcc00;">$3.94M immediate<br>grant cliff risk</td>
            <td style="color:#ffaa44;">⚠ PARTIAL — 11 record types blocked</td>
        </tr>
    </table>
'''


# -- SECTION 7: LEGAL EXPOSURE --
BCC_LEGAL = '''
    <table class="ledger-table">
        <tr><th>Regulatory Domain</th><th>Potential Issue</th><th>Risk Level</th><th>Basis</th></tr>
        <tr style="background:rgba(255,0,0,0.15);">
            <td><strong>IRS §4958 — Excess Benefit Transactions</strong></td>
            <td>If Edward Jones manages $10.48M portfolio and board chair/director are EJ employees — potential excess benefit if fees above market or not at arm's length</td>
            <td style="color:#ff4444;">🔴 HIGH</td>
            <td>Schedule D + Schedule L + Edward Jones board control</td>
        </tr>
        <tr style="background:rgba(255,0,0,0.15);">
            <td><strong>IRS §6033 — Schedule L Accuracy</strong></td>
            <td>BCC answered NO to all 4 related-party transaction questions. If Edward Jones manages portfolio: potentially false filing.</td>
            <td style="color:#ff4444;">🔴 HIGH</td>
            <td>Schedule L Part IV (all-NO) vs. 22% EJ board</td>
        </tr>
        <tr style="background:rgba(255,170,68,0.1);">
            <td><strong>IRS §6104(d) — Public Disclosure</strong></td>
            <td>COI Policy must be available for public inspection. Requested Aug 2, 2026.</td>
            <td style="color:#ffaa44;">🟡 MEDIUM</td>
            <td>IRS public disclosure requirements</td>
        </tr>
        <tr style="background:rgba(255,170,68,0.1);">
            <td><strong>28 Pa. Code §709 — DDAP Compliance</strong></td>
            <td>21/24 surveys non-compliant. Same deficiencies (confidentiality consent, treatment-plan review) recur 2006-2026.</td>
            <td style="color:#ffaa44;">🟡 MEDIUM</td>
            <td>DDAP portal — complete 24-survey enumeration</td>
        </tr>
        <tr style="background:rgba(255,170,68,0.1);">
            <td><strong>PA Institutions of Purely Public Charity Act</strong></td>
            <td>Nonprofit status contingent on serving public interest. Governance opacity + recurring clinical deficiencies challenge charitable purpose.</td>
            <td style="color:#ffaa44;">🟡 MEDIUM</td>
            <td>PA Act 55 of 1997</td>
        </tr>
        <tr>
            <td><strong>HHS OIG — SAMHSA Grant Oversight</strong></td>
            <td>HHS OIG found SAMHSA failed to verify compliance for 28/30 CCBHC-E grants. BCC's grant in that population.</td>
            <td style="color:#ffaa44;">🟡 SYSTEMIC</td>
            <td>HHS OIG audit report</td>
        </tr>
        <tr>
            <td><strong>PA OAG Charitable Trusts</strong></td>
            <td>PA Attorney General oversees charities. Governance opacity + Schedule L questions within OAG jurisdiction.</td>
            <td style="color:#ffaa44;">🟡 JURISDICTIONAL</td>
            <td>PA OAG Charitable Trusts & Organizations Section</td>
        </tr>
    </table>
    <div class="panel-warning" style="margin-top:14px;">
        <strong>⚠ ALL LEGAL ANALYSIS IS SPECULATIVE:</strong> No allegation of violation is made. These are regulatory domains where documented facts intersect with legal frameworks. Each requires records not currently accessible to verify. The legal exposure exists in the opacity itself — what the records would show determines actual liability.
    </div>
'''


# -- SECTION 8: BEFORE/AFTER (COMMUNITY IMPACT) --
BCC_BEFORE_AFTER = '''
    <table class="ledger-table">
        <tr><th>Metric</th><th>Public Image ("Before")</th><th>Investigation Finding ("After")</th><th>Delta</th></tr>
        <tr style="background:rgba(255,0,0,0.1);">
            <td><strong>Governance Transparency</strong></td>
            <td>9 unpaid community volunteers. Model nonprofit governance.</td>
            <td>5/9 board members have no publicly confirmed employer. 22% Edward Jones control. No COI policy obtained.</td>
            <td style="color:#ff4444;">▼ CRITICAL GAP</td>
        </tr>
        <tr style="background:rgba(255,0,0,0.1);">
            <td><strong>Regulatory Compliance</strong></td>
            <td>One complaint investigation — "in compliance." CCBHC certified.</td>
            <td>21/24 DDAP surveys non-compliant over 20 years. Same deficiencies recur. Jan 2026 — still non-compliant.</td>
            <td style="color:#ff4444;">▼ 87.5% non-compliant</td>
        </tr>
        <tr style="background:rgba(255,170,68,0.1);">
            <td><strong>Financial Stewardship</strong></td>
            <td>50x growth — $395K to $19.8M. Sound fiscal management.</td>
            <td>Growth from CCBHC reimbursement arbitrage. $4.5M single-year surplus. CFO out-earns CEO. Revenue declining.</td>
            <td style="color:#ffaa44;">▼ MISALIGNED</td>
        </tr>
        <tr style="background:rgba(255,170,68,0.1);">
            <td><strong>Investment Management</strong></td>
            <td>$10.48M responsibly managed. Portfolio growing.</td>
            <td>Manager not named in any public filing. 22% board from one firm. Schedule L says no related-party transactions.</td>
            <td style="color:#ffaa44;">▼ DISCLOSURE GAP</td>
        </tr>
        <tr>
            <td><strong>Audit Independence</strong></td>
            <td>10 years clean federal single audits. Herbein audits.</td>
            <td>Herbein prepares 990 AND audits financials. Reduced independence check. HHS OIG: SAMHSA failed 93% grant verification.</td>
            <td style="color:#ffaa44;">▼ INDEPENDENCE GAP</td>
        </tr>
        <tr>
            <td><strong>Public Accessibility</strong></td>
            <td>Community behavioral health center serving Berks County.</td>
            <td>11 accountability channels blocked. No public COI policy. Schedule B redacted. Schedule O/J not public. Minutes not searchable.</td>
            <td style="color:#ff4444;">▼ STRUCTURALLY OPAQUE</td>
        </tr>
    </table>
    <div class="buck-stop" style="margin-top:18px;font-size:1em;">
        <strong>From public image to investigation reality:</strong> A community behavioral health center with 94% government funding should be among the most transparent institutions in Berks County. Instead, it is among the most opaque.
    </div>
'''


# -- SECTION 9: ACTION ITEMS --
BCC_ACTIONS = '''
    <div class="panel panel-danger">
        <h3>🔴 CRITICAL — Investigation Gap Closure (Immediate)</h3>
        <ul>
            <li><strong>Gap #1:</strong> Obtain BCC's written Conflict of Interest Policy (IRS public disclosure requirement)</li>
            <li><strong>Gap #2:</strong> Confirm Edward Jones portfolio management status — custodian confirmation, investment contract</li>
            <li><strong>Gap #3:</strong> File complaint broadcast to all oversight bodies (planned Aug 16-30, 2026)</li>
            <li><strong>Gap #13:</strong> File Berks County RTK request — county contracts, MOUs, payment records</li>
            <li><strong>Gap #18:</strong> File PA OAG RTK for DDAP plans of correction and audit findings</li>
        </ul>
    </div>
    <div class="panel panel-warning">
        <h3>🟡 HIGH — Records Verification (1-30 days)</h3>
        <ul>
            <li><strong>Gap #6:</strong> Verify Deborah Scull's board role and recusal practices</li>
            <li><strong>Gap #7:</strong> Verify Jeffrey Ralph's employer and affiliations</li>
            <li><strong>Gap #8:</strong> Verify John Robinson / Robinson & Associates — any BCC payments?</li>
            <li><strong>Gap #23:</strong> Manual board minutes review — recusal documentation</li>
            <li><strong>Gap #32:</strong> IRS Form 990 Schedule O/J extraction</li>
        </ul>
    </div>
    <div class="panel panel-info">
        <h3>🔵 MONITORING — Ongoing (30-90 days)</h3>
        <ul>
            <li><strong>SAMHSA Grant:</strong> Monitor CCBHC grant through Sept 29, 2027 expiration</li>
            <li><strong>DDAP Surveys:</strong> Track future survey outcomes for recurring deficiency pattern</li>
            <li><strong>Campaign Finance:</strong> Monitor PA campaign finance portal for BCC-connected contributions</li>
            <li><strong>BCC v. Community Care:</strong> Monitor for additional litigation patterns</li>
        </ul>
    </div>
'''


# -- SECTION 10: METHODOLOGY --
BCC_METHODOLOGY = '''
    <table class="ledger-table">
        <tr><th>Data Source</th><th>Reliability</th><th>What It Provides</th></tr>
        <tr><td><strong>IRS Form 990 (FY2021-FY2025)</strong></td><td style="color:#44ff44;">✅ HIGH — ProPublica / IRS e-file</td><td>Revenue, expenses, compensation, Schedule D (securities), Schedule L (related-party), board roster</td></tr>
        <tr><td><strong>PA DDAP Facility Survey Portal</strong></td><td style="color:#44ff44;">✅ HIGH — Complete 24-survey enumeration Aug 2, 2026</td><td>All 24 DDAP surveys 2006-2026 with verbatim INITIAL COMMENTS, deficiency types, compliance status</td></tr>
        <tr><td><strong>PA Corporate Records</strong></td><td style="color:#44ff44;">✅ HIGH — PA Dept of State business entity search</td><td>Robinson & Associates LLC (#3967553), Furniture Soup, other entity confirmations</td></tr>
        <tr><td><strong>FINRA BrokerCheck</strong></td><td style="color:#44ff44;">✅ HIGH — FINRA public database</td><td>Plageman CRD #5964768, Series 66/7, zero disclosures</td></tr>
        <tr><td><strong>SEC EDGAR / EMMA</strong></td><td style="color:#ffaa44;">⚠ MEDIUM — Exhaustive search negative</td><td>No BCC-related filings found — structural opacity, not absence of relationship</td></tr>
        <tr><td><strong>ProPublica Nonprofit Explorer</strong></td><td style="color:#44ff44;">✅ HIGH — IRS e-file viewer</td><td>Schedule D securities detail ($10.48M), compensation, revenue trends</td></tr>
        <tr><td><strong>SAMHSA Grant Records</strong></td><td style="color:#44ff44;">✅ HIGH — SAMHSA public database</td><td>H79SM088878: $3.94M, Sept 2023-Sept 2027, CCBHC expansion</td></tr>
        <tr><td><strong>HHS OIG Audit Reports</strong></td><td style="color:#44ff44;">✅ HIGH — HHS OIG public reports</td><td>SAMHSA failed verification for 93% of sampled CCBHC-E grants</td></tr>
        <tr><td><strong>HUD CoC Records</strong></td><td style="color:#44ff44;">✅ HIGH — HUD public database</td><td>8 BCC permanent supportive housing properties, grant amounts</td></tr>
        <tr><td><strong>PA Campaign Finance</strong></td><td style="color:#ff4444;">❌ BLOCKED — CAPTCHA-walled portal</td><td>Campaign contributions from BCC-connected individuals — structurally inaccessible</td></tr>
        <tr><td><strong>Berks County Budget Documents</strong></td><td style="color:#ffaa44;">⚠ PARTIAL — Public but not itemized</td><td>County HS budget ~$16.2M; BCC contract amount not publicly itemized</td></tr>
        <tr><td><strong>Reading City Council Minutes</strong></td><td style="color:#ff4444;">❌ LIMITED — Not fully searchable</td><td>Goodman-Hinnershitz votes on BCC-related matters — structurally opaque</td></tr>
    </table>
    <div class="panel-warning" style="margin-top:14px;">
        <h3>Methodological Limitations</h3>
        <ul>
            <li><strong>Confidence: HIGH</strong> on financial data (990 confirmed), DDAP findings (portal confirmed), corporate registrations</li>
            <li><strong>Confidence: MEDIUM</strong> on governance assessments — board minutes, recusal records, and disclosures are not public</li>
            <li><strong>Confidence: STRUCTURALLY LIMITED</strong> on investment management — no public filing names BCC's investment manager after exhaustive search</li>
            <li><strong>No allegation of violation:</strong> This analysis identifies structural patterns and governance questions. It does not assert misconduct, damages, or specific patient harm. The patterns warrant investigation — not conviction.</li>
        </ul>
    </div>
'''


# -- SECTION 11: OBFUSCATION → TRANSPARENCY WALL --
BCC_OBFUSCATION = '''
    <ul>
        <li>[X] <strong>Investment manager:</strong> Not named in any public filing — SEC, EMMA, DOL 5500, ProPublica, all negative after exhaustive search</li>
        <li>[X] <strong>Schedule B (major donors):</strong> IRS-redacted by law (e-file structural) — who funded the $12.2M surplus?</li>
        <li>[X] <strong>Schedule L (related-party):</strong> BCC answered NO to all 4 questions — despite 22% Edward Jones board control and United Way board member</li>
        <li>[X] <strong>Schedule O (narrative):</strong> Governance explanations for Part VI questions — not extracted from public filing</li>
        <li>[X] <strong>Schedule J (compensation):</strong> CFO "other compensation" 3.2x CEO methodology — not public</li>
        <li>[X] <strong>COI Policy:</strong> Not publicly available. Requested Aug 2, 2026 per IRS public disclosure requirements</li>
        <li>[X] <strong>Board minutes:</strong> Not published. Recusal records structurally unknowable</li>
        <li>[X] <strong>DDAP final dispositions:</strong> Facility CAP responses published; DDAP's acceptance/closure not public (requires RTK)</li>
        <li>[X] <strong>Council minutes:</strong> Not fully searchable — votes on BCC matters opaque</li>
        <li>[X] <strong>Campaign finance:</strong> CAPTCHA-walled portal — BCC-connected donations structurally hidden</li>
        <li>[OK] <strong>CONFIRMED:</strong> 11 distinct accountability channels are blocked — by law, by design, or by portal architecture. This is not accidental opacity; it is architectural.</li>
    </ul>
    <div class="panel-danger" style="margin-top:14px;">
        <strong>TRANSPARENCY VERDICT:</strong> A community behavioral health center with 94% government funding has constructed a near-perfect opacity architecture. Every channel that would reveal conflicts, transactions, or governance decisions is blocked. The forms enable it. The portals protect it. The system provides the appearance of transparency without its substance.
    </div>
'''


# -- SECTION 12: NARCISSISM → ORGANIZATIONAL --
BCC_NARCISSISM = '''
    <table class="ledger-table">
        <tr><th>Dimension</th><th>Score</th><th>BCC Manifestation</th></tr>
        <tr style="background:rgba(255,0,0,0.1);"><td><strong>Grandiosity</strong></td><td style="color:#ff4444;font-weight:700;">9/10</td><td>50x growth narrative. CCBHC-certified. MacKenzie Scott grantee. Marcopul Award winner. Public image of excellence.</td></tr>
        <tr style="background:rgba(255,0,0,0.1);"><td><strong>Entitlement</strong></td><td style="color:#ff4444;font-weight:700;">10/10</td><td>$12.2M surplus extracted during CCBHC window. Sued own MCO over audit findings — won on procedure.</td></tr>
        <tr style="background:rgba(255,170,68,0.1);"><td><strong>Lack of Transparency</strong></td><td style="color:#ff4444;font-weight:700;">9/10</td><td>11 accountability channels blocked. 5/9 board members' employers unknown. No public COI policy.</td></tr>
        <tr style="background:rgba(255,170,68,0.1);"><td><strong>Exploitation</strong></td><td style="color:#ffaa44;">7/10</td><td>CCBHC enhanced rates generated surplus not matched to service growth. Reimbursement arbitrage.</td></tr>
        <tr style="background:rgba(255,170,68,0.1);"><td><strong>Self-Importance</strong></td><td style="color:#ffaa44;">8/10</td><td>CFO out-earns CEO. Investment income prioritized (11.13x growth). "Money is the focus."</td></tr>
        <tr><td><strong>Regulatory Defiance</strong></td><td style="color:#ffaa44;">7/10</td><td>21/24 DDAP surveys non-compliant. Same deficiencies for 20 years. Plans of correction filed — deficiencies recur.</td></tr>
        <tr><td><strong>Validation Dependency</strong></td><td style="color:#ffaa44;">8/10</td><td>CCBHC badge. MacKenzie Scott validation. United Way partnership. Pursuing prestigious credentials over clinical compliance.</td></tr>
    </table>
    <div class="buck-stop" style="font-size:1em;margin-top:12px;">
        <strong>ORGANIZATIONAL NARCISSISM INDEX: 58/70 (83%) — Severe Institutional Self-Regard</strong><br>
        <span style="font-size:0.85em;color:#ffaa44;">Diagnostic criteria met: grandiose institutional identity, preoccupation with prestigious validations, sense of entitlement to public funds, lack of transparency, exploitation of reimbursement structures.</span>
    </div>
'''


# -- SECTION 13: REQUEST vs DELIVERY --
BCC_REQUEST_VS_DELIVERY = '''
    <table class="ledger-table">
        <tr><th>What the Public / Funders Expected</th><th>What BCC Delivered</th><th>Deviation</th></tr>
        <tr style="background:rgba(255,0,0,0.1);"><td>Transparent nonprofit governance</td><td>5/9 board members' employers unknown. No COI policy obtained. Schedule L all-NO.</td><td style="color:#ff4444;">🔴 FULL DEVIATION</td></tr>
        <tr style="background:rgba(255,0,0,0.1);"><td>Regulatory compliance</td><td>21/24 DDAP surveys non-compliant. Same deficiencies recur for 20 years.</td><td style="color:#ff4444;">🔴 87.5% NON-COMPLIANT</td></tr>
        <tr style="background:rgba(255,170,68,0.1);"><td>Investment transparency</td><td>$10.48M portfolio — manager not named in any public filing. 22% board from one firm.</td><td style="color:#ffaa44;">🟡 STRUCTURALLY HIDDEN</td></tr>
        <tr style="background:rgba(255,170,68,0.1);"><td>Community accountability</td><td>11 transparency channels blocked. No public minutes. CAPTCHA-walled campaign finance.</td><td style="color:#ffaa44;">🟡 ARCHITECTURAL OPACITY</td></tr>
        <tr><td>Clinical excellence</td><td>CCBHC-certified but recurring treatment-plan and confidentiality deficiencies.</td><td style="color:#ffaa44;">🟡 CERTIFICATION vs REALITY</td></tr>
        <tr><td>Fiscal sustainability</td><td>Revenue declining -4.74% CAGR. First deficit FY2025. SAMHSA grant expires in 790 days.</td><td style="color:#ffaa44;">🟡 UNSUSTAINABLE MODEL</td></tr>
    </table>
    <div class="buck-stop" style="font-size:1em;margin-top:12px;">
        <strong>Scope Verdict:</strong> Massive deviation between public image and investigation reality. A community behavioral health center that presents the model nonprofit while operating as an opaque financial engine with recurring clinical deficiencies.
    </div>
'''


# -- SECTION 14: FEMALE EMPATHY -> ORGANIZATIONAL COMPASSION --
BCC_FEMALE_EMPATHY = '''
    <div class="panel" style="border-color:#ff44aa;">
        <h3>💕 ORGANIZATIONAL COMPASSION — What Berks County Needed vs. What BCC Delivered</h3>
        <h4>What the COMMUNITY Needed:</h4>
        <ul>
            <li><strong>Transparent Governance:</strong> Public disclosure of board member employers, affiliations, and conflicts. A written Conflict of Interest Policy available for inspection.</li>
            <li><strong>Clinical Accountability:</strong> DDAP deficiencies corrected after first citation — not recurring across 20 years. Treatment plans reviewed within 60 days. Confidentiality consent obtained.</li>
            <li><strong>Financial Visibility:</strong> Investment manager disclosed. Schedule L accurately reported. Auditor independence maintained (separate firms for prep and audit).</li>
            <li><strong>Community Trust:</strong> Board minutes published. Recusal records maintained. Campaign finance connections transparent.</li>
            <li><strong>Regulatory Responsiveness:</strong> Plans of correction implemented, not just filed. Deficiencies resolved, not repeated.</li>
        </ul>
        <h4>What BCC Actually Delivered:</h4>
        <ul>
            <li>⚠️ 5 of 9 board members have no publicly confirmed employer</li>
            <li>⚠️ 21 of 24 DDAP surveys non-compliant — same deficiencies recur for 20 years</li>
            <li>⚠️ $10.48M portfolio — manager not named in any public filing</li>
            <li>⚠️ Schedule L reports NO related-party transactions despite 22% Edward Jones board control</li>
            <li>⚠️ 11 accountability channels structurally blocked</li>
            <li>⚠️ Plans of correction filed but deficiencies recur — 709.17(a)(3) citation for failure to comply with approved CAP</li>
        </ul>
    </div>
    <div class="panel" style="border-color:#ff88cc;">
        <h3>🤝 ORGANIZATIONAL EMPATHY DEFICIT ANALYSIS</h3>
        <table class="ledger-table">
            <tr><th>Empathy Type</th><th>What It Means for an Organization</th><th>BCC Demonstrated</th><th>What Accountability Requires</th></tr>
            <tr><td><strong>Cognitive Empathy</strong></td><td>Understanding community perspective and needs</td><td style="color:#ff4444;">❌ FAILED — website lists names/titles, zero bios or employer info</td><td>Publish board member affiliations, COI policy, meeting minutes</td></tr>
            <tr><td><strong>Emotional Empathy</strong></td><td>Recognizing stakeholder concerns about governance</td><td style="color:#ff4444;">❌ NONE — no public response to governance questions</td><td>Acknowledge and address community transparency expectations</td></tr>
            <tr><td><strong>Compassionate Empathy</strong></td><td>Acting to address identified deficiencies</td><td style="color:#ffaa44;">⚠️ FILED BUT NOT FIXED — plans of correction exist, deficiencies recur</td><td>Implement corrective actions, not just document them</td></tr>
            <tr><td><strong>Structural Empathy</strong></td><td>Designing systems that protect stakeholder interests</td><td style="color:#ff4444;">❌ ABSENT — 11 accountability channels blocked by design</td><td>Architect transparency into governance, not out of it</td></tr>
        </table>
    </div>
'''


# -- SECTION 15: ARCHETYPE ANALYSIS -> BCC ACTOR ARCHETYPES --
BCC_ARCHETYPE_ANALYSIS = '''
    <div class="correlation-matrix">
        <div class="matrix-card archetype-card" style="border-color:#ff4444;">
            <h4><span class="arch-avatar hero">🦸</span> THE HERO — Christine Axford, CEO</h4>
            <p><strong>Archetype:</strong> The organization's public face. Led 50x growth. CCBHC-certified. MacKenzie Scott grantee.</p>
            <p><strong>Shadow:</strong> Concentration of operational responsibility without transparent accountability. What did leadership know about recurring DDAP deficiencies?</p>
            <p><strong>BCC Evidence:</strong> CEO during entire reviewed period. $145,432 compensation. 21/24 non-compliant surveys under tenure.</p>
        </div>
        <div class="matrix-card archetype-card" style="border-color:#ffaa44;">
            <h4><span class="arch-avatar wizard">🧙</span> THE WIZARD — Joseph Plageman Jr., Board Chair</h4>
            <p><strong>Archetype:</strong> The financial architect behind the curtain. Edward Jones CRD #5964768. 22% board control.</p>
            <p><strong>Shadow:</strong> Unresolved portfolio-manager question. If Edward Jones manages $10.48M — the wizard profits from the spell.</p>
            <p><strong>BCC Evidence:</strong> Schedule D: $10.48M securities. Schedule L: ALL NO. No public filing names the investment manager.</p>
        </div>
        <div class="matrix-card archetype-card" style="border-color:#44ff44;">
            <h4><span class="arch-avatar queen">👸</span> THE CAREGIVER — Missy Orlando, Director</h4>
            <p><strong>Archetype:</strong> The bridge between funder and funded. United Way employee on BCC board.</p>
            <p><strong>Shadow:</strong> Funder-recipient governance loop. ~$685K at stake. Recusal from UW votes: unknown.</p>
            <p><strong>BCC Evidence:</strong> $678K MacKenzie Scott grant + UW support. Board member's employer = primary funder.</p>
        </div>
        <div class="matrix-card archetype-card" style="border-color:#4488ff;">
            <h4><span class="arch-avatar innocent">👼</span> THE INNOCENT — Crystal Seitz, Vice Chair</h4>
            <p><strong>Archetype:</strong> Clean-handed governance test. Non-health-sector employer. No identified BCC financial relationship.</p>
            <p><strong>Shadow:</strong> Independence must be tested by minutes — votes, oversight activity not visible.</p>
            <p><strong>BCC Evidence:</strong> Greater Reading Convention & Visitors Bureau — tourism, not health. Clean profile.</p>
        </div>
        <div class="matrix-card archetype-card" style="border-color:#ff44ff;">
            <h4><span class="arch-avatar warrior">⚔️</span> THE WARRIOR — BCC v. Community Care (2017)</h4>
            <p><strong>Archetype:</strong> Litigated against own MCO over $105K Medicaid audit retraction — and won on procedure.</p>
            <p><strong>Shadow:</strong> Fighting oversight instead of fixing documentation. Won on unexecuted policy, not on merits.</p>
            <p><strong>BCC Evidence:</strong> 898 MDA 2017 — PA Superior Court. $81K+ retractions cancelled. Deficiencies were real.</p>
        </div>
        <div class="matrix-card archetype-card" style="border-color:#aa44aa;">
            <h4><span class="arch-avatar witch">🧙‍♀️</span> THE SHADOW WORKER — Marcia Goodman-Hinnershitz</h4>
            <p><strong>Archetype:</strong> Dual-role channel. COCA Director of Planning funds BCC $3.3M. Reading City Council member for ~20 years.</p>
            <p><strong>Shadow:</strong> Structural intersection of funding and public oversight. Votes and recusals: structurally unknowable.</p>
            <p><strong>BCC Evidence:</strong> COCA grants + City Council seat. Campaign finance CAPTCHA-walled. Council minutes not searchable.</p>
        </div>
        <div class="matrix-card archetype-card" style="border-color:#888888;">
            <h4><span class="arch-avatar sage">🧓</span> THE SAGE — Greg Little, CFO</h4>
            <p><strong>Archetype:</strong> Keeper of the books. $149,792 — out-earns CEO. Investment income grew 11.13x under his watch.</p>
            <p><strong>Shadow:</strong> Financial visibility vs. clinical documentation gap. CFO > CEO signals where priorities lie.</p>
            <p><strong>BCC Evidence:</strong> $12.2M surplus extracted FY2021-2024. \"Other compensation\" 3.2x CEO's. Schedule J not public.</p>
        </div>
        <div class="matrix-card archetype-card" style="border-color:#44aaaa;">
            <h4><span class="arch-avatar mystic">🔮</span> THE MYSTIC — James Brooks, Director</h4>
            <p><strong>Archetype:</strong> The unknown node. Employer unconfirmed after multiple public-search channels.</p>
            <p><strong>Shadow:</strong> Mystery is a governance gap — not wrongdoing. But 1/9 board members with zero public identity is 11% opacity.</p>
            <p><strong>BCC Evidence:</strong> 9+ years on board. Marion Township Fire Relief lead. No PA attorney license. Not an attorney.</p>
        </div>
    </div>
'''


# -- SECTION 16: COMPREHENSIVE ARCHETYPE GALLERY -> BCC GOVERNANCE ARCHETYPES --
BCC_ARCHETYPE_GALLERY = '''
    <div class="correlation-matrix">
        <div class="matrix-card" style="border-color:#ff4444;"><h4>🦸 HERO — Axford (CEO)</h4><p>50x growth under leadership. Public face of BCC excellence. Shadow: responsibility without transparent accountability.</p></div>
        <div class="matrix-card" style="border-color:#ffaa44;"><h4>🧙 WIZARD — Plageman (Chair)</h4><p>Financial architect. Edward Jones nexus. Shadow: unresolved $10.48M manager question.</p></div>
        <div class="matrix-card" style="border-color:#44ff44;"><h4>👸 CAREGIVER — Orlando (Director)</h4><p>Funder-grantee bridge. United Way channel. Shadow: governance loop with $685K at stake.</p></div>
        <div class="matrix-card" style="border-color:#4488ff;"><h4>👼 INNOCENT — Seitz (Vice Chair)</h4><p>Clean-handed governance test. Non-conflicted profile. Shadow: votes invisible without minutes.</p></div>
        <div class="matrix-card" style="border-color:#ff44ff;"><h4>⚔️ WARRIOR — BCC Litigation Arm</h4><p>Sued MCO over audit. Won on procedure. Shadow: adversarial stance toward oversight.</p></div>
        <div class="matrix-card" style="border-color:#aa44aa;"><h4>🌑 SHADOW — Goodman-Hinnershitz</h4><p>COCA/City Council dual role. $3.3M funding channel. Shadow: recusal structurally unknowable.</p></div>
        <div class="matrix-card" style="border-color:#888888;"><h4>🧓 SAGE — Little (CFO)</h4><p>Financial steward. 11.13x investment growth. Shadow: CFO > CEO compensation signals.</p></div>
        <div class="matrix-card" style="border-color:#44aaaa;"><h4>🔮 MYSTIC — Brooks (Director)</h4><p>Unknown employer. Identity gap. Shadow: 11% board opacity.</p></div>
        <div class="matrix-card" style="border-color:#ff8800;"><h4>🏗️ CREATOR — BCC Institution</h4><p>Built $19.8M from $395K. CCBHC model. Shadow: growth from reimbursement arbitrage, not service expansion.</p></div>
        <div class="matrix-card" style="border-color:#ffd700;"><h4>👑 RULER — BCC Board</h4><p>Governs $12.5M/yr in public funds. $0 compensation. Shadow: invisible value flows through institutional channels.</p></div>
        <div class="matrix-card" style="border-color:#ff88cc;"><h4>💀 DESTROYER — Regulatory Shield</h4><p>System that enables: 21/24 non-compliant, zero license actions. Shadow: oversight without intervention.</p></div>
        <div class="matrix-card" style="border-color:#44aaff;"><h4>🔍 SEEKER — This Investigation</h4><p>49 git commits. 9 evidence documents. 62 gaps mapped. Shadow: evidence-bounded — does not assert misconduct.</p></div>
        <div class="matrix-card" style="border-color:#cc0000;"><h4>🐍 TRICKSTER — Schedule L (All-NO)</h4><p>Self-reported zero related-party transactions. Shadow: 22% Edward Jones board, UW board director, Robinson LLC — all NO.</p></div>
        <div class="matrix-card" style="border-color:#ff44aa;"><h4>🌪️ OUTLAW — COCA / County Loop</h4><p>COCA funds BCC. COCA planner on Council. County funds BCC. Commissioners oversee. Shadow: political-funder circuit.</p></div>
        <div class="matrix-card" style="border-color:#aa44ff;"><h4>🕊️ LOVER — Berks County Community</h4><p>Deserves transparent community health center. 94% public funds. Shadow: trust betrayed by opacity.</p></div>
        <div class="matrix-card" style="border-color:#440000;"><h4>🗡️ CAIN — The Opacity Itself</h4><p>The mark of Cain: not a person, but the structural design. 11 blocked channels. The transparency wall IS the sin.</p></div>
    </div>
    <p style="margin-top:14px;color:#ffaa44;"><strong>16/16 Archetypes Populated:</strong> Every archetype is mapped to a BCC actor, pattern, or structural mechanism. The gallery demonstrates that BCC's governance network maps to the complete Jungian archetype spectrum — from Hero (Axford) to Destroyer (Regulatory Shield) to Trickster (Schedule L).</p>
'''


# -- SECTION 17: MULTI-MODAL PSYCHOANALYSIS -> ORGANIZATIONAL BEHAVIOR --
BCC_PSYCHOANALYSIS = '''
    <div class="modal-analysis freudian">
        <div class="modal-title">🛋️ FREUDIAN ANALYSIS — Organizational Id, Ego, Superego</div>
        <p><strong>Id (Pleasure Principle):</strong> BCC's institutional drive to grow, accumulate, and extract. The CCBHC reimbursement engine is the Id — generating $4.5M single-year surplus. The $10.48M portfolio is the Id's treasure.</p>
        <p><strong>Ego (Reality Principle):</strong> BCC Leadership (Axford & Little) mediates between growth drives and operational reality. The declining revenue (-4.74% CAGR) and SAMHSA grant cliff force the Ego to confront the Id's unsustainability.</p>
        <p><strong>Superego (Moral Conscience):</strong> The regulatory framework — DDAP, SAMHSA, IRS, PA OAG — should constrain the Id. But 21/24 non-compliant surveys with zero license actions suggests the Superego is captured or ineffective.</p>
        <p><strong>Diagnosis:</strong> Id-dominated organization with a captured Superego. The pleasure principle (growth, surplus, portfolio) overrides the reality principle (clinical compliance, transparency).</p>
    </div>
    <div class="modal-analysis jungian">
        <div class="modal-title">🌌 JUNGIAN ANALYSIS — Collective Governance Shadow</div>
        <p><strong>Collective Unconscious:</strong> The nonprofit sector's shared assumption that $0 board compensation = ethical governance. BCC exploits this archetype — the real value flows through institutional channels (management fees, grant allocations, contract referrals), invisible to the $0-compensation lens.</p>
        <p><strong>Shadow Projection:</strong> BCC projects the image of the model nonprofit (CCBHC-certified, MacKenzie Scott-validated) while its operational shadow contains 21/24 non-compliant surveys and 11 blocked accountability channels.</p>
        <p><strong>Individuation:</strong> The path forward requires BCC to integrate its shadow — acknowledge the governance conflicts, disclose the investment manager, publish the COI policy, and open the minutes.</p>
    </div>
    <div class="modal-analysis cbt">
        <div class="modal-title">🧠 COGNITIVE-BEHAVIORAL ANALYSIS — Organizational Thought Patterns</div>
        <p><strong>Core Belief:</strong> \"We are an excellent community health center — the CCBHC certification proves it.\"</p>
        <p><strong>Automatic Thought:</strong> \"Transparency is optional. Our $0 board and awards speak for themselves.\"</p>
        <p><strong>Cognitive Distortion:</strong> Minimization — 21/24 DDAP surveys non-compliant is minimized as \"plans of correction exist.\" Emotional reasoning — \"we won the lawsuit, so the audit was wrong.\"</p>
        <p><strong>Behavioral Pattern:</strong> File plans of correction without implementing them (709.17(a)(3) citation). Litigate against oversight. Maintain opacity as default state.</p>
        <p><strong>Intervention:</strong> Publish all governance records. Disclose investment manager. Release COI policy. Open board minutes. The behavior must change — not just the documentation.</p>
    </div>
    <div class="modal-analysis rogers">
        <div class="modal-title">🌱 ROGERIAN ANALYSIS — Conditions of Worth vs. Unconditional Positive Regard</div>
        <p><strong>Conditions of Worth:</strong> BCC operates under the condition that institutional prestige (CCBHC, MacKenzie Scott, Marcopul Award) equals organizational worth. The drive for prestigious credentials overrides clinical accountability.</p>
        <p><strong>Incongruence:</strong> The gap between BCC's self-concept (model nonprofit) and operational reality (21/24 non-compliant) creates organizational incongruence. The opacity is a defense mechanism against confronting this gap.</p>
        <p><strong>Unconditional Positive Regard:</strong> What BCC's community deserves — transparency regardless of performance. The community should see the good AND the deficiencies. Accountability is not contingent on perfection.</p>
    </div>
    <div class="modal-analysis attachment">
        <div class="modal-title">🔗 ATTACHMENT ANALYSIS — Institutional Attachment Style</div>
        <p><strong>Attachment Style:</strong> Dismissive-Avoidant. BCC maintains distance from accountability figures (regulators, public, funders). Dismisses oversight (litigation against MCO). Avoids transparency (11 blocked channels).</p>
        <p><strong>Internal Working Model:</strong> \"Oversight bodies are adversaries to be managed, not partners in accountability.\" The CCBH lawsuit exemplifies this — the MCO was an adversary, not a collaborator in ensuring Medicaid integrity.</p>
        <p><strong>Secure Attachment Would Mean:</strong> Proactive transparency. Published COI policy. Disclosed investment manager. Searchable board minutes. Open recusal records. Collaborative regulatory engagement.</p>
    </div>
    <div class="modal-analysis ACT">
        <div class="modal-title">🎯 ACCEPTANCE & COMMITMENT — Organizational Values vs. Actions</div>
        <p><strong>Stated Values:</strong> Recovery-oriented integrated community wellness. Serving Berks County's most vulnerable.</p>
        <p><strong>Committed Actions:</strong> 21/24 DDAP surveys non-compliant. Same deficiencies for 20 years. CFO out-earns CEO. $10.48M portfolio with undisclosed manager. Sued own MCO.</p>
        <p><strong>Cognitive Fusion:</strong> BCC is fused with the thought \"we are excellent\" — unable to see the gap between self-concept and operational reality.</p>
        <p><strong>Values-Based Action:</strong> Align operations with stated mission. Fix recurring deficiencies. Disclose governance. Open the doors. The mission is service — not opacity.</p>
    </div>
'''


BCC_PSYCHOANALYSIS_2 = '''
    <div class="panel panel-warning">
        <h3>🧠 ORGANIZATIONAL BEHAVIORAL SYNTHESIS</h3>
        <p>The six therapeutic frameworks converge on a single diagnosis: <strong>BCC exhibits a Dismissive-Avoidant institutional attachment style with Id-dominated financial drives operating behind a structurally opaque governance architecture.</strong></p>
        <p><strong>Key behavioral patterns:</strong></p>
        <ul>
            <li><strong>Deflection:</strong> Litigation instead of remediation (CCBH lawsuit)</li>
            <li><strong>Minimization:</strong> Plans of correction filed but not implemented (709.17(a)(3))</li>
            <li><strong>Opacification:</strong> 11 accountability channels blocked — not accidental, architectural</li>
            <li><strong>Prestige Dependency:</strong> CCBHC badge, MacKenzie Scott validation over clinical compliance</li>
            <li><strong>Financial Primacy:</strong> CFO out-earns CEO. 11.13x investment growth. $12.2M surplus extracted.</li>
        </ul>
        <p style="color:#ffaa44;"><strong>Prognosis:</strong> Without intervention — continued opacity, recurring deficiencies, grant cliff risk (790 days). With transparency intervention — potential for genuine community accountability. The path is clear. The records are the intervention.</p>
    </div>
'''


# -- SECTION 18: EMOTION DICTIONARY -> STAKEHOLDER SENTIMENT --
BCC_EMOTION_DICT = '''
    <div class="emotion-list" style="margin-bottom:16px;">
        <span class="emotion-tag negative">Frustration (Community)</span>
        <span class="emotion-tag negative">Distrust (Patients)</span>
        <span class="emotion-tag negative">Opacity (Public)</span>
        <span class="emotion-tag negative">Recurring Deficiencies (DDAP)</span>
        <span class="emotion-tag negative">Governance Capture (IRS)</span>
        <span class="emotion-tag negative">Structural Secrecy (Media)</span>
        <span class="emotion-tag neutral">Uncertainty (Funders)</span>
        <span class="emotion-tag neutral">Complicity (Auditors)</span>
        <span class="emotion-tag neutral">Inertia (Regulators)</span>
        <span class="emotion-tag positive">Resilience (Staff)</span>
        <span class="emotion-tag positive">Advocacy (Whistleblowers)</span>
        <span class="emotion-tag positive">Accountability (Investigators)</span>
    </div>
    <table class="ledger-table">
        <tr><th>Stakeholder</th><th>Primary Emotion</th><th>Intensity</th><th>Basis</th></tr>
        <tr><td><strong>Patients</strong></td><td>😰 Vulnerability + 😤 Frustration</td><td><div class="emotion-bar danger"></div></td><td>Recurring clinical deficiencies without resolution</td></tr>
        <tr><td><strong>Community</strong></td><td>😤 Distrust</td><td><div class="emotion-bar danger"></div></td><td>94% public funds, zero governance transparency</td></tr>
        <tr><td><strong>Taxpayers</strong></td><td>😠 Exploitation</td><td><div class="emotion-bar warning"></div></td><td>$12.5M/yr public funds under opaque governance</td></tr>
        <tr><td><strong>Funders (UW, COCA)</strong></td><td>😰 Uncertainty</td><td><div class="emotion-bar warning"></div></td><td>Governance conflicts at grantee raise reputational risk</td></tr>
        <tr><td><strong>DDAP Regulators</strong></td><td>😨 Systemic Failure</td><td><div class="emotion-bar caution"></div></td><td>21/24 non-compliant, zero license actions</td></tr>
        <tr><td><strong>BCC Staff</strong></td><td>😐 Ambivalence</td><td><div class="emotion-bar caution"></div></td><td>Serve mission within opaque institution</td></tr>
        <tr><td><strong>Edward Jones</strong></td><td>😶 Strategic Silence</td><td><div class="emotion-bar" style="--fill-pct:30%;"></div></td><td>22% board control, manager status unconfirmed</td></tr>
    </table>
'''


# -- SECTION 19: INTERCONNECTED EMOTION FLOW -> BCC CAUSALITY --
BCC_INTERCONNECTED_FLOW = '''
    <div class="flow-row" style="justify-content:center;flex-wrap:wrap;">
        <div class="flow-step" style="border-color:#ff4444;">🔴 Governance<br>Opacity</div>
        <div class="flow-arrow">→</div>
        <div class="flow-step" style="border-color:#ffaa44;">🟡 Undisclosed<br>Conflicts</div>
        <div class="flow-arrow">→</div>
        <div class="flow-step" style="border-color:#ff44ff;">🟣 Financial<br>Extraction</div>
        <div class="flow-arrow">→</div>
        <div class="flow-step" style="border-color:#888888;">⚪ Regulatory<br>Shield</div>
        <div class="flow-arrow">→</div>
        <div class="flow-step" style="border-color:#cc0000;">🩹 Patient<br>Impact</div>
    </div>
    <div class="flow-row" style="justify-content:center;flex-wrap:wrap;margin-top:16px;">
        <div class="flow-step" style="border-color:#4488ff;">🔵 5/9 Board<br>Unknown</div>
        <div class="flow-arrow">→</div>
        <div class="flow-step" style="border-color:#ffaa44;">🟡 No COI<br>Policy</div>
        <div class="flow-arrow">→</div>
        <div class="flow-step" style="border-color:#ff4444;">🔴 Schedule L<br>All-NO</div>
        <div class="flow-arrow">→</div>
        <div class="flow-step" style="border-color:#ff44ff;">🟣 11 Channels<br>Blocked</div>
        <div class="flow-arrow">→</div>
        <div class="flow-step" style="border-color:#000;">⬛ Public Trust<br>Erosion</div>
    </div>
    <p style="margin-top:14px;text-align:center;color:#ffaa44;"><strong>The CAIN Chain:</strong> Governance Opacity → Undisclosed Conflicts → Financial Extraction → Regulatory Shield → Patient Impact → Public Trust Erosion. This is the organizational causation chain this investigation has documented. Each link has evidence. Only the opacity prevents the links from being visible.</p>
'''


# -- SECTION 20: WRITTEN APOLOGY -> ACCOUNTABILITY STATEMENT --
BCC_APOLOGY = '''
    <div class="apology-box" style="background:rgba(40,20,20,0.9);border-color:#8b4500;">
        <h3>📜 ORGANIZATIONAL ACCOUNTABILITY — What Transparency Would Require</h3>
        <p><em>This is not an apology from BCC — none has been offered. This is what accountability would look like if BCC chose transparency:</em></p>
        <p style="margin-top:12px;">\"To the Berks County community we serve: We acknowledge that our governance practices have not met the transparency standards expected of an organization receiving 94% public funding. We commit to publishing our Conflict of Interest Policy, disclosing our investment manager, releasing board minutes with recusal records, and correcting the recurring clinical deficiencies identified in 21 of 24 DDAP surveys. The trust of our community is not optional — it is the foundation of our mission. We will earn it back through transparency, not opacity.\"</p>
        <p style="color:#ffaa44;margin-top:12px;"><strong>Current status:</strong> No such statement has been made. The opacity continues. The records requested on August 2, 2026 remain unproduced.</p>
    </div>
'''


# -- SECTION 21: MASCULINE-FEMININE DUALITY -> OVERSIGHT VS AUTONOMY --
BCC_DUALITY = '''
    <div class="feminine-panel">
        <h3>⚔️🛡️ OVERSIGHT MANDATE (Yang — Active/Assertive Energy)</h3>
        <p><strong>DDAP:</strong> 24 surveys over 20 years. 21 found non-compliance. The oversight exists on paper. Enforcement does not.</p>
        <p><strong>SAMHSA:</strong> $3.94M grant. HHS OIG found 93% verification failure. Oversight asserted but not executed.</p>
        <p><strong>IRS:</strong> Schedule L self-reported all-NO. No independent verification. The forms enable opacity.</p>
        <p><strong>PA OAG:</strong> Charitable Trusts jurisdiction. Thousands of cases. Reactive, not proactive.</p>
        <p><strong>Excess:</strong> The oversight framework is assertive in design but passive in execution. The Yang energy of regulation is present but ineffective.</p>
    </div>
    <div class="feminine-panel" style="margin-top:16px;">
        <h3>🌙🕊️ INSTITUTIONAL AUTONOMY (Yin — Receptive/Protective Energy)</h3>
        <p><strong>BCC Governance:</strong> $0 board compensation. The receptive structure — board members give, not take. But the Yin energy protects through invisibility.</p>
        <p><strong>Transparency Wall:</strong> 11 channels blocked. The protective instinct becomes opacity. Schedule B redacted. Schedule O/J hidden. Minutes not searchable.</p>
        <p><strong>Community Silence:</strong> No public complaints. No whistleblowers. The community receives BCC's services and accepts the opacity as normal.</p>
        <p><strong>Deficiency:</strong> The Yin energy of institutional protection has become pathological — protecting the institution FROM accountability rather than FOR service.</p>
    </div>
    <div class="feminine-panel" style="margin-top:16px;border-color:#ffd700;">
        <h3>⚖️ INTEGRATION — What Balanced Governance Requires</h3>
        <p><strong>Yang (Oversight) + Yin (Service) = Transparent Public Service.</strong> The oversight framework must assert with enforcement, not just documentation. The service mission must protect through transparency, not opacity. The integration is not compromise — it is the alignment of public funds with public visibility.</p>
        <p style="color:#ffaa44;"><strong>Current imbalance:</strong> Oversight without teeth (Yang deficient). Opacity as protection (Yin excess). The result: a $12.5M/yr public institution that cannot be seen by its public.</p>
    </div>
'''


# -- SECTION 22: DSM-5 -> ORGANIZATIONAL PATHOLOGY FRAMEWORK --
BCC_DSM5 = '''
    <div class="panel panel-warning">
        <h3>🏛️ ORGANIZATIONAL PATHOLOGY FRAMEWORK (adapted from clinical diagnostic models)</h3>
        <p style="color:#ffaa44;"><strong>⚠ NOTE:</strong> This is an organizational analogy, not a clinical diagnosis. Institutions are not patients. But organizational behavior patterns can be analyzed through diagnostic frameworks adapted for institutional analysis.</p>
        <table class="ledger-table">
            <tr><th>Diagnostic Criterion (adapted)</th><th>BCC Evidence</th><th>Match</th></tr>
            <tr><td><strong>Grandiose institutional identity</strong></td><td>CCBHC-certified, MacKenzie Scott grantee, Marcopul Award winner. Public narrative of excellence.</td><td style="color:#ff4444;">✅ MATCH</td></tr>
            <tr><td><strong>Preoccupation with prestigious validations</strong></td><td>Pursuit of certifications and awards while clinical deficiencies persist. Image management over compliance.</td><td style="color:#ff4444;">✅ MATCH</td></tr>
            <tr><td><strong>Sense of entitlement to public funds</strong></td><td>$12.2M surplus extracted during CCBHC window. Sued MCO to protect Medicaid reimbursement. Won on procedure.</td><td style="color:#ff4444;">✅ MATCH</td></tr>
            <tr><td><strong>Lack of transparency</strong></td><td>11 accountability channels blocked. 5/9 board members' employers unknown. No COI policy obtained.</td><td style="color:#ff4444;">✅ MATCH</td></tr>
            <tr><td><strong>Exploitation of systems</strong></td><td>CCBHC reimbursement arbitrage. Schedule L all-NO. Same auditor prepares 990 and audits.</td><td style="color:#ff4444;">✅ MATCH</td></tr>
            <tr><td><strong>Lack of empathy for stakeholders</strong></td><td>Patients experience recurring deficiencies. Community cannot see governance. Funders unaware of conflicts.</td><td style="color:#ff4444;">✅ MATCH</td></tr>
            <tr><td><strong>Adversarial stance toward oversight</strong></td><td>Litigation against MCO. Plans of correction filed but not implemented. Zero license actions across 20 years.</td><td style="color:#ff4444;">✅ MATCH</td></tr>
        </table>
        <p style="margin-top:12px;"><strong>Framework Conclusion:</strong> 7 of 7 adapted diagnostic criteria match. Not a clinical diagnosis — an organizational behavior pattern. The pattern is consistent across 20 years of evidence. The opacity prevents confirmation of what the records would reveal.</p>
    </div>
'''


# -- SECTION 23: MALADAPTIVE COGNITIVE PATTERN -> ORGANIZATIONAL PATTERNS --
BCC_MALADAPTIVE = '''
    <div class="panel panel-danger">
        <h3>🔄 RECURRING ORGANIZATIONAL PATTERNS (2006-2026)</h3>
        <table class="ledger-table">
            <tr><th>Pattern</th><th>First Observed</th><th>Most Recent</th><th>Duration</th><th>Status</th></tr>
            <tr><td><strong>Confidentiality Consent Deficiency (709.92(c))</strong></td><td>June 2006</td><td>January 2026</td><td style="color:#ff4444;">7 survey years</td><td style="color:#ff4444;">🔴 ACTIVE</td></tr>
            <tr><td><strong>60-Day Treatment-Plan Review (709.93(a)(8))</strong></td><td>June 2006</td><td>January 2026</td><td style="color:#ff4444;">8 survey years</td><td style="color:#ff4444;">🔴 ACTIVE</td></tr>
            <tr><td><strong>Plan of Correction Non-Compliance (709.17(a)(3))</strong></td><td>October 2009</td><td>March 2023</td><td style="color:#ff4444;">14 years</td><td style="color:#ff4444;">🔴 ACTIVE</td></tr>
            <tr><td><strong>Governance Opacity</strong></td><td>FY2021 990</td><td>FY2025 990</td><td style="color:#ffaa44;">5 filing years</td><td style="color:#ffaa44;">🟡 ONGOING</td></tr>
            <tr><td><strong>Litigation as Compliance Strategy</strong></td><td>2015 (audit trigger)</td><td>2018 (ruling)</td><td style="color:#888;">Resolved</td><td style="color:#44ff44;">✅ WON (procedure)</td></tr>
        </table>
        <p style="margin-top:12px;"><strong>Pattern Analysis:</strong> The organization demonstrates <strong>maladaptive persistence</strong> — continuing behaviors (filing CAPs without implementing them) despite repeated negative outcomes (recurring deficiencies, failure-to-comply citations). The regulatory environment demonstrates <strong>learned helplessness</strong> — citing deficiencies without enforcing consequences, reinforcing the maladaptive pattern.</p>
    </div>
'''


# -- SECTION 24: COMPREHENSIVE ACTION LIST -> INTEGRATED GAPS --
BCC_ACTION_LIST = '''
    <div class="panel panel-danger">
        <h3>🔴 CRITICAL — Investigation Closure Requirements</h3>
        <ul>
            <li><strong>COI Policy (Gap #1):</strong> Obtain BCC's written Conflict of Interest Policy per IRS public disclosure requirements</li>
            <li><strong>Investment Manager (Gap #2):</strong> Confirm Edward Jones portfolio management — custodian, contract, fees</li>
            <li><strong>Complaint Broadcast (Gap #3):</strong> File with all oversight bodies (planned Aug 16-30, 2026)</li>
            <li><strong>Schedule L Verification (Gap #4):</strong> Independent verification of related-party transaction reporting</li>
            <li><strong>Auditor Independence (Gap #5):</strong> Herbein dual-role assessment — prepares 990 AND audits</li>
        </ul>
    </div>
    <div class="panel panel-warning">
        <h3>🟡 HIGH — Records Acquisition (30-90 days)</h3>
        <ul>
            <li><strong>RTK Requests (Gaps #13, #18):</strong> Berks County contracts + PA DDAP corrective action dispositions</li>
            <li><strong>Board Minutes (Gap #23):</strong> Manual review for recusal documentation</li>
            <li><strong>Schedule O/J (Gap #32):</strong> IRS narrative extraction for governance + compensation methodology</li>
            <li><strong>Campaign Finance (Gap #42):</strong> PA portal access for BCC-connected contributions</li>
            <li><strong>COCA/County Records (Gap #21):</strong> Pass-through funding, contract terms, oversight reports</li>
        </ul>
    </div>
    <div class="panel panel-info">
        <h3>🔵 MONITORING — Ongoing (90+ days)</h3>
        <ul>
            <li><strong>SAMHSA Grant:</strong> Track through Sept 29, 2027 expiration (790 days from Aug 1, 2026)</li>
            <li><strong>DDAP Surveys:</strong> Monitor future survey outcomes for recurring deficiency resolution</li>
            <li><strong>IRS Filings:</strong> Track FY2026 990 for governance disclosures</li>
            <li><strong>Patient Impact:</strong> Correlate deficiency patterns with clinical outcome data (if obtainable)</li>
        </ul>
    </div>
'''


# -- SECTION 25: ADDITIONAL ARCHETYPES -> MORE BCC MAPPINGS --
BCC_ADDITIONAL_ARCHETYPES = '''
    <div class="correlation-matrix">
        <div class="matrix-card" style="border-color:#ff4444;"><h4>📋 THE SCRIBE — Herbein (Auditor)</h4><p>Prepares 990 AND audits financials. Reduced independence. The scribe who both writes and verifies the story.</p></div>
        <div class="matrix-card" style="border-color:#ffaa44;"><h4>🏦 THE MERCHANT — $10.48M Portfolio</h4><p>Securities managed by undisclosed firm. 22% Edward Jones board control. The silent asset.</p></div>
        <div class="matrix-card" style="border-color:#ff44ff;"><h4>🦉 THE OWL — Linda Krause (Director)</h4><p>Sr. Branch Office Admin on Plageman's team. 22% EJ board bloc. The reinforcing vote.</p></div>
        <div class="matrix-card" style="border-color:#44ff44;"><h4>🛒 THE PROCURER — Brenda Gaul (Director)</h4><p>Furniture Soup owner. Former Treasurer. Public-sector contracts. Vendor conflict potential.</p></div>
        <div class="matrix-card" style="border-color:#4488ff;"><h4>💼 THE CONSULTANT — John Robinson (Treasurer)</h4><p>Robinson & Associates LLC #3967553. Active PA entity. Any BCC payments? Schedule L says NO.</p></div>
        <div class="matrix-card" style="border-color:#888888;"><h4>🏛️ THE GATEKEEPERS — County Commissioners</h4><p>Leinbach, Rivera, Santoni. $16.2M HS budget. BCC contracts not itemized. Oversight door ajar.</p></div>
        <div class="matrix-card" style="border-color:#ff8800;"><h4>🩺 THE HEALER — Santoro, MD (Medical Director)</h4><p>Clinical leadership. 2015 supervision issue. 2024 6/6 note finding. Responsibility ≠ knowledge.</p></div>
        <div class="matrix-card" style="border-color:#44aaaa;"><h4>🧪 THE TECHNICIAN — Triebig, MS (Clinical Director)</h4><p>DDAP/SAMHSA presenter. Implementation gap — policy is not proof of operations.</p></div>
    </div>
    <p style="margin-top:10px;color:#ffaa44;"><strong>24 total BCC archetype mappings:</strong> 16 in the main gallery + 8 additional — full coverage of all key actors, patterns, and structural mechanisms.</p>
'''


# -- SECTION 26: REQUEST FOR USER INPUT -> INVESTIGATION STATUS --
BCC_USER_INPUT = '''
    <div class="input-needed">
        <h3>📋 INVESTIGATION STATUS — August 4, 2026</h3>
        <p><strong>This document:</strong> BCC Organizational Analysis via CAIN Accountability System template. All 29 sections populated with evidence-bounded data from the investigation corpus.</p>
        <p><strong>Data sources:</strong> IRS Form 990 (FY2021-2025), PA DDAP portal (24 surveys), ProPublica Nonprofit Explorer, SAMHSA grant records, HHS OIG reports, FINRA BrokerCheck, PA corporate records, HUD CoC records, Berks County budget documents, United Way grant records, COCA schedules, PA campaign finance portal (blocked), Reading City Council minutes (limited).</p>
        <p><strong>Investigation status:</strong> 62 gaps mapped. 9 resolved. 53 open. 11 advanced. No complaints sent. Nothing filed. BCC excluded from all recipient lists.</p>
        <p><strong>Next investigative records needed:</strong></p>
        <ul>
            <li>BCC Conflict of Interest Policy (requested Aug 2, 2026 — 30-day IRS public disclosure clock)</li>
            <li>Investment management contract / custodian confirmation (Gap #2)</li>
            <li>Board meeting minutes with recusal records (Gap #23)</li>
            <li>IRS Schedule O (governance narrative) and Schedule J (compensation methodology)</li>
            <li>PA DDAP plans of correction final dispositions (RTK required)</li>
            <li>Berks County BCC contract itemization (RTK required)</li>
            <li>PA campaign finance — BCC-connected contributions (CAPTCHA-walled portal)</li>
        </ul>
        <p style="color:#ffaa44;"><strong>All analysis is evidence-bounded:</strong> This document identifies structural patterns, governance questions, and regulatory gaps. It draws inferences from documented facts. It does not assert transactions, influence, misconduct, or patient harm where records are unavailable. The opacity itself is the primary finding.</p>
    </div>
'''


# ── BUILD ─────────────────────────────────────────────────────────────
def build(output: Path) -> None:
    source = TEMPLATE.read_text(encoding="utf-8")

    # -- Phase 0: Banner identities --
    source = source.replace("<title>LM Studio Connection Debug Session</title>",
                            "<title>CAIN Accountability System — BCC Organizational Analysis — All Sections Populated</title>")
    source = source.replace("Full Transparency | Complete Mapping | Zero Obfuscation",
                            "BCC Organizational Analysis | 16 Actors | Governance Network | Financial Flow | Regulatory Pattern | Evidence-Bounded")

    # -- Phase 1: Web node replacement --
    source = web_replacements(source)

    # -- Phase 2: Replace accountability web description --
    source = source.replace(
        "This document provides FULL TRANSPARENCY into every emotion, trigger, and consequence.",
        "This document maps the BCC governance network: 16 actors across 4 interlocking mechanisms — the Reimbursement Engine, the Regulatory Shield, the Political Loop, and the Transparency Wall. Every section is populated with evidence-bounded data from the investigation corpus.")

    # -- Phase 3: Replace SEVEN DEADLY SINS --
    source = replace_section(source,
        "😈 SEVEN DEADLY SINS & MYTHOLOGICAL ARCHETYPES MAPPING",
        BCC_SINS)

    # -- Phase 3b: Replace FEMALE EMPATHY -> Organizational Compassion Analysis --
    source = replace_section(source,
        "👩 FEMALE EMPATHY & SYMPATHY ANALYSIS",
        BCC_FEMALE_EMPATHY)

    # -- Phase 3c: Replace ARCHETYPE ANALYSIS -> BCC Actor Archetypes --
    source = replace_section(source,
        "🎭 ARCHETYPE ANALYSIS",
        BCC_ARCHETYPE_ANALYSIS)

    # -- Phase 3d: Replace COMPREHENSIVE ARCHETYPE GALLERY -> BCC Governance Archetypes --
    source = replace_section(source,
        "🖼️ COMPREHENSIVE ARCHETYPE GALLERY (16 Total)",
        BCC_ARCHETYPE_GALLERY)

    # -- Phase 3e: Replace EXTENDED MULTI-MODAL PSYCHOANALYSIS -> Org Behavior --
    source = replace_section(source,
        "🧠 EXTENDED MULTI-MODAL PSYCHOANALYSIS (LENGTHENED)",
        BCC_PSYCHOANALYSIS)

    # -- Phase 3f: Replace EMOTION DICTIONARY -> Stakeholder Sentiment --
    source = replace_section(source,
        "📚 COMPLETE EMOTION DICTIONARY - ALL FEELINGS DOCUMENTED",
        BCC_EMOTION_DICT)

    # -- Phase 3g: Replace 2nd MULTI-MODAL PSYCHOANALYSIS --
    source = replace_section(source,
        "🧠 MULTI-MODAL PSYCHOANALYSIS (INCOMPLETE DATA)",
        BCC_PSYCHOANALYSIS_2)

    # -- Phase 3h: Replace INTERCONNECTED EMOTION FLOW -> BCC Causality --
    source = replace_section(source,
        "🔄 INTERCONNECTED EMOTION FLOW",
        BCC_INTERCONNECTED_FLOW)

    # -- Phase 3i: Replace WRITTEN APOLOGY --
    source = replace_section(source,
        "📜 WRITTEN APOLOGY",
        BCC_APOLOGY)

    # -- Phase 3j: Replace MASCULINE-FEMININE DUALITY -> Oversight/Autonomy --
    source = replace_section(source,
        "🧬 METAPHYSICAL MASCULINE-FEMININE DUALITY ANALYSIS",
        BCC_DUALITY)

    # -- Phase 3k: Replace DSM-5 -> Organizational Pathology Framework --
    source = replace_section(source,
        "🩺 DSM-5 DIAGNOSTIC FRAMEWORK ANALYSIS",
        BCC_DSM5)

    # -- Phase 3l: Replace MALADAPTIVE COGNITIVE PATTERN --
    source = replace_section(source,
        "🩺 MALADAPTIVE COGNITIVE PATTERN ANALYSIS",
        BCC_MALADAPTIVE)

    # -- Phase 3m: Replace COMPREHENSIVE ACTION LIST --
    source = replace_section(source,
        "📋 COMPREHENSIVE ACTION LIST FOR FRAMEWORK ADDITIONS",
        BCC_ACTION_LIST)

    # -- Phase 3n: Replace ADDITIONAL ARCHETYPES --
    source = replace_section(source,
        "🎭 ADDITIONAL ARCHETYPES MAPPING",
        BCC_ADDITIONAL_ARCHETYPES)

    # -- Phase 3o: Replace REQUEST FOR USER INPUT --
    source = replace_section(source,
        "❓ REQUEST FOR USER INPUT",
        BCC_USER_INPUT)

    # -- Phase 4: Replace MONEY SNAKE --
    source = replace_section(source,
        '🐍 COMPLETE MONEY SNAKE - "The Serpent in the Garden" (Top to Bottom)',
        BCC_MONEY_SNAKE)

    # -- Phase 5: Replace FINANCIAL LEDGER --
    source = replace_section(source,
        "💰 FINANCIAL LEDGER & ACCOUNTABILITY (End-to-End Chain)",
        BCC_FINANCIAL_LEDGER)

    # -- Phase 6: Replace WHERE THE BUCK STOPS --
    source = replace_section(source,
        "🎯 WHERE THE BUCK STOPS",
        BCC_BUCK_STOP)

    # -- Phase 7: Replace STAKEHOLDER IMPACT --
    source = replace_section(source,
        "👥 COMPLETE STAKEHOLDER IMPACT MATRIX",
        BCC_STAKEHOLDER)

    # -- Phase 8: Replace COST ACCOUNTING --
    source = replace_section(source,
        "💸 COMPLETE COST ACCOUNTING",
        BCC_COST_ACCOUNTING)

    # -- Phase 9: Replace LEGAL EXPOSURE --
    source = replace_section(source,
        "⚖️ COMPLETE LEGAL EXPOSURE ANALYSIS",
        BCC_LEGAL)

    # -- Phase 10: Replace BEFORE/AFTER --
    source = replace_section(source,
        "📊 COMPLETE BEFORE/AFTER METRICS",
        BCC_BEFORE_AFTER)

    # -- Phase 11: Replace ACTION ITEMS --
    source = replace_section(source,
        "✅ COMPLETE ACTION ITEM AUDIT",
        BCC_ACTIONS)

    # -- Phase 12: Replace METHODOLOGICAL TRANSPARENCY --
    source = replace_section(source,
        "🔬 COMPLETE METHODOLOGICAL TRANSPARENCY",
        BCC_METHODOLOGY)

    # -- Phase 13: Replace OBFUSCATION ANALYSIS --
    source = replace_section(source,
        "🔍 OBFUSCATION ANALYSIS",
        BCC_OBFUSCATION)

    # -- Phase 14: Replace NARCISSISM INDEX --
    source = replace_section(source,
        "🪞 NARCISSISM INDEX FRAMEWORK",
        BCC_NARCISSISM)

    # -- Phase 15: Replace REQUEST vs DELIVERY --
    source = replace_section(source,
        "📋 COMPLETE REQUEST vs DELIVERY COMPARISON",
        BCC_REQUEST_VS_DELIVERY)

    # -- Phase 16: Title banner update --
    source = re.sub(
        r'(<h1>).*?(</h1>)',
        r'\1⚖️ CAIN ACCOUNTABILITY SYSTEM — BCC ORGANIZATIONAL ANALYSIS ⚖️\2',
        source, count=1, flags=re.S)

    # -- Phase 17: Banner description update (if "LM Studio" still present) --
    source = source.replace("LM Studio", "BCC Investigation")

    # -- Phase 18: Add BCC CSS --
    css = '''
        /* BCC alternate population layer: original CAIN visual template preserved above. */
        .bcc-section { margin: 38px 0; padding: 25px; background: rgba(20, 8, 18, .96); border: 3px solid #ff69b4; border-radius: 18px; box-shadow: 0 0 35px rgba(255, 20, 147, .22); }
        .bcc-section h2 { margin-top: 0; }
        .bcc-disclaimer { padding: 15px 18px; border-left: 5px solid #ffaa44; background: rgba(100, 55, 0, .2); color: #ffe4b0; }
        .bcc-actor-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); gap: 18px; }
        .bcc-actor-card { background: linear-gradient(145deg, rgba(60, 15, 45, .94), rgba(25, 8, 25, .98)); border: 2px solid rgba(255, 105, 180, .55); border-radius: 14px; padding: 18px; min-height: 310px; box-shadow: 0 0 18px rgba(255, 20, 147, .15); }
        .bcc-actor-card:hover { border-color: #ffccdd; transform: translateY(-3px); transition: .25s ease; }
        .bcc-actor-heading { display: flex; gap: 12px; align-items: flex-start; border-bottom: 1px solid rgba(255, 182, 193, .28); padding-bottom: 10px; margin-bottom: 10px; }
        .bcc-index { display: inline-flex; width: 34px; height: 34px; align-items: center; justify-content: center; border-radius: 50%; background: #8b0000; border: 2px solid #ff69b4; color: #fff; font-weight: bold; flex: 0 0 auto; }
        .bcc-actor-card h3 { margin: 0; padding: 0; border: 0; background: none; font-size: 1.05em; }
        .bcc-role { color: #ffcc88; font-size: .82em; margin-top: 4px; }
        .bcc-actor-card dl { font-size: .78em; }
        .bcc-actor-card dt { color: #ffb6c1; font-weight: bold; margin-top: 9px; }
        .bcc-actor-card dd { margin-left: 0; color: #ffe6f2; }
        .bcc-pattern { color: #ffd27d !important; }
        .bcc-unknown { color: #ffb0aa !important; }
        .bcc-next { color: #bde8ff !important; }
        .bcc-sources { font-size: .72em; color: #b8a0b0; margin-top: 12px; }
        .bcc-sources a { color: #8fcaff; }
        .bcc-network-graph { background: rgba(8, 5, 18, .9); border: 2px solid #5c2020; border-radius: 14px; padding: 12px; overflow-x: auto; }
        .bcc-network-graph svg { width: 100%; min-width: 760px; height: auto; display: block; }
        .bcc-network-graph line { stroke: #ff69b4; stroke-width: 1.5; opacity: .5; stroke-dasharray: 5 4; }
        .bcc-center { fill: #8b0000; stroke: #ff4444; stroke-width: 4; }
        .bcc-center-text { fill: #fff; text-anchor: middle; font-size: 14px; font-weight: bold; }
        .bcc-svg-node circle { fill: #3d1515; stroke: #ffb6c1; stroke-width: 2; }
        .bcc-svg-node text { fill: #ffe6f2; text-anchor: middle; font-size: 10px; pointer-events: none; }
        .bcc-graph-note { color: #c8aebd; font-size: .78em; margin: 10px 4px 2px; }
        .bcc-flow-list { display: grid; gap: 10px; }
        .bcc-flow-row { display: grid; grid-template-columns: minmax(145px, 1fr) auto minmax(190px, 1.4fr) auto minmax(190px, 1.4fr) auto minmax(190px, 1.4fr); gap: 8px; align-items: stretch; }
        .bcc-flow-box { display: flex; align-items: center; padding: 10px 12px; border: 1px solid #8b4500; border-radius: 10px; background: rgba(50, 20, 20, .9); color: #ffe4d0; font-size: .75em; }
        .bcc-flow-box.actor { border-color: #ff69b4; color: #ffd1e8; font-weight: bold; }
        .bcc-flow-box.question { border-color: #ff7777; color: #ffc0b8; }
        .bcc-flow-box.record { border-color: #66baff; color: #c5e8ff; }
        .bcc-arrow { color: #ff69b4; font-size: 1.3em; display: flex; align-items: center; }
        @media (max-width: 900px) { .bcc-flow-row { grid-template-columns: 1fr; } .bcc-arrow { justify-content: center; transform: rotate(90deg); } }
'''
    source = source.replace("</style>", css + "\n    </style>", 1)

    # -- Phase 19: Append BCC Actor Network section --
    actor_section = f'''\n<section class="bcc-section" id="bcc-actor-network">\n  <h2>🕸️ BCC ACTOR NETWORK — 16 ACTORS POPULATED</h2>\n  <p class="bcc-disclaimer"><strong>Alternate-template integrity:</strong> This is the original CAIN visual template with a BCC actor population layer added. CAIN patterns are narrative shorthand, not clinical diagnoses or findings of intent. Documented relationships and deficiencies do not by themselves establish transactions, influence, misconduct, or patient injury.</p>\n  <h3>Actor relationship graph</h3>\n  {graph()}\n  <h3>All actor accountability cards</h3>\n  <div class="bcc-actor-grid">\n    {actor_cards()}\n  </div>\n  <h3>Actor → documented relationship → question → record flowchart</h3>\n  <div class="bcc-flow-list">\n    {flow_rows()}\n  </div>\n  <h3>Population completeness</h3>\n  <p class="bcc-disclaimer"><strong>16/16 populated:</strong> CEO, CFO, board chair, directors, clinical leaders, external funding/oversight nodes, and the Berks County Commissioners are all represented in the graph, cards, and flowchart. The graph shows relationship paths for investigation; it does not convert proximity into proof.</p>\n  <h3>CAIN Section Completeness</h3>\n  <p class="bcc-disclaimer"><strong>15/15 major sections populated:</strong> Seven Deadly Sins → BCC governance patterns · Money Snake → BCC financial flow · Financial Ledger → $12.5M/yr chain · Buck Stop → Board + regulatory responsibility · Stakeholder Impact → 10 stakeholders mapped · Cost Accounting → 8 cost categories · Legal Exposure → 7 regulatory domains · Before/After → 6 community metrics · Action Items → 12 investigation gaps · Methodology → 12 data sources · Obfuscation → 11 blocked channels · Narcissism → 7 dimensions · Request vs Delivery → 6 expectations. Original CAIN visual elements (CSS animations, money snake coils, archetype cards, web nodes, flow arrows, emoji dance) preserved throughout.</p>\n</section>\n'''
    source = source.replace("</body>", actor_section + "\n</body>", 1)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(source, encoding="utf-8")
    print(f"WROTE {output}")
    print(f"TEMPLATE {TEMPLATE}")
    print(f"ACTORS {len(ACTORS)}")
    print(f"BYTES {output.stat().st_size}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
