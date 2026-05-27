import json
import uuid
from datetime import datetime

# Read the extraction notes
with open('workspace/generated/extraction_notes.txt', 'r') as f:
    extraction_notes = f.read()

# Parse the extraction notes to extract key information
lines = extraction_notes.split('\n')
data = {}

# Extract insurer details
for i, line in enumerate(lines):
    if line.startswith("## Insurer Details"):
        i += 1
        while i < len(lines) and not lines[i].startswith("## "):
            if ": " in lines[i]:
                key, value = lines[i].split(": ", 1)
                data[key.strip()] = value.strip()
            i += 1
        break

# Extract policy details
for i, line in enumerate(lines):
    if line.startswith("## Policy Details"):
        i += 1
        while i < len(lines) and not lines[i].startswith("## "):
            if ": " in lines[i]:
                key, value = lines[i].split(": ", 1)
                data[key.strip()] = value.strip()
            i += 1
        break

# Extract coverage sections
coverage_start = False
current_section = ""
for i, line in enumerate(lines):
    if line.startswith("## Coverage Sections"):
        coverage_start = True
        continue
    elif coverage_start and line.startswith("## "):
        break
    elif coverage_start:
        if line.startswith("- "):
            if "Hospitalization Benefit" in line:
                current_section = "Hospitalization Benefit"
                data[current_section] = []
            elif "Diagnosis Cover" in line:
                current_section = "Diagnosis Cover"
                data[current_section] = []
            elif line.startswith("- "):
                if current_section:
                    data[current_section].append(line[2:].strip())

# Extract exclusions
exclusions_start = False
data["Exclusions"] = []
for i, line in enumerate(lines):
    if line.startswith("## Exclusions"):
        exclusions_start = True
        continue
    elif exclusions_start and line.startswith("## "):
        break
    elif exclusions_start and line.strip():
        data["Exclusions"].append(line.strip())

# Extract waiting periods
waiting_periods_start = False
data["Waiting Periods"] = []
for i, line in enumerate(lines):
    if line.startswith("## Waiting Periods"):
        waiting_periods_start = True
        continue
    elif waiting_periods_start and line.startswith("## "):
        break
    elif waiting_periods_start and line.strip():
        data["Waiting Periods"].append(line.strip())

# Extract claim procedure
claim_procedure_start = False
data["Claim Procedure"] = []
for i, line in enumerate(lines):
    if line.startswith("## Claim Procedure"):
        claim_procedure_start = True
        continue
    elif claim_procedure_start and line.startswith("## "):
        break
    elif claim_procedure_start and line.strip():
        data["Claim Procedure"].append(line.strip())

# Extract general terms and conditions
gterms_start = False
data["General Terms & Conditions"] = []
for i, line in enumerate(lines):
    if line.startswith("## General Terms & Conditions"):
        gterms_start = True
        continue
    elif gterms_start and line.startswith("## "):
        break
    elif gterms_start and line.strip():
        data["General Terms & Conditions"].append(line.strip())

# Extract grievance redressal
grievance_start = False
data["Grievance Redressal"] = []
for i, line in enumerate(lines):
    if line.startswith("## Grievance Redressal"):
        grievance_start = True
        continue
    elif grievance_start and line.startswith("## "):
        break
    elif grievance_start and line.strip():
        data["Grievance Redressal"].append(line.strip())

# Extract additional information
additional_start = False
data["Additional Information"] = []
for i, line in enumerate(lines):
    if line.startswith("## Additional Information"):
        additional_start = True
        continue
    elif additional_start and line.startswith("## "):
        break
    elif additional_start and line.strip():
        data["Additional Information"].append(line.strip())

# Create the bundle structure
bundle = {
    "resourceType": "Bundle",
    "id": "InsurancePlanBundle-UniversalSompo",
    "meta": {
        "profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlanBundle"]
    },
    "type": "collection",
    "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f+05:30"),
    "entry": []
}

# Generate proper UUIDs
org_uuid = str(uuid.uuid4())
insurance_plan_uuid = str(uuid.uuid4())

# Create Organization entry
org_entry = {
    "fullUrl": f"urn:uuid:{org_uuid}",
    "resource": {
        "resourceType": "Organization",
        "id": org_uuid,
        "meta": {
            "profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/Organization"]
        },
        "text": {
            "status": "generated",
            "div": "<div xmlns=\"http://www.w3.org/1999/xhtml\"><a name=\"Organization_" + org_uuid + "\"> </a><p class=\"res-header-id\"><b>Generated Narrative: Organization " + org_uuid + "</b></p><p><b>identifier</b>: Registry of Hospitals in Network of Insurance (ROHINI) ID/4567878</p><p><b>name</b>: Universal Sompo General Insurance Co Ltd</p><p><b>telecom</b>: <a href=\"tel:+912227639800\">+91 22 2763 9800</a>, <a href=\"mailto:contactus@universalsompo.com\">contactus@universalsompo.com</a></p></div>"
        },
        "identifier": [
            {
                "type": {
                    "coding": [
                        {
                            "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code",
                            "code": "ROHINI",
                            "display": "Registry of Hospitals in Network of Insurance (ROHINI) ID"
                        }
                    ]
                },
                "system": "https://rohini.iib.gov.in/",
                "value": "4567878"
            }
        ],
        "name": "Universal Sompo General Insurance Co Ltd",
        "telecom": [
            {
                "system": "phone",
                "value": "+91 22 2763 9800",
                "use": "work"
            },
            {
                "system": "email",
                "value": "contactus@universalsompo.com",
                "use": "work"
            }
        ]
    }
}

bundle["entry"].append(org_entry)

# Create InsurancePlan entry
insurance_plan = {
    "fullUrl": f"urn:uuid:{insurance_plan_uuid}",
    "resource": {
        "resourceType": "InsurancePlan",
        "id": insurance_plan_uuid,
        "meta": {
            "profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlan"]
        },
        "text": {
            "status": "extensions",
            "div": "<div xmlns=\"http://www.w3.org/1999/xhtml\"><a name=\"InsurancePlan_" + insurance_plan_uuid + "\"> </a><p class=\"res-header-id\"><b>Generated Narrative: InsurancePlan " + insurance_plan_uuid + "</b></p><p><b>identifier</b>: <code>https://irdai.gov.in</code>/NOTFOUND</p><p><b>status</b>: active</p><p><b>type</b>: <span title=\"Codes:{https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-insuranceplan-type 01}\">Hospitalisation Indemnity Policy</span></p><p><b>name</b>: Group Mashak Rakshak</p><p><b>period</b>: 2023-09-10 --&gt; 2024-09-10</p><p><b>ownedBy</b>: <a href=\"Bundle-InsurancePlanBundle-example-01.html#urn-uuid-ef131456-dc56-4d73-9e88-87d6cb12091e\">Bundle: type = collection; timestamp = 2023-09-11 15:32:26+0530</a></p><p><b>administeredBy</b>: <a href=\"Bundle-InsurancePlanBundle-example-01.html#urn-uuid-ef131456-dc56-4d73-9e88-87d6cb12091e\">Bundle: type = collection; timestamp = 2023-09-11 15:32:26+0530</a></p></div>"
        },
        "identifier": [
            {
                "system": "https://irdai.gov.in",
                "value": "NOTFOUND"
            }
        ],
        "status": "active",
        "type": [
            {
                "coding": [
                    {
                        "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-insuranceplan-type",
                        "code": "01",
                        "display": "Hospitalisation Indemnity Policy"
                    }
                ]
            }
        ],
        "name": "Group Mashak Rakshak",
        "period": {
            "start": "2023-09-10",
            "end": "2024-09-10"
        },
        "ownedBy": {
            "reference": f"urn:uuid:{org_uuid}"
        },
        "administeredBy": {
            "reference": f"urn:uuid:{org_uuid}"
        },
        "coverage": [],
        "plan": []
    }
}

# Add coverage details
# Hospitalization Benefit
hospitalization_coverage = {
    "type": {
        "coding": [
            {
                "system": "http://snomed.info/sct",
                "code": "737481003",
                "display": "Inpatient care management (procedure)"
            }
        ]
    },
    "benefit": [
        {
            "type": {
                "coding": [
                    {
                        "system": "http://snomed.info/sct",
                        "code": "737481003",
                        "display": "Inpatient care management (procedure)"
                    }
                ]
            }
        }
    ]
}

insurance_plan["resource"]["coverage"].append(hospitalization_coverage)

# Diagnosis Cover
diagnosis_coverage = {
    "type": {
        "coding": [
            {
                "system": "http://snomed.info/sct",
                "code": "737492002",
                "display": "Outpatient care management"
            }
        ]
    },
    "benefit": [
        {
            "type": {
                "coding": [
                    {
                        "system": "http://snomed.info/sct",
                        "code": "737492002",
                        "display": "Outpatient care management"
                    }
                ]
            }
        }
    ]
}

insurance_plan["resource"]["coverage"].append(diagnosis_coverage)

# Add plans (individual and floater)
# Individual plan
individual_plan = {
    "identifier": [
        {
            "use": "official",
            "value": "Group Mashak Rakshak - Individual"
        }
    ],
    "type": {
        "coding": [
            {
                "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-plan-type",
                "code": "01",
                "display": "Individual"
            }
        ]
    },
    "generalCost": [
        {
            "cost": {
                "value": 10000,
                "currency": "INR"
            }
        }
    ]
}

# Floater plan
floater_plan = {
    "identifier": [
        {
            "use": "official",
            "value": "Group Mashak Rakshak - Floater"
        }
    ],
    "type": {
        "coding": [
            {
                "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-plan-type",
                "code": "02",
                "display": "Individual Floater"
            }
        ]
    },
    "generalCost": [
        {
            "cost": {
                "value": 200000,
                "currency": "INR"
            }
        }
    ]
}

insurance_plan["resource"]["plan"].extend([individual_plan, floater_plan])

# Add supporting info requirements to the plan level
insurance_plan["resource"]["extension"] = [
    {
        "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-SupportingInfoRequirement",
        "extension": [
            {
                "url": "category",
                "valueCodeableConcept": {
                    "coding": [
                        {
                            "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-supportinginfo-category",
                            "code": "POI",
                            "display": "Proof of identity"
                        }
                    ]
                }
            },
            {
                "url": "code",
                "valueCodeableConcept": {
                    "coding": [
                        {
                            "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code",
                            "code": "ADN",
                            "display": "Adhaar number"
                        }
                    ]
                }
            }
        ]
    },
    {
        "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-SupportingInfoRequirement",
        "extension": [
            {
                "url": "category",
                "valueCodeableConcept": {
                    "coding": [
                        {
                            "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-supportinginfo-category",
                            "code": "POA",
                            "display": "Proof of address"
                        }
                    ]
                }
            },
            {
                "url": "code",
                "valueCodeableConcept": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                            "code": "PPN",
                            "display": "Passport number"
                        }
                    ]
                }
            }
        ]
    }
]

# Add claim exclusions to the plan level
exclusion_codes = {
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
    "Excl18": "Maternity expenses"
}

# Add a few key exclusions to the plan
exclusion_list = [
    {
        "code": "Excl01",
        "statement": "Claim for any illness/disease other than for vector borne diseases covered under the policy."
    },
    {
        "code": "Excl02",
        "statement": "Diagnosis / Treatment outside the geographical limits of India."
    },
    {
        "code": "Excl03",
        "statement": "Any laboratory test not recognized/ approved by the state or central government."
    },
    {
        "code": "Excl04",
        "statement": "Unproven Treatments: Expenses related to any unproven treatment, services and supplies for or in connection with any treatment. Unproven treatments are treatments, procedures or supplies that lack significant medical documentation to support their effectiveness."
    },
    {
        "code": "Excl05",
        "statement": "Domiciliary Hospitalization, Day care OPD treatment."
    },
    {
        "code": "Excl06",
        "statement": "Investigation & Evaluation: Expenses related to any admission primarily for diagnostics and evaluation purposes. Any diagnostic expenses which are not related or not incidental to the current diagnosis and treatment"
    },
    {
        "code": "Excl07",
        "statement": "Rest Cure, rehabilitation and respite care: Expenses related to any admission primarily for enforced bed rest and not for receiving treatment. This also includes: Custodial care either at home or in a nursing facility for personal care such as help with activities of daily living such as bathing, dressing, moving around either by skilled nurses or assistant or non-skilled persons. Any services for people who are terminally ill to address physical, social, emotional and spiritual needs."
    },
    {
        "code": "Excl08",
        "statement": "Excluded Providers: Expenses incurred towards treatment in any hospital or by any Medical Practitioner or any other provider specifically excluded by the Insurer and disclosed in its website / notified to the policyholders are not admissible. However, in case of life threatening situations expenses up to the stage of stabilization are payable but not the complete claim."
    },
    {
        "code": "Excl09",
        "statement": "Treatments received in heath hydros, nature cure clinics, spas or similar establishments or private beds registered as a nursing home attached to such establishments or where admission is arranged wholly or partly for domestic reasons."
    },
    {
        "code": "Excl10",
        "statement": "Dietary supplements and substances that can be purchased without prescription, including but not limited to Vitamins, minerals and organic substances unless prescribed by a medical practitioner as part of hospitalization claim or day care procedure."
    },
    {
        "code": "Excl11",
        "statement": "Hospitalization for treatment other than allopathy."
    },
    {
        "code": "Excl12",
        "statement": "Hospitalization for less than a minimum period of seventy-two (72) consecutive hours."
    }
]

for exclusion in exclusion_list:
    if exclusion["code"] in exclusion_codes:
        insurance_plan["resource"]["extension"].append({
            "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Exclusion",
            "extension": [
                {
                    "url": "category",
                    "valueCodeableConcept": {
                        "coding": [
                            {
                                "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                                "code": exclusion["code"],
                                "display": exclusion_codes[exclusion["code"]]
                            }
                        ]
                    }
                },
                {
                    "url": "statement",
                    "valueString": exclusion["statement"]
                }
            ]
        })

bundle["entry"].append(insurance_plan)

# Write the bundle to file
with open('workspace/generated/InsurancePlanBundle.json', 'w') as f:
    json.dump(bundle, f, indent=2)

print("Bundle generated successfully.")