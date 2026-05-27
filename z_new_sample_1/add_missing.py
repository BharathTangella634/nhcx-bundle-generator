import json
import pathlib

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
BUNDLE_PATH = r"/mnt/8b4bbd12-99b7-4ef1-9218-be56afd51a3d/nhcx-bundle-generator/workspace/generated/InsurancePlanBundle.json"

# ----------------------------------------------------------------------
# Helper to create a ClaimSupportingInfoRequirement extension
# ----------------------------------------------------------------------
def make_supporting_info_ext(category_code: str, category_display: str,
                             code_system: str, code_code: str, code_display: str):
    """Return a dict representing a ClaimSupportingInfoRequirement extension."""
    return {
        "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-SupportingInfoRequirement",
        "extension": [
            {
                "url": "category",
                "valueCodeableConcept": {
                    "coding": [
                        {
                            "system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-supportinginfo-category",
                            "code": category_code,
                            "display": category_display
                        }
                    ]
                }
            },
            {
                "url": "code",
                "valueCodeableConcept": {
                    "coding": [
                        {
                            "system": code_system,
                            "code": code_code,
                            "display": code_display
                        }
                    ]
                }
            }
        ]
    }

# ----------------------------------------------------------------------
# Load the existing bundle
# ----------------------------------------------------------------------
bundle_path = pathlib.Path(BUNDLE_PATH)
bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

# ----------------------------------------------------------------------
# Locate the InsurancePlan resource inside the bundle
# ----------------------------------------------------------------------
insurance_plan = None
for entry in bundle.get("entry", []):
    resource = entry.get("resource", {})
    if resource.get("resourceType") == "InsurancePlan":
        insurance_plan = resource
        break

if insurance_plan is None:
    raise RuntimeError("No InsurancePlan resource found in the bundle.")

# ----------------------------------------------------------------------
# Prepare the missing extensions (if they are not already present)
# ----------------------------------------------------------------------
new_extensions = [
    # Proof of Identity – Aadhaar Number
    make_supporting_info_ext(
        category_code="POI",
        category_display="Proof of identity",
        code_system="https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code",
        code_code="ADN",
        code_display="Adhaar number"
    ),
    # Proof of Address – Passport Number
    make_supporting_info_ext(
        category_code="POA",
        category_display="Proof of address",
        code_system="http://terminology.hl7.org/CodeSystem/v2-0203",
        code_code="PPN",
        code_display="Passport number"
    )
]

# Ensure the InsurancePlan has an 'extension' list
extensions = insurance_plan.setdefault("extension", [])

# Helper to decide if an extension already exists (by url + category code)
def ext_exists(ext_list, candidate):
    cand_cat = next(
        (c["valueCodeableConcept"]["coding"][0]["code"]
         for c in candidate["extension"] if c["url"] == "category"),
        None,
    )
    for existing in ext_list:
        if existing.get("url") != candidate["url"]:
            continue
        exist_cat = next(
            (c["valueCodeableConcept"]["coding"][0]["code"]
             for c in existing.get("extension", []) if c["url"] == "category"),
            None,
        )
        if exist_cat == cand_cat:
            return True
    return False

# Append only those that are truly missing
for ext in new_extensions:
    if not ext_exists(extensions, ext):
        extensions.append(ext)

# ----------------------------------------------------------------------
# Write the updated bundle back to the same file
# ----------------------------------------------------------------------
bundle_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")