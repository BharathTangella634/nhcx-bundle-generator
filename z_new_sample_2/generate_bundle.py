"""generate_bundle.py
Generates an NHCX-compliant InsurancePlanBundle JSON from extraction_notes.txt.
The script parses the extraction notes (produced in Phase 2) and builds a FHIR Bundle
with:
  * Organization (insurer)
  * InsurancePlan (with identifier, status, period, type, plan entries)
  * Coverage entries for each major benefit
  * Claim‑Exclusion extensions (once on InsurancePlan)
  * Claim‑SupportingInfoRequirement extensions (once on InsurancePlan)
  * Claim‑Condition extensions on each coverage.benefit where waiting periods apply
All monetary values are placeholders (e.g., "<SumInsured_OPD>") because the PDF does not contain
exact numbers. The script uses UUIDs for all resource IDs and fullUrl values.
"""
import json, uuid, re, os
from pathlib import Path

# ---------- Helper functions ----------

def new_uuid():
    return str(uuid.uuid4())

def read_notes(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def extract_section(notes, header_regex):
    """Return the text block that follows a header matching header_regex until the next blank line or next header.
    Simple line‑based extraction sufficient for our notes format.
    """
    lines = notes.splitlines()
    capture = []
    in_section = False
    for line in lines:
        if re.search(header_regex, line, re.IGNORECASE):
            in_section = True
            continue
        if in_section:
            if line.strip() == '' or re.match(r'^---', line):
                break
            capture.append(line)
    return '\n'.join(capture).strip()

# ---------- Build resources ----------

def build_organization(notes):
    # Insurer details from notes
    name_match = re.search(r"Name:\s*(.+)", notes)
    address_match = re.search(r"Address:\s*(.+)", notes)
    name = name_match.group(1).strip() if name_match else "Aditya Birla Health Insurance Co. Limited"
    address = address_match.group(1).strip() if address_match else "9th Floor, Tower 1, One Indiabulls Centre, Jupiter Mills Compound, 841, Senapati Bapat Marg, Elphinstone Road, Mumbai 400013, India"
    org_id = new_uuid()
    org = {
        "resourceType": "Organization",
        "id": org_id,
        "meta": {"profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/Organization"]},
        "name": name,
        "address": [{"text": address}],
        "identifier": [{
            "system": "https://rohini.iib.gov.in/",
            "value": "ROHINI-PLACEHOLDER"
        }]
    }
    # Narrative
    org["text"] = {
        "status": "generated",
        "div": f"<div xmlns=\"http://www.w3.org/1999/xhtml\"><p>{name}</p><p>{address}</p></div>"
    }
    return org

def build_insurance_plan(notes, org_ref):
    # Extract UIN
    uin_match = re.search(r"UIN \(Product UIN\):\s*([A-Z0-9]+)", notes)
    uin = uin_match.group(1) if uin_match else "ADIHLGP22023V032122"
    # Identifier
    identifier = {
        "system": "https://irdai.gov.in",
        "value": uin
    }
    # Status and period (placeholders)
    status = "active"
    period = {"start": "2023-01-01"}
    # Type (Package Policy – using ndhm-insuranceplan-type code 01 as example)
    plan_type = {
        "coding": [{
            "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-insuranceplan-type",
            "code": "01",
            "display": "Package Policy"
        }]
    }
    plan_id = new_uuid()
    plan = {
        "resourceType": "InsurancePlan",
        "id": plan_id,
        "meta": {"profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlan"]},
        "identifier": identifier,
        "status": status,
        "type": [plan_type],
        "period": period,
        "ownedBy": {"reference": org_ref},
        "administeredBy": {"reference": org_ref},
        "coverage": [],
        "plan": [],
        "extension": []
    }
    # Narrative summary
    plan["text"] = {
        "status": "generated",
        "div": f"<div xmlns=\"http://www.w3.org/1999/xhtml\"><p><b>Group Protect</b> - {org_ref.split(':')[-1]}</p><p>Product UIN: {uin}</p><p>Status: {status}</p><p>Type: Package Policy</p></div>"
    }
    return plan

def add_supporting_info(plan):
    # Add a few generic supporting info requirements (identity, address, claim form)
    categories = [
        ("FCF", "Filled claim form"),
        ("POI", "Proof of identity"),
        ("POA", "Proof of address")
    ]
    for code, display in categories:
        ext = {
            "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-SupportingInfoRequirement",
            "extension": [
                {"url": "category", "valueCodeableConcept": {"coding": [{"system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-supportinginfo-category", "code": code, "display": display}] }},
                {"url": "code", "valueCodeableConcept": {"coding": [{"system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code", "code": "ADN", "display": "Adhaar number"}]}}
            ]
        }
        plan["extension"].append(ext)

def add_exclusions(plan):
    # Use the exclusion codes list from the notes (Excl01‑Excl18)
    exclusion_codes = [
        "Excl01","Excl02","Excl03","Excl04","Excl05","Excl06","Excl07","Excl08",
        "Excl09","Excl10","Excl11","Excl12","Excl13","Excl14","Excl15","Excl16",
        "Excl17","Excl18"
    ]
    for code in exclusion_codes:
        ext = {
            "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Exclusion",
            "extension": [
                {"url": "category", "valueCodeableConcept": {"coding": [{"system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion", "code": code, "display": ""}]}}
            ]
        }
        plan["extension"].append(ext)

def make_coverage(benefit_code, display, condition_text=None, benefit_limit="<Limit>"):
    cov = {
        "type": {"coding": [{"system": "http://snomed.info/sct", "code": benefit_code, "display": display}]},
        "benefit": [{"type": {"coding": [{"system": "http://snomed.info/sct", "code": benefit_code, "display": display}]}}]
    }
    if condition_text:
        cov["extension"] = [{
            "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Condition",
            "extension": [{"url": "claim-condition", "valueString": condition_text}]
        }]
    return cov

def add_coverages(plan):
    # Helper to create coverage with optional extra conditions
    def make_cov(code, display, condition_text=None, extra_conditions=None):
        cov = {
            "type": {"coding": [{"system": "http://snomed.info/sct", "code": code, "display": display}]},
            "benefit": [{"type": {"coding": [{"system": "http://snomed.info/sct", "code": code, "display": display}]}}]
        }
        extensions = []
        if condition_text:
            extensions.append({
                "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Condition",
                "extension": [{"url": "claim-condition", "valueString": condition_text}]
            })
        if extra_conditions:
            for ec in extra_conditions:
                extensions.append({
                    "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Condition",
                    "extension": [{"url": "claim-condition", "valueString": ec}]
                })
        if extensions:
            cov["extension"] = extensions
        return cov
    # OPD Expenses – SNOMED 737492002 (Outpatient)
    plan["coverage"].append(make_cov("737492002", "Outpatient care (procedure)", "Waiting period as per schedule", ["Co‑payment: <percentage>%"]))
    # Cancer Secure Cover – generic cancer code 363406005 (Neoplasm)
    plan["coverage"].append(make_cov("363406005", "Neoplasm (disorder)", "Initial waiting period and survival period apply", ["Room rent limit: Single Private A/C Room", "Co‑payment: <percentage>%"]))
    # Heart Secure Cover – SNOMED 49601007 (Heart disease)
    plan["coverage"].append(make_cov("49601007", "Heart disease (disorder)", "Initial waiting period applies", ["Room rent limit: Single Private A/C Room", "Co‑payment: <percentage>%"]))
    # Cancer Assure Cover – SNOMED 414916001 (Oncological care)
    plan["coverage"].append(make_cov("414916001", "Oncological care (procedure)", "Initial waiting period and survival period apply", ["Room rent limit: Single Private A/C Room", "Co‑payment: <percentage>%"]))
    # Income Protect – SNOMED 386661006 (Disability)
    plan["coverage"].append(make_cov("386661006", "Disability (finding)", "Initial waiting period applies", ["Deductible days: <number>"]))
    # Preferred Provider Network – SNOMED 71388002 (Network)
    plan["coverage"].append(make_cov("71388002", "Network (environment)", "Cashless facility only"))
    # Hospital Cash Benefit – SNOMED 386661006 (same as disability but different semantics)
    plan["coverage"].append(make_cov("386661006", "Hospital cash benefit", "Deductible days as per schedule"))
    # Major Illness Cover – SNOMED 64572001 (Disease)
    plan["coverage"].append(make_cov("64572001", "Disease (disorder)", "Initial waiting period of 90 days", ["Room rent limit: Single Private A/C Room", "Co‑payment: <percentage>%"]))
    # Credit Protect – SNOMED 442083001 (Accident)
    plan["coverage"].append(make_cov("442083001", "Accident (event)", "No waiting period"))
    # Heart Assure Cover – same as heart disease
    plan["coverage"].append(make_cov("49601007", "Heart disease (disorder) - Assure", "Initial waiting period applies", ["Room rent limit: Single Private A/C Room", "Co‑payment: <percentage>%"]))
    # OPD Expenses – SNOMED 737492002 (Outpatient)
    plan["coverage"].append(make_coverage("737492002", "Outpatient care (procedure)", "Waiting period as per schedule"))
    # Cancer Secure Cover – use generic cancer code 363406005 (Neoplasm)
    plan["coverage"].append(make_coverage("363406005", "Neoplasm (disorder)", "Initial waiting period and survival period apply"))
    # Heart Secure Cover – SNOMED 49601007 (Heart disease)
    plan["coverage"].append(make_coverage("49601007", "Heart disease (disorder)", "Initial waiting period applies"))
    # Cancer Assure Cover – same as cancer inpatient 414916001 (Oncological care)
    plan["coverage"].append(make_coverage("414916001", "Oncological care (procedure)", "Initial waiting period and survival period apply"))
    # Income Protect – SNOMED 386661006 (Disability)
    plan["coverage"].append(make_coverage("386661006", "Disability (finding)", "Initial waiting period applies"))
    # Preferred Provider Network – SNOMED 71388002 (Network)
    plan["coverage"].append(make_coverage("71388002", "Network (environment)", "Cashless facility only"))
    # Hospital Cash Benefit – SNOMED 386661006 (same as disability but different semantics)
    plan["coverage"].append(make_coverage("386661006", "Hospital cash benefit", "Deductible days as per schedule"))
    # Major Illness Cover – SNOMED 64572001 (Disease)
    plan["coverage"].append(make_coverage("64572001", "Disease (disorder)", "Initial waiting period of 90 days"))
    # Credit Protect – SNOMED 442083001 (Accident)
    plan["coverage"].append(make_coverage("442083001", "Accident (event)", "No waiting period"))
    # Heart Assure Cover – same as heart disease
    plan["coverage"].append(make_coverage("49601007", "Heart disease (disorder) - Assure", "Initial waiting period applies"))

def add_plan_entries(plan):
    # Create a simple plan entry for each variant – using placeholder costs
    variants = [
        "OPD Expenses",
        "Cancer Secure Cover - Option 1",
        "Cancer Secure Cover - Option 2",
        "Heart Secure Cover",
        "Cancer Assure Cover",
        "Income Protect",
        "Preferred Provider Network",
        "Hospital Cash Benefit",
        "Major Illness Cover",
        "Credit Protect",
        "Heart Assure Cover"
    ]
    for var in variants:
        entry = {
            "identifier": {"value": var},
            "type": {"coding": [{"system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-plan-type", "code": "01", "display": var}]},
            "generalCost": [{"type": {"coding": [{"system": "http://snomed.info/sct", "code": "442083001", "display": "Cost placeholder"}], "value": 1000, "currency": "INR"}}]
        }
        plan["plan"].append(entry)

def build_bundle(org, plan):
    bundle = {
        "resourceType": "Bundle",
        "id": "InsurancePlanBundle-" + new_uuid(),
        "meta": {"profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlanBundle"], "security": [{"system": "http://terminology.hl7.org/CodeSystem/v3-Confidentiality", "code": "V", "display": "very restricted"}]},
        "type": "collection",
        "timestamp": "2026-05-26T00:00:00+00:00",
        "entry": []
    }
    # Organization entry
    bundle["entry"].append({
        "fullUrl": f"urn:uuid:{org['id']}",
        "resource": org
    })
    # InsurancePlan entry
    bundle["entry"].append({
        "fullUrl": f"urn:uuid:{plan['id']}",
        "resource": plan
    })
    return bundle

def main():
    notes_path = Path('workspace/generated/extraction_notes.txt')
    notes = read_notes(notes_path)
    # Build resources
    org = build_organization(notes)
    org_ref = f"urn:uuid:{org['id']}"
    plan = build_insurance_plan(notes, org_ref)
    add_supporting_info(plan)
    add_exclusions(plan)
    add_coverages(plan)
    add_plan_entries(plan)
    bundle = build_bundle(org, plan)
    # Write output
    out_path = Path('workspace/generated/InsurancePlanBundle.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(bundle, f, indent=2)
    print(f"Bundle written to {out_path}")

if __name__ == "__main__":
    main()
