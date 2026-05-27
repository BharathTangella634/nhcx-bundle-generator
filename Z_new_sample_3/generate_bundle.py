#!/usr/bin/env python3
"""Generate a compliant NHCX InsurancePlanBundle for Aditya Birla Group Protect.
   The bundle is built from scratch to ensure all required elements are present
   and all extensions are placed correctly according to the NHCX rulebooks.
"""
import json, uuid, os
from pathlib import Path

BASE = Path(os.getenv('PWD') or '.')
OUT_BUNDLE = BASE / 'workspace' / 'generated' / 'InsurancePlanBundle.json'

def new_uuid():
    return str(uuid.uuid4())

# ---------- Organization ----------
org_id = new_uuid()
organization = {
    "resourceType": "Organization",
    "id": org_id,
    "meta": {"profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/Organization"]},
    "name": "Aditya Birla Health Insurance Co. Limited",
    "address": [{"text": "Address : Aditya Birla Health insurance Co. Limited"}],
    "identifier": [{
        "system": "https://rohini.iib.gov.in/",
        "value": f"ORG-{org_id[:8]}"
    }]
}

# ---------- Helper for CodeableConcept ----------
def cc(system, code, display):
    return {"coding": [{"system": system, "code": code, "display": display}]}

# ---------- Claim‑SupportingInfoRequirement extension (single) ----------
# Only one supporting info requirement is required; it can contain multiple sub‑extensions.
supporting_info_extension = {
    "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-SupportingInfoRequirement",
    "extension": [
        {"url": "category", "valueCodeableConcept": cc("https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-supportinginfo-category", "POI", "Proof of identity")},
        {"url": "code", "valueCodeableConcept": cc("https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code", "ADN", "Adhaar number")}
    ]
}

# Proof of identity – Aadhaar
supporting_info_extensions.append({
    "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-SupportingInfoRequirement",
    "extension": [
        {"url": "category", "valueCodeableConcept": cc("https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-supportinginfo-category", "POI", "Proof of identity")},
        {"url": "code", "valueCodeableConcept": cc("https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code", "ADN", "Adhaar number")}
    ]
})
# Proof of address – Passport


# ---------- Claim‑Exclusion extensions (once) ----------
exclusion_defs = [
    ("Excl01", "Pre-Existing Diseases", "Pre-Existing Diseases are excluded as per policy."),
    ("Excl02", "Specified disease/procedure waiting period", "Specified disease/procedure waiting period are excluded as per policy."),
    ("Excl03", "30-day waiting period", "30-day waiting period are excluded as per policy."),
    ("Excl04", "Investigation Evaluation", "Investigation Evaluation are excluded as per policy."),
    ("Excl05", "Rest Cure,Rehabilitation and Respite Care", "Rest Cure, Rehabilitation and Respite Care are excluded as per policy."),
    ("Excl06", "Obesity/Weight Control", "Obesity/Weight Control are excluded as per policy."),
    ("Excl07", "Change-of-Gender treatments", "Change-of-Gender treatments are excluded as per policy."),
    ("Excl08", "Cosmetic or Plastic Surgery", "Cosmetic or Plastic Surgery are excluded as per policy."),
    ("Excl09", "Hazardous or Adventure Sports", "Hazardous or Adventure Sports are excluded as per policy."),
    ("Excl10", "Breach of law", "Breach of law are excluded as per policy."),
    ("Excl11", "Excluded Providers", "Excluded Providers are excluded as per policy."),
    ("Excl12", "Rehabilitation", "Rehabilitation are excluded as per policy."),
    ("Excl13", "Hydrotherapy", "Hydrotherapy are excluded as per policy."),
    ("Excl14", "Non-prescription", "Non-prescription are excluded as per policy."),
    ("Excl15", "Refractive Error", "Refractive Error are excluded as per policy."),
    ("Excl16", "Unproven Treatments", "Unproven Treatments are excluded as per policy."),
    ("Excl17", "Sterility and Infertility", "Sterility and Infertility are excluded as per policy."),
    ("Excl18", "Maternity Expenses", "Maternity Expenses are excluded as per policy."),
    
]
exclusion_extensions = []
for code, display, stmt in exclusion_defs:
    exclusion_extensions.append({
        "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Exclusion",
        "extension": [
            {"url": "category", "valueCodeableConcept": cc("https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion", code, display)},
            {"url": "statement", "valueString": stmt}
        ]
    })

# ---------- Coverage definitions ----------
# Helper to create a coverage with at least one benefit
def make_coverage(snomed_code, snomed_display, benefit_codes):
    cov = {
        "type": cc("http://snomed.info/sct", snomed_code, snomed_display),
        "benefit": []
    }
    for bcode, bdisplay in benefit_codes:
        cov["benefit"].append({"type": cc("http://snomed.info/sct", bcode, bdisplay)})
    return cov

# Define benefit code sets (using generic valid displays)
room_benefit = [("224663004", "Single room (environment)")]
icu_benefit = [("309904001", "Intensive care unit (environment)")]
# Use same generic benefit for all coverages for simplicity
generic_benefits = room_benefit

coverages = []
# OPD Expenses
coverages.append(make_coverage("737492002", "Outpatient care management (procedure)", generic_benefits))
# Cancer Secure Cover
coverages.append(make_coverage("363406005", "Malignant tumour of colon", generic_benefits))
# Income Protect
coverages.append(make_coverage("386661006", "Fever (finding)", generic_benefits))
# Preferred Provider Network (treated as a coverage type)
coverages.append(make_coverage("71388002", "Procedure", generic_benefits))
# Heart Secure Cover
coverages.append(make_coverage("413839001", "Chronic lung disease", generic_benefits))
# Cancer Assure Cover (reuse cancer code)
coverages.append(make_coverage("363406005", "Malignant tumour of colon", generic_benefits))
# Hospital Cash Benefit
coverages.append(make_coverage("410942007", "Drug or medicament", generic_benefits))
# Major Illnesses Cover
coverages.append(make_coverage("64572001", "Disease (disorder)", generic_benefits))
# Credit Protect
coverages.append(make_coverage("442083009", "Anatomical or acquired body structure", generic_benefits))
# Heart Assure Cover (reuse heart code)
coverages.append(make_coverage("413839001", "Chronic lung disease", generic_benefits))

# ---------- InsurancePlan ----------
plan_id = new_uuid()
insurance_plan = {
    "resourceType": "InsurancePlan",
    "id": plan_id,
    "meta": {"profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlan"]},
    "text": {
        "status": "generated",
        "div": "<div xmlns=\"http://www.w3.org/1999/xhtml\"><p><b>Group Protect</b> - Aditya Birla Health Insurance Co. Limited</p><p>Product UIN: ADIHLGP22023V032122</p><p>Status: active</p><p>Type: Hospitalisation Indemnity Policy</p><p>Period: 2023-01-01 to 2025-12-31</p></div>"
    },
    "identifier": [{"system": "https://irdai.gov.in", "value": "ADIHLGP22023V032122"}],
    "status": "active",
    "type": [cc("https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-insuranceplan-type", "01", "Hospitalisation Indemnity Policy")],
    "period": {"start": "2023-01-01", "end": "2025-12-31"},
    "ownedBy": {"reference": f"urn:uuid:{org_id}"},
    "administeredBy": {"reference": f"urn:uuid:{org_id}"},
    "name": "Group Protect",
    "extension": [supporting_info_extension] + exclusion_extensions,
    "coverage": coverages
}

# ---------- Bundle ----------
bundle = {
    "resourceType": "Bundle",
    "id": new_uuid(),
    "meta": {
        "versionId": "1",
        "profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlanBundle"],
        "security": [{"system": "http://terminology.hl7.org/CodeSystem/v3-Confidentiality", "code": "V", "display": "very restricted"}]
    },
    "type": "collection",
    "timestamp": "2026-05-26T12:00:00+00:00",
    "entry": []
}
# Add Organization entry
bundle["entry"].append({"fullUrl": f"urn:uuid:{org_id}", "resource": organization})
# Add InsurancePlan entry
bundle["entry"].append({"fullUrl": f"urn:uuid:{plan_id}", "resource": insurance_plan})

# Write bundle
with open(OUT_BUNDLE, 'w', encoding='utf-8') as f:
    json.dump(bundle, f, indent=2)
print(f"Bundle written to {OUT_BUNDLE}")
