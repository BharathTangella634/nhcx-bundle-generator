import json
import uuid
from datetime import datetime

# Constants
INSURER_NAME = "Aditya Birla Health Insurance Co. Limited"
INSURER_ADDRESS = "9th Floor, Tower 1, One Indiabulls Centre, Jupiter Mills Compound, 841, Senapati Bapat Marg, Elphinstone Road, Mumbai 400013"
INSURER_WEBSITE = "https://www.adityabirlacapital.com/healthinsurance"
POLICY_NAME = "Group Protect"
POLICY_UIN = "ADIHLGP22023V032122"
POLICY_TYPE = "Package Policy (covering more than one type of health above)"
POLICY_PERIOD_START = "2023-01-01"
POLICY_PERIOD_END = "2025-12-31"

# SNOMED CT codes for coverage types
SNOMED_OUTPATIENT = "737492002"
SNOMED_INPATIENT = "737481003"
SNOMED_DAYCARE = "737850002"
SNOMED_ORGAN_DONOR = "105461009"
SNOMED_AMBULANCE = "49122002"
SNOMED_INTENSIVE_CARE = "309904001"
SNOMED_SINGLE_ROOM = "224663004"
SNOMED_HOME_CARE = "60689008"
SNOMED_PRE_HOSPITAL = "409972000"
SNOMED_POST_HOSPITAL = "710967003"

# NHCX Code Systems
NDHM_INSURANCE_PLAN_TYPE = "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-insuranceplan-type"
NDHM_PLAN_TYPE = "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-plan-type"
NDHM_SUPPORTING_INFO = "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-supportinginfo-category"
NDHM_CLAIM_EXCLUSION = "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion"
NDHM_IDENTIFIER_TYPE = "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code"
HL7_V2_IDENTIFIER = "http://terminology.hl7.org/CodeSystem/v2-0203"
HL7_CONFIDENTIALITY = "http://terminology.hl7.org/CodeSystem/v3-Confidentiality"

# NHCX Extension URLs
CLAIM_SUPPORTING_INFO_REQ = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-SupportingInfoRequirement"
CLAIM_EXCLUSION = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Exclusion"
CLAIM_CONDITION = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Condition"

# NHCX Exclusion Codes
EXCLUSION_CODES = {
    "Excl01": "Pre-Existing Diseases",
    "Excl02": "Certain specified diseases/procedures require Waiting Period",
    "Excl03": "30 Day Waiting Period",
    "Excl04": "Investigation & Treatment for Infertility/Subfertility",
    "Excl05": "Maternity Expenses",
    "Excl06": "External Congenital Defect",
    "Excl07": "Breach of law with criminal intent",
    "Excl08": "Excluded Providers",
    "Excl09": "Treatment for Alcoholism, drug/substance abuse",
    "Excl10": "Unproven/Experimental Treatment",
    "Excl11": "Cosmetic or Plastic Surgery",
    "Excl12": "Hazardous or Adventure Sports",
    "Excl13": "Self inflicted injuries or suicide attempt",
    "Excl14": "Injury/disease directly from War or nuclear contamination",
    "Excl15": "Service in Armed Forces",
    "Excl16": "Naturopathy Treatments",
    "Excl17": "Obesity and Weight Management",
    "Excl18": "Change of Gender Treatment"
}

# Supporting Info Requirements
SUPPORTING_INFO_REQUIREMENTS = [
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-supportinginfo-category",
                    "code": "FCF",
                    "display": "Filled claim form"
                }
            ]
        },
        "code": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code",
                    "code": "ADN",
                    "display": "Adhaar number"
                }
            ]
        }
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-supportinginfo-category",
                    "code": "POI",
                    "display": "Proof of identity"
                }
            ]
        },
        "code": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code",
                    "code": "ADN",
                    "display": "Adhaar number"
                }
            ]
        }
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-supportinginfo-category",
                    "code": "POA",
                    "display": "Proof of address"
                }
            ]
        },
        "code": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                    "code": "PPN",
                    "display": "Passport number"
                }
            ]
        }
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-supportinginfo-category",
                    "code": "MB",
                    "display": "Medical bill"
                }
            ]
        },
        "code": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code",
                    "code": "ADN",
                    "display": "Adhaar number"
                }
            ]
        }
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-supportinginfo-category",
                    "code": "DIA",
                    "display": "Diagnostic report"
                }
            ]
        },
        "code": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code",
                    "code": "ADN",
                    "display": "Adhaar number"
                }
            ]
        }
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-supportinginfo-category",
                    "code": "HDS",
                    "display": "Hospital discharge summary"
                }
            ]
        },
        "code": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code",
                    "code": "ADN",
                    "display": "Adhaar number"
                }
            ]
        }
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-supportinginfo-category",
                    "code": "CD",
                    "display": "Clinical document"
                }
            ]
        },
        "code": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code",
                    "code": "ADN",
                    "display": "Adhaar number"
                }
            ]
        }
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-supportinginfo-category",
                    "code": "ATT",
                    "display": "Attachment"
                }
            ]
        },
        "code": {
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

# Claim Exclusions
CLAIM_EXCLUSIONS = [
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "Excl01",
                    "display": "Pre-Existing Diseases"
                }
            ]
        },
        "statement": "Expenses for pre-existing Disease (PED) excluded until expiry of specified months of continuous coverage. Enhancement of Sum Insured applies exclusion afresh. Portability reduces waiting period. Coverage after expiry subject to declaration and acceptance."
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "Excl02",
                    "display": "Specified disease/procedure waiting period"
                }
            ]
        },
        "statement": "24-month waiting period for listed conditions/surgeries. Not applicable for accident claims. Enhancement applies afresh. Longer of PED or specified disease waiting period applies.",
        "item": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": "86077009",
                    "display": "Operation for glaucoma"
                }
            ]
        }
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "Excl03",
                    "display": "30-day waiting period"
                }
            ]
        },
        "statement": "Treatment within first 30 days from policy commencement excluded except accident claims. Not applicable if Continuous Coverage >12 months. Applies to enhanced Sum Insured."
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "Excl04",
                    "display": "Investigation Evaluation"
                }
            ]
        },
        "statement": "Admission primarily for diagnostics/evaluation only is excluded.",
        "statement": "Diagnostic expenses not related to current diagnosis/treatment are excluded."
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "Excl05",
                    "display": "Rest Cure,Rehabilitation and Respite Care"
                }
            ]
        },
        "statement": "Admission primarily for enforced bed rest is excluded. Includes custodial care at home/nursing facility, services for terminally ill.",
        "statement": "Convalescence, cure, sanatorium treatment, private duty nursing are excluded."
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "Excl06",
                    "display": "Obesity/Weight Control"
                }
            ]
        },
        "statement": "Surgical treatment of obesity not meeting all conditions: (1) advised by Doctor, (2) supported by clinical protocols, (3) age 18+, (4) BMI >=40 or BMI >=35 with severe co-morbidities (obesity-related cardiomyopathy, coronary heart disease, severe sleep apnea, uncontrolled Type2 Diabetes)."
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "Excl07",
                    "display": "Change-of-Gender treatments"
                }
            ]
        },
        "statement": "Any treatment to change body characteristics to opposite sex is excluded."
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "Excl08",
                    "display": "Cosmetic or plastic Surgery"
                }
            ]
        },
        "statement": "Cosmetic or plastic Surgery is excluded unless for reconstruction following Accident, Burns or Cancer, or medically necessary to remove direct and immediate health risk."
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "Excl09",
                    "display": "Hazardous or Adventure sports"
                }
            ]
        },
        "statement": "Para-jumping, rock climbing, mountaineering, rafting, motor racing, horse racing, scuba diving, hang gliding, sky diving, deep-sea diving are excluded."
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "Excl10",
                    "display": "Breach of law"
                }
            ]
        },
        "statement": "Treatment from committing or attempting breach of law with criminal intent is excluded."
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "Excl11",
                    "display": "Excluded Providers"
                }
            ]
        },
        "statement": "Treatment at specifically excluded hospitals/providers as per Annexure C. Life-threatening/accident expenses payable up to stabilization."
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "Excl12",
                    "display": "Rehabilitation"
                }
            ]
        },
        "statement": "Treatment for Alcoholism, drug or substance abuse or addictive conditions and consequences."
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "Excl13",
                    "display": "Hydrotherapy"
                }
            ]
        },
        "statement": "Treatments at health hydros, nature cure clinics, spas or similar establishments are excluded."
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "Excl14",
                    "display": "Non-prescription"
                }
            ]
        },
        "statement": "Dietary supplements/substances purchasable without prescription (vitamins, minerals, organic substances) unless prescribed as part of hospitalization/day care."
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "Excl15",
                    "display": "Refractive Error"
                }
            ]
        },
        "statement": "Correction of eye sight due to refractive error less than 7.5 dioptres."
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "Excl16",
                    "display": "Unproven Treatments"
                }
            ]
        },
        "statement": "Treatments, procedures or supplies lacking significant medical documentation are excluded.",
        "statement": "Experimental treatment, investigational treatments, devices and pharmacological regimens are excluded."
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "Excl17",
                    "display": "Sterility and Infertility"
                }
            ]
        },
        "statement": "Contraception, sterilization, assisted reproduction (IVF, ZIFT, GIFT, ICSI), gestational surrogacy, reversal of sterilization are excluded."
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "Excl18",
                    "display": "Maternity Expenses"
                }
            ]
        },
        "statement": "Childbirth expenses (including complicated/caesarean deliveries) except ectopic pregnancy are excluded. Miscarriage expenses (unless due to accident) and lawful medical termination are excluded."
    },
    {
        "category": {
            "coding": [
                {
                    "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion",
                    "code": "IIB20",
                    "display": "Not covered under the Terms and Conditions of the contract"
                }
            ]
        },
        "statement": "War, invasion, act of foreign enemy, war-like operations, civil war, rebellion, nuclear weapons/materials, chemical/biological weapons, ionizing radiation, radioactive contamination.",
        "statement": "Willful or deliberate exposure to danger, intentional self-injury, non-adherence to Medical Advice, participation in naval/military/air force operations.",
        "statement": "Illness/Injury/Accident due to abuse of intoxicants, smoking cessation programs, nicotine addiction treatment (unless prescribed by Medical Practitioner).",
        "statement": "All routine examinations and preventive health check-ups.",
        "statement": "Circumcisions (unless necessitated by Illness or Injury).",
        "statement": "Non-allopathic treatment unless mentioned as part of inclusions in Policy Schedule.",
        "statement": "Conditions treatable on out-patient basis without Hospitalization (not applicable for OPD Expenses).",
        "statement": "Preventive care, vaccination/inoculation/immunizations (except post-bite treatment).",
        "statement": "Admission for nutritional and electrolyte supplements unless certified by Medical Practitioner as direct consequence of covered claim.",
        "statement": "Hearing aids, spectacles, contact lenses including optometric therapy, multifocal lens.",
        "statement": "Treatment for alopecia, baldness, wigs, toupees and related treatments.",
        "statement": "Medical supplies including elastic stockings, diabetic test strips, and products specified in Annexure B - Non Medical Expenses.",
        "statement": "Prosthesis, corrective devices, external durable medical equipment (wheelchairs, crutches, sleep apnea instruments, CAPD equipment, oxygen concentrator, cochlear implants unless accident). External appliances for diagnosis/treatment.",
        "statement": "Parkinson disease, general debility/exhaustion, sleep-apnea, stress.",
        "statement": "External Congenital Anomalies, diseases or defects.",
        "statement": "Stem cell therapy (except Hematopoietic stem cells for bone marrow transplant for haematological conditions), Growth hormone therapy, Hormone Replacement Therapy.",
        "statement": "Expenses for organ donor screening (except as provided for donor treatment in transplant Surgery).",
        "statement": "Organ Transplant not compliant under Transplantation of Human Organs Act, 1994.",
        "statement": "Spinal subluxation analysis/treatment, skeletal manipulation, muscle stimulation (except treatment of fractures excluding hairline fractures and dislocations).",
        "statement": "Dentures, artificial teeth, Dental Treatment/Surgery (unless requiring Hospitalization due to Accident).",
        "statement": "Health check-ups, medical certificates, examinations for employment/travel.",
        "statement": "RFQMR, ECP, EECP, Hyperbaric Oxygen Therapy, KTP Laser Surgeries, cyber knife, Femto laser, bioabsorbable stents/valves/implants, Infliximab, rituximab, avastin, lucentis and similar molecules.",
        "statement": "Medically unnecessary items of personal comfort/convenience (television, telephone, food/cosmetics/hygiene articles, barber/beauty/guest services, vitamins/tonics unless certified). Non-Medical Expenses as specified in Annexure B.",
        "statement": "Treatment from unregistered Medical Practitioner.",
        "statement": "Treatment charges by Medical Practitioner acting outside scope of license/registration.",
        "statement": "Treatment by Medical Practitioner who is family member or stays in same residence (unless pre-approved).",
        "statement": "Unreasonable charges, non-Medically Necessary Treatment, drugs/treatments without prescription.",
        "statement": "Hospital stay charges not expressly mentioned as covered (admission, discharge, administration, registration, documentation, MRD charges).",
        "statement": "Treatment taken outside India.",
        "statement": "Death within stipulated survival period specified in Policy Schedule.",
        "statement": "Use of Radio Frequency probe for ablation unless specifically pre-approved in writing.",
        "statement": "Existing diseases disclosed by insured and mentioned in policy schedule with specified ICD codes excluded (based on insured's consent)."
    }
]

# Coverage data
COVERAGES = [
    {
        "type": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": SNOMED_OUTPATIENT,
                    "display": "Outpatient care management (procedure)"
                }
            ]
        },
        "benefit": [
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://snomed.info/sct",
                            "code": SNOMED_OUTPATIENT,
                            "display": "Outpatient care management (procedure)"
                        }
                    ]
                },
                "requirement": "Reasonable and Customary Charges incurred towards medically required consultations, visit(s) to a doctor (Medical Practitioner holding minimum qualification of MBBS), E-consultation and/or Tele-consultation for specialties listed in Policy Schedule, within Network only if opted, on out-patient basis up to limits specified in Policy Schedule / Certificate of Insurance."
            }
        ],
        "extension": [
            {
                "extension": [
                    {
                        "url": "claim-condition",
                        "valueString": "Covers Reasonable and Customary Charges for Medically Necessary Treatment during Policy Period for Illness/Injury contracted or sustained during Policy Period."
                    },
                    {
                        "url": "claim-condition",
                        "valueString": "Claims for amounts exceeding limits in Policy Schedule are not covered."
                    },
                    {
                        "url": "claim-condition",
                        "valueString": "In-patient treatment and day care procedures are excluded from OPD."
                    },
                    {
                        "url": "claim-condition",
                        "valueString": "Naturopathy treatment(s) are excluded from OPD."
                    },
                    {
                        "url": "claim-condition",
                        "valueString": "CT and MRI are excluded from OPD."
                    }
                ],
                "url": CLAIM_CONDITION
            }
        ]
    },
    {
        "type": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": SNOMED_INPATIENT,
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
                            "code": SNOMED_INPATIENT,
                            "display": "Inpatient care management (procedure)"
                        }
                    ]
                }
            }
        ]
    },
    {
        "type": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": SNOMED_DAYCARE,
                    "display": "Day care case management"
                }
            ]
        },
        "benefit": [
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://snomed.info/sct",
                            "code": SNOMED_DAYCARE,
                            "display": "Day care case management"
                        }
                    ]
                }
            }
        ]
    },
    {
        "type": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": SNOMED_ORGAN_DONOR,
                    "display": "Organ donor"
                }
            ]
        },
        "benefit": [
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://snomed.info/sct",
                            "code": SNOMED_ORGAN_DONOR,
                            "display": "Organ donor"
                        }
                    ]
                }
            }
        ]
    },
    {
        "type": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": SNOMED_AMBULANCE,
                    "display": "Ambulance, device (physical object)"
                }
            ]
        },
        "benefit": [
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://snomed.info/sct",
                            "code": SNOMED_AMBULANCE,
                            "display": "Ambulance, device (physical object)"
                        }
                    ]
                }
            }
        ]
    },
    {
        "type": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": SNOMED_INTENSIVE_CARE,
                    "display": "Intensive care unit (environment)"
                }
            ]
        },
        "benefit": [
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://snomed.info/sct",
                            "code": SNOMED_INTENSIVE_CARE,
                            "display": "Intensive care unit (environment)"
                        }
                    ]
                }
            }
        ]
    },
    {
        "type": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": SNOMED_SINGLE_ROOM,
                    "display": "Single room (environment)"
                }
            ]
        },
        "benefit": [
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://snomed.info/sct",
                            "code": SNOMED_SINGLE_ROOM,
                            "display": "Single room (environment)"
                        }
                    ]
                }
            }
        ]
    },
    {
        "type": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": SNOMED_HOME_CARE,
                    "display": "Home care of patient"
                }
            ]
        },
        "benefit": [
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://snomed.info/sct",
                            "code": SNOMED_HOME_CARE,
                            "display": "Home care of patient"
                        }
                    ]
                }
            }
        ]
    },
    {
        "type": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": SNOMED_PRE_HOSPITAL,
                    "display": "Pre-hospital care (situation)"
                }
            ]
        },
        "benefit": [
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://snomed.info/sct",
                            "code": SNOMED_PRE_HOSPITAL,
                            "display": "Pre-hospital care (situation)"
                        }
                    ]
                }
            }
        ]
    },
    {
        "type": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": SNOMED_POST_HOSPITAL,
                    "display": "Management of health status after discharge from hospital (procedure)"
                }
            ]
        },
        "benefit": [
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://snomed.info/sct",
                            "code": SNOMED_POST_HOSPITAL,
                            "display": "Management of health status after discharge from hospital (procedure)"
                        }
                    ]
                }
            }
        ]
    }
]

# Plan variants (simplified)
PLAN_VARIANTS = [
    {
        "identifier": [
            {
                "use": "official",
                "value": "Option 1 - Cancer Secure Cover"
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
                    "value": 1000000,
                    "currency": "INR"
                }
            }
        ]
    },
    {
        "identifier": [
            {
                "use": "official",
                "value": "Option 2 - Cancer Secure Cover"
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
                    "value": 1500000,
                    "currency": "INR"
                }
            }
        ]
    },
    {
        "identifier": [
            {
                "use": "official",
                "value": "Option 1 - Heart Secure Cover"
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
                    "value": 1000000,
                    "currency": "INR"
                }
            }
        ]
    },
    {
        "identifier": [
            {
                "use": "official",
                "value": "Option 2 - Heart Secure Cover"
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
                    "value": 1500000,
                    "currency": "INR"
                }
            }
        ]
    },
    {
        "identifier": [
            {
                "use": "official",
                "value": "Option 3 - Heart Secure Cover"
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
                    "value": 2000000,
                    "currency": "INR"
                }
            }
        ]
    },
    {
        "identifier": [
            {
                "use": "official",
                "value": "Option 4 - Heart Secure Cover"
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
                    "value": 2500000,
                    "currency": "INR"
                }
            }
        ]
    },
    {
        "identifier": [
            {
                "use": "official",
                "value": "Option 5 - Heart Secure Cover"
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
                    "value": 3000000,
                    "currency": "INR"
                }
            }
        ]
    },
    {
        "identifier": [
            {
                "use": "official",
                "value": "Option 6 - Heart Secure Cover"
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
                    "value": 3500000,
                    "currency": "INR"
                }
            }
        ]
    },
    {
        "identifier": [
            {
                "use": "official",
                "value": "Option 7 - Heart Secure Cover"
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
                    "value": 4000000,
                    "currency": "INR"
                }
            }
        ]
    },
    {
        "identifier": [
            {
                "use": "official",
                "value": "Option 8 - Heart Secure Cover"
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
                    "value": 5000000,
                    "currency": "INR"
                }
            }
        ]
    }
]

def generate_bundle():
    # Generate UUIDs
    bundle_uuid = str(uuid.uuid4())
    insurer_uuid = str(uuid.uuid4())
    insurance_plan_uuid = str(uuid.uuid4())

    # Create the bundle
    bundle = {
        "resourceType": "Bundle",
        "id": bundle_uuid,
        "meta": {
            "versionId": "1",
            "profile": [
                "https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlanBundle"
            ],
            "security": [
                {
                    "system": HL7_CONFIDENTIALITY,
                    "code": "V",
                    "display": "very restricted"
                }
            ]
        },
        "type": "collection",
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f+00:00"),
        "entry": [
            {
                "fullUrl": f"urn:uuid:{insurance_plan_uuid}",
                "resource": {
                    "resourceType": "InsurancePlan",
                    "id": insurance_plan_uuid,
                    "meta": {
                        "versionId": "1",
                        "profile": [
                            "https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlan"
                        ]
                    },
                    "text": {
                        "status": "extensions",
                        "div": f"<div xmlns=\"http://www.w3.org/1999/xhtml\"><p><b>{POLICY_NAME}</b> - {INSURER_NAME}</p><p>Product UIN: {POLICY_UIN}</p><p>Status: active</p><p>Type: {POLICY_TYPE}</p><p>Period: {POLICY_PERIOD_START} to {POLICY_PERIOD_END}</p><p>Coverages: OPD Expenses, Cancer Secure Cover, Income Protect, Preferred Provider Network, Heart Secure Cover, Cancer Assure Cover, Hospital Cash Benefit, Major Illnesses Cover, Credit Protect, Heart Assure Cover</p></div>"
                    },
                    "extension": [
                        # Supporting Info Requirements
                        *[
                            {
                                "extension": [
                                    {
                                        "url": "category",
                                        "valueCodeableConcept": req["category"]
                                    },
                                    {
                                        "url": "code",
                                        "valueCodeableConcept": req["code"]
                                    }
                                ],
                                "url": CLAIM_SUPPORTING_INFO_REQ
                            }
                            for req in SUPPORTING_INFO_REQUIREMENTS
                        ],
                        # Claim Exclusions
                        *[
                            {
                                "extension": [
                                    {
                                        "url": "category",
                                        "valueCodeableConcept": excl["category"]
                                    },
                                    {
                                        "url": "statement",
                                        "valueString": excl["statement"]
                                    },
                                    *(
                                        [
                                            {
                                                "url": "item",
                                                "valueCodeableConcept": excl["item"]
                                            }
                                        ]
                                        if "item" in excl else []
                                    )
                                ],
                                "url": CLAIM_EXCLUSION
                            }
                            for excl in CLAIM_EXCLUSIONS
                        ]
                    ],
                    "identifier": [
                        {
                            "system": "https://irdai.gov.in",
                            "value": POLICY_UIN
                        }
                    ],
                    "status": "active",
                    "type": [
                        {
                            "coding": [
                                {
                                    "system": NDHM_INSURANCE_PLAN_TYPE,
                                    "code": "09",
                                    "display": POLICY_TYPE
                                }
                            ]
                        }
                    ],
                    "name": POLICY_NAME,
                    "period": {
                        "start": POLICY_PERIOD_START,
                        "end": POLICY_PERIOD_END
                    },
                    "ownedBy": {
                        "reference": f"urn:uuid:{insurer_uuid}"
                    },
                    "administeredBy": {
                        "reference": f"urn:uuid:{insurer_uuid}"
                    },
                    "coverage": COVERAGES,
                    "plan": PLAN_VARIANTS
                }
            },
            {
                "fullUrl": f"urn:uuid:{insurer_uuid}",
                "resource": {
                    "resourceType": "Organization",
                    "id": insurer_uuid,
                    "meta": {
                        "profile": [
                            "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Organization"
                        ]
                    },
                    "text": {
                        "status": "generated",
                        "div": f"<div xmlns=\"http://www.w3.org/1999/xhtml\"><a name=\"Organization_{insurer_uuid}\"> </a><p class=\"res-header-id\"><b>Generated Narrative: Organization {insurer_uuid}</b></p><p><b>identifier</b>: Registry of Hospitals in Network of Insurance (ROHINI) ID/4567878</p><p><b>name</b>: {INSURER_NAME}</p><p><b>telecom</b>: <a href=\"tel:+9124326341234\">+91 243 2634 1234</a>, <a href=\"mailto:contact@labs.xyz.org\">contact@labs.xyz.org</a></p></div>"
                    },
                    "identifier": [
                        {
                            "type": {
                                "coding": [
                                    {
                                        "system": NDHM_IDENTIFIER_TYPE,
                                        "code": "ROHINI",
                                        "display": "Registry of Hospitals in Network of Insurance (ROHINI) ID"
                                    }
                                ]
                            },
                            "system": "https://rohini.iib.gov.in/",
                            "value": "4567878"
                        }
                    ],
                    "name": INSURER_NAME
                }
            }
        ]
    }

    return bundle

if __name__ == "__main__":
    bundle = generate_bundle()
    with open('/mnt/8b4bbd12-99b7-4ef1-9218-be56afd51a3d/nhcx-bundle-generator/workspace/generated/InsurancePlanBundle.json', 'w') as f:
        json.dump(bundle, f, indent=2)
    print("Bundle generated successfully.")