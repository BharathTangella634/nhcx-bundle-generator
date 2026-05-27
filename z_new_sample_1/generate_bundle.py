import json
import uuid
from datetime import datetime, timezone

# ----------------------------------------------------------------------
# Constants (do NOT modify)
# ----------------------------------------------------------------------
INPUT_PATH = "/mnt/8b4bbd12-99b7-4ef1-9218-be56afd51a3d/nhcx-bundle-generator/workspace/generated/extracted_data.json"
OUTPUT_PATH = r"/mnt/8b4bbd12-99b7-4ef1-9218-be56afd51a3d/nhcx-bundle-generator/workspace/generated/InsurancePlanBundle.json"

# ----------------------------------------------------------------------
# Static code‑systems & display maps required by the spec
# ----------------------------------------------------------------------
SNOMED_CODES = {
    "inpatient": {"code": "737481003", "display": "Inpatient care management (procedure)"},
    "inpatient_care": {"code": "737481003", "display": "Inpatient care management (procedure)"},
    "hospitalization": {"code": "737481003", "display": "Inpatient care management (procedure)"},
    "post_discharge": {"code": "710967003", "display": "Management of health status after discharge from hospital (procedure)"},
    "post_hospitalization": {"code": "710967003", "display": "Management of health status after discharge from hospital (procedure)"},
    "pre_hospital": {"code": "409972000", "display": "Pre-hospital care (situation)"},
    "pre_hospitalization": {"code": "409972000", "display": "Pre-hospital care (situation)"},
    "ambulance": {"code": "49122002", "display": "Ambulance, device (physical object)"},
    "day_care": {"code": "737850002", "display": "Day care case management"},
    "daycare": {"code": "737850002", "display": "Day care case management"},
    "organ_donor": {"code": "105461009", "display": "Organ donor"},
    "organ_transplant": {"code": "105461009", "display": "Organ donor"},
    "icu": {"code": "309904001", "display": "Intensive care unit (environment)"},
    "iccu": {"code": "309904001", "display": "Intensive care unit (environment)"},
    "blood": {"code": "87612001", "display": "Blood"},
    "oxygen": {"code": "24099007", "display": "Oxygen (substance)"},
    "single_room": {"code": "224663004", "display": "Single room (environment)"},
    "room_rent": {"code": "224663004", "display": "Single room (environment)"},
    "home_care": {"code": "60689008", "display": "Home care of patient"},
    "domiciliary": {"code": "60689008", "display": "Home care of patient"},
    "pharmacy": {"code": "373784001", "display": "Pharmacy service (procedure)"},
    "consultation": {"code": "11429006", "display": "Consultation (procedure)"},
    "doctor_consultation": {"code": "11429006", "display": "Consultation (procedure)"},
    "diagnostic": {"code": "165340005", "display": "Laboratory test finding (finding)"},
    "maternity": {"code": "118189007", "display": "Prenatal finding (finding)"},
    "newborn": {"code": "133906008", "display": "Newborn care (regime/therapy)"},
    "ayush": {"code": "716186003", "display": "No known allergy (situation)"},
}
EXCLUSION_CODES = {
    "Excl01": "Pre-Existing Diseases",
    "Excl02": "Specified disease/procedure waiting period",
    "Excl03": "30-day waiting period",
    "Excl04": "Investigation Evaluation",
    "Excl05": "Rest Cure,Rehabilitation and Respite Care",
    "Excl06": "Obesity/Weight Control",
    "Excl07": "Change-of-Gender treatments",
    "Excl08": "Cosmetic or plastic Surgery",
    "Excl09": "Hazardous or Adventure sports",
    "Excl10": "Breach of law",
    "Excl11": "Excluded providers",
    "Excl12": "Rehabilitation",
    "Excl13": "Hydrotherapy",
    "Excl14": "Non-prescription",
    "Excl15": "Refractive Error",
    "Excl16": "Unproven Treatments",
    "Excl17": "Sterility and infertility",
    "Excl18": "Maternity expenses",
}
INSURANCEPLAN_TYPE_CODES = {
    "01": {"code": "01", "display": "Hospitalisation Indemnity Policy"},
    "02": {"code": "02", "display": "Hospital Cash Plan"},
}
PLAN_TYPE_CS = "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-plan-type"
INSURANCEPLAN_TYPE_CS = "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-insuranceplan-type"
CLAIM_EXCLUSION_URL = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Exclusion"
CLAIM_SUPPORTING_INFO_URL = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-SupportingInfoRequirement"
CLAIM_CONDITION_URL = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Condition"
BUNDLE_PROFILE = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlanBundle"
INSURANCEPLAN_PROFILE = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlan"
ORGANIZATION_PROFILE = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Organization"

# ----------------------------------------------------------------------
# Helper utilities
# ----------------------------------------------------------------------
def make_uuid():
    return str(uuid.uuid4())

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def narrative(resource_type, resource_id, has_extensions):
    status = "extensions" if has_extensions else "generated"
    div = f'<div xmlns="http://www.w3.org/1999/xhtml"><p><b>{resource_type} {resource_id}</b></p></div>'
    return {"status": status, "div": div}

def codeable_concept_from_snomed(key):
    """Return a CodeableConcept dict if key exists in SNOMED_CODES, else None."""
    entry = SNOMED_CODES.get(key.lower())
    if entry:
        return {"coding": [{"system": "http://snomed.info/sct", "code": entry["code"], "display": entry["display"]}]}
    return None

def codeable_concept_from_code_system(system, code, display):
    return {"coding": [{"system": system, "code": code, "display": display}]}

def claim_exclusion_extension(excl_code, statement, item_coding=None):
    """Build Claim‑Exclusion extension."""
    category_display = EXCLUSION_CODES.get(excl_code, None)
    if not category_display:
        # fallback to text‑only exclusion (no code)
        return {
            "extension": [
                {"url": "statement", "valueString": statement}
            ],
            "url": CLAIM_EXCLUSION_URL
        }
    ext = [
        {
            "url": "category",
            "valueCodeableConcept": {
                "coding": [{
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": excl_code,
                    "display": category_display
                }]
            }
        },
        {"url": "statement", "valueString": statement}
    ]
    if item_coding:
        ext.append({
            "url": "item",
            "valueCodeableConcept": {
                "coding": [item_coding]
            }
        })
    return {"extension": ext, "url": CLAIM_EXCLUSION_URL}

def claim_supporting_info_extension(category_code, category_display, doc_code, doc_display):
    return {
        "extension": [
            {
                "url": "category",
                "valueCodeableConcept": {
                    "coding": [{
                        "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-supportinginfo-category",
                        "code": category_code,
                        "display": category_display
                    }]
                }
            },
            {
                "url": "code",
                "valueCodeableConcept": {
                    "coding": [{
                        "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code",
                        "code": doc_code,
                        "display": doc_display
                    }]
                }
            }
        ],
        "url": CLAIM_SUPPORTING_INFO_URL
    }

def claim_condition_extension(condition_text):
    return {
        "extension": [
            {
                "url": "claim-condition",
                "valueString": condition_text
            }
        ],
        "url": CLAIM_CONDITION_URL
    }

# ----------------------------------------------------------------------
# Main builder
# ----------------------------------------------------------------------
def build_bundle(data):
    bundle_id = make_uuid()
    bundle = {
        "resourceType": "Bundle",
        "id": bundle_id,
        "meta": {
            "versionId": "1",
            "profile": [BUNDLE_PROFILE],
            "security": [{
                "system": "http://terminology.hl7.org/CodeSystem/v3-Confidentiality",
                "code": "V",
                "display": "very restricted"
            }]
        },
        "type": "collection",
        "timestamp": now_iso(),
        "entry": []
    }

    # ------------------------------------------------------------------
    # Organization (issuer / administrator)
    # ------------------------------------------------------------------
    org_id = make_uuid()
    insurer = data.get("insurer", {})
    org_identifier = {
        "type": {
            "coding": [{
                "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code",
                "code": "ROHINI",
                "display": "Registry of Hospitals in Network of Insurance (ROHINI) ID"
            }]
        },
        "system": "https://rohini.iib.gov.in/",
        "value": insurer.get("rohini_id", "UNKNOWN_ROHINI")
    }
    organization = {
        "resourceType": "Organization",
        "id": org_id,
        "meta": {"profile": [ORGANIZATION_PROFILE]},
        "text": narrative("Organization", org_id, False),
        "identifier": [org_identifier],
        "name": insurer.get("name", "UNKNOWN_INSURER"),
        "telecom": [
            {"system": "phone", "value": insurer.get("contact_phone", ""), "use": "work"},
            {"system": "email", "value": insurer.get("contact_email", ""), "use": "work"}
        ]
    }
    bundle["entry"].append({
        "fullUrl": f"urn:uuid:{org_id}",
        "resource": organization
    })

    # ------------------------------------------------------------------
    # InsurancePlan
    # ------------------------------------------------------------------
    plan_id = make_uuid()
    policy = data.get("policy", {})
    insurer_type_code = policy.get("insurance_type_code", "01")
    plan_type_coding = INSURANCEPLAN_TYPE_CODES.get(insurer_type_code, {"code": insurer_type_code, "display": "Unknown"})
    insuranceplan = {
        "resourceType": "InsurancePlan",
        "id": plan_id,
        "meta": {"profile": [INSURANCEPLAN_PROFILE]},
        "text": {"status": "generated", "div": f'<div xmlns="http://www.w3.org/1999/xhtml"><p>{policy.get("full_name","Unnamed Plan")}</p></div>'},
        "identifier": [{
            "system": "https://irdai.gov.in",
            "value": policy.get("uin", "UNKNOWN_UIN")
        }],
        "status": "active",
        "type": [{
            "coding": [{
                "system": INSURANCEPLAN_TYPE_CS,
                "code": plan_type_coding["code"],
                "display": plan_type_coding["display"]
            }]
        }],
        "name": policy.get("full_name", "Unnamed Plan"),
        "ownedBy": {"reference": f"urn:uuid:{org_id}"},
        "administeredBy": {"reference": f"urn:uuid:{org_id}"},
        "extension": [],          # will be filled with exclusions & supporting docs
        "coverage": [],           # will be filled below
        "plan": []                # will be filled below
    }

    # ------------------------------------------------------------------
    # Supporting Documents -> Claim‑SupportingInfoRequirement
    # ------------------------------------------------------------------
    for doc in data.get("supporting_documents", []):
        cat_code = doc.get("category_code", "UNKNOWN")
        cat_disp = doc.get("category_display", "Unknown Category")
        doc_code = doc.get("document_code", "UNKNOWN")
        doc_disp = doc.get("document_display", "Unknown Document")
        insuranceplan["extension"].append(
            claim_supporting_info_extension(cat_code, cat_disp, doc_code, doc_disp)
        )

    # ------------------------------------------------------------------
    # Permanent Exclusions -> Claim‑Exclusion
    # ------------------------------------------------------------------
    for excl in data.get("permanent_exclusions", []):
        code = excl.get("code")
        statement = excl.get("description", "")
        insuranceplan["extension"].append(
            claim_exclusion_extension(code, statement)
        )

    # ------------------------------------------------------------------
    # Waiting Periods -> also Claim‑Exclusion (using Excl02/Excl03 where appropriate)
    # ------------------------------------------------------------------
    for wp in data.get("waiting_periods", []):
        # Attempt to map waiting period name to a known exclusion code
        wp_name = wp.get("name", "").lower()
        if "pre‑existing" in wp_name or "pre-existing" in wp_name:
            excl_code = "Excl01"
        elif "specified" in wp_name:
            excl_code = "Excl02"
        elif "30‑day" in wp_name or "30-day" in wp_name:
            excl_code = "Excl03"
        else:
            excl_code = None
        statement = wp.get("description", "")
        insuranceplan["extension"].append(
            claim_exclusion_extension(excl_code, statement)
        )

    # ------------------------------------------------------------------
    # Coverages
    # ------------------------------------------------------------------
    for cov in data.get("coverages", []):
        cov_id = make_uuid()
        # Determine coverage type CodeableConcept
        cov_type = None
        if cov.get("snomed_code"):
            cov_type = {
                "coding": [{
                    "system": "http://snomed.info/sct",
                    "code": cov["snomed_code"],
                    "display": cov.get("snomed_display", cov.get("name", ""))
                }]
            }
        else:
            # try to map type_hint
            hint = cov.get("type_hint", "")
            cov_type = codeable_concept_from_snomed(hint) or {"text": cov.get("name", "Unnamed Coverage")}

        # Build coverage entry
        coverage_entry = {
            "type": cov_type,
            "benefit": [],
            "extension": []   # Claim‑Condition extensions will be added here
        }

        # Add Claim‑Condition extensions for coverage‑level conditions
        for cond in cov.get("conditions", []):
            coverage_entry["extension"].append(claim_condition_extension(cond))

        # Process sub‑benefits (if any)
        for sb in cov.get("sub_benefits", []):
            benefit_type = None
            # Attempt to map sub‑benefit name to SNOMED
            sb_name = sb.get("name", "").lower()
            benefit_type = codeable_concept_from_snomed(sb_name) or {"text": sb.get("name", "Unnamed Benefit")}
            benefit = {"type": benefit_type}
            # Add condition extensions for sub‑benefit specific text (if needed)
            if sb.get("what_is_covered"):
                benefit.setdefault("extension", []).append(claim_condition_extension(sb["what_is_covered"]))
            coverage_entry["benefit"].append(benefit)

        # If no sub‑benefits, create a placeholder benefit using coverage name
        if not coverage_entry["benefit"]:
            coverage_entry["benefit"].append({
                "type": {"text": cov.get("name", "Unnamed Benefit")}
            })

        insuranceplan["coverage"].append(coverage_entry)

    # ------------------------------------------------------------------
    # Plan entries (cost sharing) – minimal placeholder using policy data
    # ------------------------------------------------------------------
    plan_entry = {
        "identifier": [{
            "use": "official",
            "value": policy.get("full_name", "Unnamed Plan")
        }],
        "type": {
            "coding": [{
                "system": PLAN_TYPE_CS,
                "code": "01",
                "display": "Individual"
            }]
        },
        "generalCost": [{
            "cost": {
                "value": 0,
                "currency": "INR"
            }
        }]
    }
    insuranceplan["plan"].append(plan_entry)

    # ------------------------------------------------------------------
    # Add InsurancePlan entry to bundle
    # ------------------------------------------------------------------
    bundle["entry"].append({
        "fullUrl": f"urn:uuid:{plan_id}",
        "resource": insuranceplan
    })

    return bundle

# ----------------------------------------------------------------------
# Execution
# ----------------------------------------------------------------------
def main():
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    bundle = build_bundle(data)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
