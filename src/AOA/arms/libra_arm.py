"""Libra Arm - Adapter for legal document automation system.

REGULATORY NOTICE: All generated documents include a compliance disclaimer
requiring licensed attorney review before filing. This is NOT legal advice.
"""
import sys, json, datetime
from typing import Dict, List
from pathlib import Path
from core.arm_registry import BaseArm, ArmCapability, register_arm

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Static lookup tables for template-to-method routing
_METHOD_MAP = {
    "pfa_petition": "generate_ifp",
    "motion": "generate_motion",
    "appeal": "generate_appeal",
    "discovery": "generate_discovery",
    "complaint": "generate_complaint",
    # Berks County BCC-specific templates
    "bcc_motion": "generate_motion",
    "bcc_motion_brief": "generate_motion",
    "bcc_cover_sheet": "generate_motion",
    "bcc_ifp_petition": "generate_ifp",
    "bcc_certificate_of_service": "generate_motion",
    "bcc_continuance_motion": "generate_motion",
    "bcc_praecipe_argument": "generate_motion",
    "bcc_entry_of_appearance": "generate_motion",
    "bcc_certificate_readiness": "generate_motion",
    "bcc_verification": "generate_motion",
}
_METHOD_DEFAULTS = {
    "generate_motion": {"motion_type": "Motion for Relief", "movant": "Petitioner", "court_name": "Berks County Court of Common Pleas", "court_level": "pa_commonwealth"},
    "generate_ifp": {"plaintiff": "Petitioner", "court_name": "Berks County Court of Common Pleas", "court_level": "pa_commonwealth"},
    "generate_appeal": {"appellant": "Appellant", "appellee": "Appellee", "order_date": "[Date of Order]", "order_description": "[Description of order being appealed]"},
    "generate_discovery": {"propounding_party": "Requesting Party", "responding_party": "Responding Party"},
}

@register_arm(arm_id="libra",name="Libra Legal Automation",version="0.1.0",description="Legal document automation with 55 templates, 69 filing types, template generator, fee schedules, and deadline calculator",category="quartet",priority=9)
class LibraArm(BaseArm):
    def initialize(self)->bool:
        try:
            libra_src = Path.home() / "Desktop" / "Libra" / "libra"
            if libra_src.exists() and str(libra_src) not in sys.path:
                sys.path.insert(0, str(libra_src))
            self._libra_src = libra_src
            self._initialized = True
            return True
        except Exception as e:
            print(f"LibraArm init failed: {e}")
            return False
    def execute(self,capability,payload=None)->dict:
        payload=payload or {}
        try:
            if capability=="list_templates":
                from template_generator import TemplateGenerator
                tg=TemplateGenerator()
                templates=tg.list_templates()
                return {"status":"ok","data":{"count":len(templates),"templates":templates}}
            elif capability=="generate_document":
                tn=payload.get("template","pfa_petition")
                tokens=payload.get("tokens",{})
                from template_generator import TemplateGenerator
                tg=TemplateGenerator()
                method_name=_METHOD_MAP.get(tn)
                if method_name is None:
                    return {"status":"error","error":f"Unknown template: {tn}. Available: {list(_METHOD_MAP.keys())}"}
                # Merge method defaults with user tokens (user tokens take precedence)
                defaults=_METHOD_DEFAULTS.get(method_name,{})
                # Replace placeholder names if user provided them
                merged={}
                for k,v in defaults.items():
                    if k in ("movant","appellant","propounding_party","plaintiff") and tokens.get("plaintiff_name"):
                        merged[k]=tokens["plaintiff_name"]
                    elif k in ("appellee","responding_party") and tokens.get("defendant_name"):
                        merged[k]=tokens["defendant_name"]
                    else:
                        merged[k]=v
                merged.update(tokens)
                # Strip mapping keys that aren't method parameters
                merged.pop("plaintiff_name", None)
                merged.pop("defendant_name", None)
                content=getattr(tg,method_name)(**merged) if merged else getattr(tg,method_name)()
                return {"status":"ok","data":{
                    "template":tn,
                    "method":method_name,
                    "length":len(str(content)),
                    "content_preview":str(content)[:500],
                    "disclaimer":"REGULATORY NOTICE: This document was AI-generated. "
                                 "It is a DRAFT TEMPLATE ONLY. Must be reviewed by a "
                                 "licensed attorney before filing. NOT legal advice."
                }}
            elif capability=="get_filing_types":
                if hasattr(self,"_libra_src") and self._libra_src.exists():
                    ft_path=self._libra_src.parent/"data"/"filing_types.json"
                else:
                    ft_path=Path.home()/"Desktop"/"Libra"/"data"/"filing_types.json"
                with open(ft_path, encoding="utf-8") as f:
                    ft=json.load(f)
                types=ft.get("filing_types",{})
                return {"status":"ok","data":{"total":len(types),"categories":list(set(v["category"] for v in types.values()))}}
            elif capability=="calculate_fees":
                ft=payload.get("filing_type","PFA_PETITION")
                court=payload.get("court","pa_commonwealth")
                from fee_schedule import FeeSchedule
                fs=FeeSchedule()
                fee=fs.get_fee(court, ft)
                return {"status":"ok","data":{"filing_type":ft,"court":court,"fee":fee}}
            elif capability=="calculate_deadline":
                from deadline_calculator import DeadlineCalculator
                dc=DeadlineCalculator()
                days=payload.get("days",30)
                base=datetime.date.today()
                d=dc.add_days(base,days)
                return {"status":"ok","data":{"days":days,"base_date":str(base),"deadline":str(d)}}
            elif capability=="check_berks_rules":
                from jurisdictions.pa.berks.court_rules import BerksCourtRules
                bcr=BerksCourtRules()
                filing_type=payload.get("filing_type","motion")
                issues=bcr.validate_filing({"filing_type":filing_type,"court_level":"pa_commonwealth"})
                return {"status":"ok","data":{"filing_type":filing_type,"issues":issues,"pass":len(issues)==0}}
            elif capability=="calculate_berks_fees":
                from jurisdictions.pa.berks.prothonotary import BerksProthonotary
                ft=payload.get("filing_types",["civil_complaint"])
                total=BerksProthonotary.calculate_total_fee(ft)
                schedule=BerksProthonotary.get_all_fees()
                return {"status":"ok","data":{"filing_types":ft,"total_fee":total,"schedule":schedule}}
            elif capability=="generate_berks_ifp":
                from jurisdictions.pa.berks.ifp_assistant import BerksIFPAssistant,FinancialAffidavit,EmploymentStatus
                ifp=BerksIFPAssistant()
                affidavit=FinancialAffidavit(
                    monthly_gross_income=payload.get("monthly_income",0),
                    employment_status=EmploymentStatus(payload.get("employment_status","unemployed")),
                    cash_on_hand=payload.get("cash_on_hand",0),
                    dependents_count=payload.get("dependents",0),
                )
                result=ifp.check_eligibility(affidavit)
                return {"status":"ok","data":{"eligible":result.eligible,"reasons":result.reasons,"income_below_threshold":result.income_below_threshold,"monthly_income":affidavit.total_monthly_income}}
            elif capability=="prepare_berks_filing":
                from jurisdictions.pa.berks.prothonotary import BerksProthonotary
                from jurisdictions.pa.berks.filing_submitter import PaperFilingSubmitter
                packet=BerksProthonotary.prepare_filing_packet(
                    petitioner_name=payload.get("name","Petitioner"),
                    case_number=payload.get("case_number",""),
                    filing_types=payload.get("filing_types",["civil_complaint"]),
                )
                submitter=PaperFilingSubmitter()
                result=submitter.submit({
                    "filing_type":payload.get("filing_types",["civil_complaint"])[0],
                    "petitioner_name":payload.get("name","Petitioner"),
                    "case_number":payload.get("case_number",""),
                    "filing_fee":packet.total_fee,
                })
                return {"status":"ok","data":{
                    "packet":{"total_fee":packet.total_fee,"copies":packet.copies_required,"sase":packet.sase_required},
                    "submission":{"success":result.success,"tracking":result.tracking_number},
                    "instructions":packet.filing_instructions[:500]
                }}
            elif capability=="get_berks_calendar":
                from jurisdictions.pa.berks.court_calendar import BerksCourtCalendar
                cal=BerksCourtCalendar()
                year=payload.get("year",2026)
                month=payload.get("month",7)
                sched=cal.get_court_schedule(year,month)
                # get_court_schedule already returns ISO strings and dicts
                return {"status":"ok","data":sched}
            elif capability=="get_status":
                return {"status":"ok","data":{"initialized":self._initialized,"libra_src":str(self._libra_src) if hasattr(self,"_libra_src") else None,"libra_src_exists":self._libra_src.exists() if hasattr(self,"_libra_src") else False}}
            return {"status":"error","error":"Unknown: "+capability}
        except Exception as e:
            note_failure("libra_arm:L168", e)
            return {"status":"error","error":str(e)}
    def get_capabilities(self)->List[ArmCapability]:
        return [ArmCapability("list_templates","List all available templates",category="template"),ArmCapability("generate_document","Generate a document from a template",category="generation"),ArmCapability("get_filing_types","Get all filing types",category="filing"),ArmCapability("calculate_fees","Calculate filing fees",category="fees"),ArmCapability("calculate_deadline","Calculate court deadlines",category="deadline"),ArmCapability("get_status","Get Libra status",category="monitoring"),ArmCapability("check_berks_rules","Validate a filing against Berks County rules (BCR)",category="berks"),ArmCapability("calculate_berks_fees","Calculate Berks County filing fees",category="berks"),ArmCapability("generate_berks_ifp","Check IFP eligibility and generate IFP petition for Berks County",category="berks"),ArmCapability("prepare_berks_filing","Prepare a complete Berks County filing packet",category="berks"),ArmCapability("get_berks_calendar","Get Berks County court calendar/schedule",category="berks")]
    def shutdown(self)->bool:
        self._initialized=False; return True
