"""
Multi-Stage Pipeline for NHCX Insurance Plan Bundle Generation
================================================================
Replaces the single monolithic ReAct loop with focused stages:

  Stage 1 — EXTRACT:  PDF markdown → structured extracted_data.json
  Stage 2 — GENERATE: extracted data + rules + example → FHIR bundle JSON
  Stage 3 — VALIDATE & FIX: iterative validator → parse errors → LLM fix → re-validate
  Stage 4 — VERIFY:  compare extracted data vs bundle for completeness

Each stage gets a FRESH LLM context (no pollution across stages).
Data passes between stages via JSON files in workspace/generated/.
"""

import os
import json
import re
import uuid
import copy
import subprocess
import logging
import time
import html
from datetime import datetime

from agent.llm_client import LocalLLMClient

logger = logging.getLogger("nhcx_pipeline")

# ═══════════════════════════════════════════════════════════
# KNOWN SNOMED CT CODES (from NHCX example bundle)
# Only use codes we are CERTAIN about. For anything else,
# use text-only CodeableConcept.
# ═══════════════════════════════════════════════════════════
# Generic SNOMED CT codes for common insurance coverage types.
# The LLM decides which code fits each coverage based on its understanding.
# For product-specific coverage names (e.g., "Cancer Secure", "Heart Assure"),
# the LLM should reason about the closest match dynamically.
SNOMED_LOOKUP = {
    "outpatient": ("737492002", "Outpatient care management (procedure)"),
    "opd": ("737492002", "Outpatient care management (procedure)"),
    "inpatient": ("737481003", "Inpatient care management (procedure)"),
    "hospitalization": ("737481003", "Inpatient care management (procedure)"),
    "surgical": ("387713003", "Surgical procedure (procedure)"),
    "surgery": ("387713003", "Surgical procedure (procedure)"),
    "chemotherapy": ("367336001", "Chemotherapy (procedure)"),
    "post_discharge": ("710967003", "Management of health status after discharge from hospital (procedure)"),
    "post_hospitalization": ("710967003", "Management of health status after discharge from hospital (procedure)"),
    "pre_hospital": ("409972000", "Pre-hospital care (situation)"),
    "pre_hospitalization": ("409972000", "Pre-hospital care (situation)"),
    "ambulance": ("49122002", "Ambulance, device (physical object)"),
    "day_care": ("737850002", "Day care case management"),
    "daycare": ("737850002", "Day care case management"),
    "organ_donor": ("105461009", "Organ donor"),
    "organ_transplant": ("105461009", "Organ donor"),
    "home_care": ("60689008", "Home care of patient"),
    "domiciliary": ("60689008", "Home care of patient"),
    "pharmacy": ("373784001", "Pharmacy service (procedure)"),
    "consultation": ("11429006", "Consultation (procedure)"),
    "screening": ("275926002", "Screening procedure (procedure)"),
    "advance_care": ("713603004", "Advance care planning (procedure)"),
    "diagnostic": ("165340005", "Laboratory test finding (finding)"),
    "maternity": ("118189007", "Prenatal finding (finding)"),
    "newborn": ("133906008", "Newborn care (regime/therapy)"),
}

# NHCX exclusion codes — EXACT display names from the CodeSystem
# Wrong display names cause cascade validation failures!
EXCLUSION_CODE_DISPLAY = {
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

# Insurance plan type display names
INSURANCEPLAN_TYPE_DISPLAY = {
    "01": "Hospitalisation Indemnity Policy",
    "02": "Hospital Cash Plan",
    "03": "Critical Illness Cover",
    "04": "Super Top Up",
    "05": "Personal Accident",
    "06": "Overseas Mediclaim Policy",
    "07": "Micro Insurance Product",
    "08": "Disease Specific Policy",
    "09": "Package Policy (covering more than one type of health above)",
}

# Keyword to exclusion code mapping
EXCLUSION_CODES = {
    "pre-existing": "Excl01",
    "specified_disease": "Excl02",
    "30_day": "Excl03",
    "investigation": "Excl04",
    "rest_cure": "Excl05",
    "obesity": "Excl06",
    "change_of_gender": "Excl07",
    "cosmetic": "Excl08",
    "adventure_sports": "Excl09",
    "breach_of_law": "Excl10",
    "excluded_providers": "Excl11",
    "substance_abuse": "Excl12",
    "hydrotherapy": "Excl13",
    "non_prescription": "Excl14",
    "refractive_error": "Excl15",
    "unproven_treatment": "Excl16",
    "sterility": "Excl17",
    "maternity": "Excl18",
}


class NHCXPipeline:
    """
    Multi-stage pipeline for generating NHCX-compliant FHIR InsurancePlan bundles.

    Each stage uses FRESH LLM conversations — no context pollution.
    Data flows between stages via JSON files.
    """

    def __init__(self, project_root: str, settings: dict):
        self.project_root = os.path.abspath(project_root)
        self.settings = settings
        self.generated_dir = os.path.join(self.project_root, "workspace", "generated")
        self.pdf_dir = os.path.join(self.project_root, "workspace", "pdf")
        self.examples_dir = os.path.join(self.project_root, "workspace", "examples")
        self.rulebooks_dir = os.path.join(self.project_root, "workspace", "rulebooks")
        self.logs_dir = os.path.join(self.project_root, "logs")

        os.makedirs(self.generated_dir, exist_ok=True)
        os.makedirs(self.logs_dir, exist_ok=True)

        llm_cfg = settings.get("llm", {})
        self.llm = LocalLLMClient(
            base_url=llm_cfg.get("base_url", "http://localhost:8090/v1"),
            model=llm_cfg.get("model", "gpt-oss-120b"),
            api_key=llm_cfg.get("api_key", "not-needed"),
            temperature=llm_cfg.get("temperature", 0.2),
            max_tokens=llm_cfg.get("max_tokens", 16384),
            request_timeout=llm_cfg.get("request_timeout", 600),
        )

        self.start_time = None
        self.stage_results = {}
        self.bundle_path = os.path.join(self.generated_dir, "InsurancePlanBundle.json")
        self.extracted_data_path = os.path.join(self.generated_dir, "extracted_data.json")

    # ═══════════════════════════════════════════════════════════
    # MAIN ENTRY POINT
    # ═══════════════════════════════════════════════════════════

    def run(self, pdf_path: str = None) -> dict:
        """Run the full pipeline. Returns status dict."""
        self.start_time = datetime.now()

        md_path = self._find_pdf_markdown(pdf_path)
        if not md_path:
            return {"status": "error", "message": "No PDF markdown found in workspace/pdf/"}

        print(f"\n{'═' * 60}")
        print(f"  NHCX Bundle Generator — Multi-Stage Pipeline")
        print(f"  PDF: {os.path.basename(md_path)}")
        print(f"  Model: {self.settings['llm']['model']}")
        print(f"  Started: {self.start_time.isoformat()}")
        print(f"{'═' * 60}\n")

        if not self.llm.test_connection():
            return {"status": "error", "message": "LLM connection failed"}

        try:
            # ──── STAGE 1: EXTRACT ────
            self._print_stage_header(1, "EXTRACT DATA FROM PDF")
            self.stage_extract(md_path)
            self.stage_results["extract"] = "complete"

            # ──── STAGE 2: GENERATE ────
            self._print_stage_header(2, "GENERATE FHIR BUNDLE")
            self.stage_generate()
            self.stage_results["generate"] = "complete"

            # ──── STAGE 3: CASCADE PREVENTION ────
            self._print_stage_header(3, "CASCADE PREVENTION (postprocess)")
            self.stage_postprocess()
            self.stage_results["postprocess"] = "complete"

            # ──── STAGE 4: VALIDATE & FIX (LLM-driven loop) ────
            self._print_stage_header(4, "VALIDATE & FIX (LLM-driven)")
            self.stage_validate_and_fix()
            self.stage_results["validate"] = "complete"

            # ──── STAGE 5: VERIFY COMPLETENESS ────
            self._print_stage_header(5, "VERIFY COMPLETENESS")
            self.stage_verify()
            self.stage_results["verify"] = "complete"

            # ──── STAGE 6: FINAL VALIDATION ────
            self._print_stage_header(6, "FINAL VALIDATION")
            errors, warnings, _ = self._run_fhir_validator()
            self.stage_results["final_errors"] = len(errors)
            self.stage_results["final_warnings"] = len(warnings)
            if len(errors) == 0:
                print(f"  VALIDATION PASSED: 0 errors, {len(warnings)} warnings")
            else:
                print(f"  {len(errors)} errors remain, {len(warnings)} warnings")
                error_summary = self._build_error_summary(errors)
                for group in error_summary["groups"][:15]:
                    print(f"    [{group['count']}x] {group['message'][:120]}")

            elapsed = (datetime.now() - self.start_time).total_seconds()
            result = {
                "status": "complete",
                "duration_seconds": round(elapsed, 1),
                "llm_usage": self.llm.get_usage_stats(),
                "stages": self.stage_results,
                "bundle_path": self.bundle_path,
            }
            self._print_final_summary(result)
            return result

        except Exception as e:
            logger.error(f"Pipeline failed: {e}", exc_info=True)
            elapsed = (datetime.now() - self.start_time).total_seconds()
            return {
                "status": "error",
                "message": str(e),
                "duration_seconds": round(elapsed, 1),
                "llm_usage": self.llm.get_usage_stats(),
                "stages": self.stage_results,
            }

    # ═══════════════════════════════════════════════════════════
    # STAGE 1: EXTRACT STRUCTURED DATA FROM PDF
    # ═══════════════════════════════════════════════════════════

    def stage_extract(self, md_path: str):
        """Extract all policy data from PDF markdown into structured JSON."""
        pdf_content = self._read_file(md_path)
        pdf_lines = len(pdf_content.splitlines())
        pdf_chars = len(pdf_content)
        print(f"  PDF markdown: {pdf_lines} lines, {pdf_chars:,} chars")

        # ── Call 1: Organization + Policy metadata ──
        print("  [1/3] Extracting organization & policy info...")
        org_policy = self._llm_extract(pdf_content, self._extraction_prompt_org_policy())

        # ── Call 2: All coverage sections ──
        print("  [2/3] Extracting all coverage sections...")
        coverages = self._llm_extract(pdf_content, self._extraction_prompt_coverages())

        # ── Call 3: Exclusions, waiting periods, supporting docs ──
        print("  [3/3] Extracting exclusions, waiting periods, supporting docs...")
        excl_wait = self._llm_extract(pdf_content, self._extraction_prompt_exclusions())

        # Merge into single extraction
        extracted = {}
        if org_policy:
            extracted.update(org_policy)
        if coverages:
            if isinstance(coverages, list):
                extracted["coverages"] = coverages
            elif isinstance(coverages, dict) and "coverages" in coverages:
                extracted["coverages"] = coverages["coverages"]
            else:
                extracted["coverages"] = [coverages]
        else:
            extracted["coverages"] = []

        if excl_wait:
            for key in ["waiting_periods", "permanent_exclusions", "supporting_documents",
                         "general_exclusions_not_coded"]:
                if key in excl_wait:
                    extracted[key] = excl_wait[key]

        # Ensure required keys exist
        for key in ["insurer", "policy", "coverages", "waiting_periods",
                     "permanent_exclusions", "supporting_documents",
                     "general_exclusions_not_coded", "plans"]:
            if key not in extracted:
                extracted[key] = {} if key in ["insurer", "policy"] else []

        # Merge plans from org_policy extraction if present
        if org_policy and "plans" in org_policy:
            extracted["plans"] = org_policy["plans"]

        # ── Verification pass: ask LLM if anything was missed ──
        print("  [verify] Checking extraction completeness...")
        missing = self._llm_verify_extraction(pdf_content, extracted)
        if missing:
            extracted["_verification_additions"] = missing
            if isinstance(missing, dict):
                for key, val in missing.items():
                    if key in extracted and isinstance(extracted[key], list) and isinstance(val, list):
                        extracted[key].extend(val)
                    elif key in extracted and isinstance(extracted[key], dict) and isinstance(val, dict):
                        extracted[key].update(val)

        # Save
        self._write_json("extracted_data.json", extracted)
        n_cov = len(extracted.get("coverages", []))
        n_excl = len(extracted.get("permanent_exclusions", []))
        n_gen_excl = len(extracted.get("general_exclusions_not_coded", []))
        n_wait = len(extracted.get("waiting_periods", []))
        n_supp = len(extracted.get("supporting_documents", []))
        n_plans = len(extracted.get("plans", []))
        print(f"  Saved extracted_data.json: {n_cov} coverages, {n_excl} coded exclusions, "
              f"{n_gen_excl} general exclusions, {n_wait} waiting periods, "
              f"{n_supp} supporting docs, {n_plans} plans")

    def _extraction_prompt_org_policy(self) -> str:
        return """Extract the INSURER/ORGANIZATION and POLICY information from this insurance policy document.

Return a JSON object with EXACTLY this structure (fill in every field you can find; use null for unknown):
{
  "insurer": {
    "name": "Full legal name of the insurance company",
    "subsidiary_of": "Parent company if mentioned",
    "product_uin": "Product UIN number (e.g., ADIHLGP22023V032122)",
    "rohini_id": "ROHINI ID number — look for ROHINI number, Reg. No., or Registration Number. Often a short number like 153",
    "irdai_registration": "IRDAI registration number — look for 'IRDAI Reg. No.', 'Registration No.', 'Registered with IRDAI vide Registration No.' Often in footers/headers",
    "cin": "Corporate Identity Number if mentioned, otherwise null",
    "contact_phone": "Phone number if mentioned",
    "contact_email": "Email if mentioned",
    "website": "Website URL if mentioned",
    "address": "Full address if mentioned"
  },
  "policy": {
    "name": "Policy/Product name",
    "full_name": "Full policy name including any subtitle",
    "type": "individual or group or family_floater",
    "uin": "Product UIN number",
    "insurance_type_code": "Determine from the policy content: 01=Hospitalisation Indemnity, 02=Hospital Cash, 03=Critical Illness, 04=Super Top Up, 05=Personal Accident, 06=Overseas Mediclaim, 09=Package Policy. Use 09 if the policy covers MULTIPLE types of health benefits (e.g., OPD + Cancer + Hospital Cash + Critical Illness together)"
  },
  "plans": [
    {
      "name": "Plan name — e.g., 'Base Plan', 'Cancer Secure Cover - Options', 'Heart Secure Cover - Options A through H'",
      "description": "Brief description",
      "sum_insured": "Sum insured amount if mentioned",
      "claim_documents": ["List of documents required for claims under this plan"]
    }
  ]
}

IMPORTANT:
- The UIN is often found in headers/footers like "Product UIN: ADIHLGP22023V032122"
- IRDAI Registration Number is often in footers — look for "Reg. No." followed by a number
- If the ROHINI ID is not explicitly stated, check if the IRDAI registration number can serve as the identifier value
- For insurance_type_code: if the policy has MULTIPLE benefit types (OPD + Cancer Cover + Hospital Cash + Heart Cover etc.), use "09" (Package Policy)
- Extract ALL plan variants mentioned — base plan, add-on plans, optional covers
- Look for claim documentation requirements for each plan/section
- Extract the EXACT text, do not paraphrase
- Return ONLY the JSON, no markdown formatting"""

    def _extraction_prompt_coverages(self) -> str:
        return """Extract the TOP-LEVEL coverage sections from this insurance policy.

CRITICAL DISTINCTION — what IS a coverage vs what is NOT:
- A COVERAGE is a distinct type of insurance benefit the policyholder can claim (e.g., OPD, Cancer, Hospitalization, Day Care, Heart Cover, etc.)
- These are NOT coverages and MUST NOT be extracted:
  * "Definitions" sections — these define terms, not benefits
  * "Claims Procedure" — describes how to file a claim
  * "General Conditions" — contract terms, not benefits
  * "Grievance Redressal" — administrative process
  * "Options Grid" or tables — part of a parent coverage
  * Sub-sections (e.g., II.2.1, II.2.2) — these are SUB-BENEFITS of the parent section

GROUPING RULE: If a section has sub-sections (e.g., II.2 has II.2.1 through II.2.6), the sub-sections are SUB-BENEFITS of the parent coverage, NOT separate coverages.

Return a JSON object:
{
  "coverages": [
    {
      "section_id": "Top-level section number like II.1, II.2, II.3",
      "name": "Coverage name as stated in the policy",
      "type_hint": "Your best short keyword for the coverage type (e.g., outpatient, inpatient, cancer, surgical, hospital_cash, day_care, critical_illness, etc.). This is used by the LLM later to decide the SNOMED CT code.",
      "description": "Brief description of what this coverage provides",
      "conditions": ["ALL conditions/requirements/exclusions for this coverage — be EXHAUSTIVE, include waiting periods, survival periods, sub-limits, claim impact rules, what is NOT covered under this specific coverage"],
      "sub_benefits": [
        {
          "name": "Sub-benefit name",
          "section_id": "Sub-section ID like II.2.1 if applicable",
          "what_is_covered": "Full description of what is covered",
          "what_is_not_covered": "Full description of what is NOT covered",
          "limit_description": "Any limits, sub-limits, co-pay mentioned"
        }
      ],
      "section_specific_exclusions": ["Exclusions specific to this coverage section"],
      "options": [
        {
          "option_name": "Option 1 or Option 2 etc.",
          "description": "What this option covers",
          "payout_details": "Payout amounts or percentages"
        }
      ]
    }
  ]
}

IMPORTANT:
- Typically an insurance policy has 8-15 top-level coverages, NOT 20-30
- Sub-sections (II.2.1, II.2.2) go into sub_benefits of their parent (II.2)
- Sections about Definitions, Claims, General Conditions are NOT coverages
- Extract ALL conditions per coverage — these become Claim-Condition extensions in the FHIR bundle
- DO NOT skip any conditions, waiting periods, or exclusions within a coverage section
- Return ONLY the JSON, no markdown formatting"""

    def _extraction_prompt_exclusions(self) -> str:
        return """Extract ALL exclusions, waiting periods, and supporting document requirements from this insurance policy.

Return a JSON object:
{
  "waiting_periods": [
    {
      "code": "MUST be one of: Excl01 (Pre-Existing Diseases), Excl02 (Specified disease/procedure waiting period), Excl03 (30-day waiting period)",
      "name": "Name like Pre-Existing Diseases",
      "period": "Duration like 48 months, 24 months, 30 days",
      "description": "FULL verbatim description of this waiting period",
      "applicable_diseases": ["COMPLETE list of ALL diseases/conditions subject to this waiting period"],
      "applicable_procedures": ["COMPLETE list of ALL procedures/surgeries subject to this waiting period"]
    }
  ],
  "permanent_exclusions": [
    {
      "nhcx_code": "Map to NHCX exclusion code. MUST be one of: Excl04=Investigation Evaluation, Excl05=Rest Cure/Rehabilitation/Respite Care, Excl06=Obesity/Weight Control, Excl07=Change-of-Gender, Excl08=Cosmetic/Plastic Surgery, Excl09=Hazardous/Adventure Sports, Excl10=Breach of Law, Excl11=Excluded Providers, Excl12=Rehabilitation (substance abuse), Excl13=Hydrotherapy, Excl14=Non-prescription, Excl15=Refractive Error, Excl16=Unproven Treatments, Excl17=Sterility/Infertility, Excl18=Maternity Expenses. Use null ONLY for exclusions that don't match any of these categories.",
      "name": "Short name of the exclusion",
      "description": "FULL verbatim description — do NOT summarize, include the complete text"
    }
  ],
  "general_exclusions_not_coded": [
    {
      "description": "Full text of each general exclusion that does NOT map to Excl01-Excl18 (e.g., war, self-injury, nuclear, dental, etc.)"
    }
  ],
  "supporting_documents": [
    {
      "category_code": "One of: FCF=Filled Claim Form, POI=Proof of Identity, POA=Proof of Address, MB=Medical Bill, DIA=Diagnostic Report, HDS=Hospital Discharge Summary, CD=Clinical Document, ATT=Attachment, CER=Certificate",
      "category_display": "Display name matching the code above",
      "document_code": "One of: ADN=Aadhaar, PPN=Passport, DL=Driving Licence, or other code",
      "document_display": "Display name for the document type"
    }
  ]
}

CRITICAL RULES:
- Map EACH exclusion to its NHCX code (Excl01-Excl18) based on the category. Most insurance policies have exclusions that map to ALL 18 categories.
- For the 2-year waiting period (Excl02), there is typically a LARGE TABLE of diseases and procedures. Extract EVERY disease and procedure.
- For Section IV (permanent exclusions), extract EVERY numbered exclusion and map each to its Excl code.
- For supporting documents, look for claim documentation requirements across ALL sections. Common categories:
  FCF = Filled Claim Form, POI = Proof of Identity, POA = Proof of Address,
  MB = Medical Bill, DIA = Diagnostic Report, HDS = Hospital Discharge Summary,
  CD = Clinical Document, ATT = Attachment, CER = Certificate
- Include the FULL text of each exclusion — verbatim, not summarized
- Return ONLY the JSON, no markdown formatting"""

    def _llm_extract(self, pdf_content: str, extraction_instructions: str) -> dict:
        """Call LLM to extract structured data from PDF."""
        system = (
            "You are a precise insurance policy data extractor. "
            "Your job is to extract structured data from a PDF document converted to markdown. "
            "You must be EXHAUSTIVE — extract EVERY piece of information. "
            "Never summarize or omit details. Extract verbatim where possible. "
            "Return ONLY valid JSON — no markdown code blocks, no explanations."
        )
        user = f"{extraction_instructions}\n\n{'═' * 40}\nPDF DOCUMENT CONTENT:\n{'═' * 40}\n\n{pdf_content}"

        response = self._llm_call(system, user, temperature=0.1)
        result = self._extract_json_from_response(response)

        if result is None:
            logger.warning("Failed to parse extraction response as JSON, retrying with simpler prompt")
            retry_prompt = (
                "Your previous response was not valid JSON. "
                "Please output ONLY valid JSON matching the requested schema. "
                "No markdown, no code blocks, no explanations. Just the JSON object.\n\n"
                f"Original request:\n{extraction_instructions}\n\n"
                f"Your response was:\n{response[:2000]}"
            )
            response2 = self._llm_call(system, retry_prompt, temperature=0.0)
            result = self._extract_json_from_response(response2)

        if result is None:
            logger.error("Failed to extract JSON from LLM response after retry")
            return {}

        return result

    def _llm_verify_extraction(self, pdf_content: str, extracted: dict) -> dict:
        """Ask LLM to verify if the extraction is complete."""
        extracted_summary = json.dumps(extracted, indent=2, default=str)
        if len(extracted_summary) > 30000:
            extracted_summary = extracted_summary[:30000] + "\n... [truncated]"

        system = (
            "You are verifying the completeness of data extraction from an insurance policy PDF. "
            "Compare the extracted data against the original PDF and identify ANY missing information. "
            "Return ONLY valid JSON — no markdown, no explanations."
        )
        user = (
            "Compare the extracted data below against the original PDF document. "
            "If you find ANY coverage sections, exclusions, waiting periods, or other important information "
            "that was NOT captured in the extraction, return them in the same JSON format.\n\n"
            "If the extraction is complete, return: {\"complete\": true}\n\n"
            f"EXTRACTED DATA:\n{extracted_summary}\n\n"
            f"{'═' * 40}\nORIGINAL PDF:\n{'═' * 40}\n\n{pdf_content[:80000]}"
        )

        response = self._llm_call(system, user, temperature=0.1)
        result = self._extract_json_from_response(response)

        if result and result.get("complete"):
            print("  Extraction verified: complete")
            return None
        elif result:
            print(f"  Verification found additional items to add")
            return result
        return None

    # ═══════════════════════════════════════════════════════════
    # STAGE 2: GENERATE FHIR BUNDLE
    # ═══════════════════════════════════════════════════════════

    def stage_generate(self):
        """Generate the FHIR InsurancePlan bundle from extracted data."""
        extracted = self._load_json("extracted_data.json")
        if not extracted:
            raise RuntimeError("extracted_data.json not found or empty")

        example = self._read_file(
            os.path.join(self.examples_dir, "Bundle-InsurancePlanBundle-example-01.json")
        )

        # Read relevant rulebooks
        rulebook_ip = self._read_file(
            os.path.join(self.rulebooks_dir, "StructureDefinition-InsurancePlan_updated.json")
        )
        rulebook_org = self._read_file(
            os.path.join(self.rulebooks_dir, "StructureDefinition-Organization_updated.json")
        )
        rulebook_bundle = self._read_file(
            os.path.join(self.rulebooks_dir, "StructureDefinition-InsurancePlanBundle_updated.json")
        )

        # Ask the LLM to generate a Python script
        print("  Generating Python bundle script via LLM...")
        python_code = self._llm_generate_script(extracted, example, rulebook_ip, rulebook_org, rulebook_bundle)

        if not python_code:
            raise RuntimeError("LLM failed to generate Python script")

        script_path = os.path.join(self.generated_dir, "generate_bundle.py")

        # Inject a hardcoded OUTPUT_PATH at the top of the script so the LLM
        # cannot accidentally write to a wrong directory.
        python_code = self._inject_output_path(python_code)

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(python_code)
        print(f"  Script written: {script_path} ({len(python_code):,} chars)")

        # Run the script, fix errors iteratively
        max_fix_attempts = 15
        for attempt in range(max_fix_attempts):
            print(f"  Running script (attempt {attempt + 1})...")
            returncode, stdout, stderr = self._run_command(
                f"python3 {script_path}", timeout=120
            )

            if returncode == 0:
                print(f"  Script executed successfully!")
                # Check expected path first, then search for misplaced file
                if not os.path.exists(self.bundle_path):
                    self._recover_misplaced_bundle()

                if os.path.exists(self.bundle_path):
                    bundle_size = os.path.getsize(self.bundle_path)
                    print(f"  Bundle generated: {bundle_size:,} bytes")
                    try:
                        with open(self.bundle_path) as f:
                            json.load(f)
                        print(f"  JSON syntax: valid")
                        # Immediately deduplicate — LLM scripts often produce repetition
                        self._run_dedup_pass()
                        return
                    except json.JSONDecodeError as e:
                        print(f"  JSON syntax error: {e}")
                        stderr = f"Generated JSON has syntax error: {e}"
                else:
                    stderr = f"Script ran but {self.bundle_path} was not created. The script MUST write output to exactly: {self.bundle_path}"
                    print(f"  WARNING: {stderr}")

            # Fix Python errors
            error_output = stderr or stdout
            print(f"  Script error, asking LLM to fix...")

            # Read current script
            with open(script_path, "r", encoding="utf-8") as f:
                current_code = f.read()

            fixed_code = self._llm_fix_python_error(current_code, error_output)
            if fixed_code:
                fixed_code = self._inject_output_path(fixed_code)
                with open(script_path, "w", encoding="utf-8") as f:
                    f.write(fixed_code)
                print(f"  Script fixed, retrying...")
            else:
                print(f"  Failed to fix script error")
                break

        if not os.path.exists(self.bundle_path):
            raise RuntimeError(f"Failed to generate bundle after {max_fix_attempts} attempts")

    def _inject_output_path(self, code: str) -> str:
        """Inject a hardcoded OUTPUT_PATH constant at the top of the script."""
        path_line = f'OUTPUT_PATH = r"{self.bundle_path}"\n'
        # If OUTPUT_PATH already present, replace it
        if "OUTPUT_PATH" in code:
            code = re.sub(r'^OUTPUT_PATH\s*=.*$', path_line.strip(), code, flags=re.MULTILINE)
            return code
        # Otherwise insert after imports
        lines = code.split('\n')
        insert_idx = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith('#') and not stripped.startswith('import') and not stripped.startswith('from') and stripped != '':
                insert_idx = i
                break
        lines.insert(insert_idx, path_line)
        return '\n'.join(lines)

    def _recover_misplaced_bundle(self):
        """Search for InsurancePlanBundle.json written to the wrong location and move it."""
        import shutil
        search_dirs = [
            os.path.join(self.project_root, "generated"),
            os.path.join(self.generated_dir, "generated"),
            self.project_root,
        ]
        for d in search_dirs:
            candidate = os.path.join(d, "InsurancePlanBundle.json")
            if os.path.exists(candidate) and candidate != self.bundle_path:
                print(f"  Found misplaced bundle at {candidate}, moving to {self.bundle_path}")
                shutil.move(candidate, self.bundle_path)
                # Clean up empty directory
                try:
                    os.rmdir(d)
                except OSError:
                    pass
                return

    def _llm_generate_script(self, extracted: dict, example: str,
                              rulebook_ip: str, rulebook_org: str, rulebook_bundle: str) -> str:
        """Ask LLM to generate a Python script that builds the FHIR bundle."""
        data_summary = {
            "top_level_keys": list(extracted.keys()),
            "insurer": extracted.get("insurer", {}),
            "policy": extracted.get("policy", {}),
            "num_coverages": len(extracted.get("coverages", [])),
            "coverage_names": [c.get("name", c.get("category", "?")) for c in extracted.get("coverages", [])],
            "num_exclusions": len(extracted.get("permanent_exclusions", [])),
            "num_general_exclusions": len(extracted.get("general_exclusions_not_coded", [])),
            "num_waiting_periods": len(extracted.get("waiting_periods", [])),
            "num_supporting_docs": len(extracted.get("supporting_documents", [])),
            "num_plans": len(extracted.get("plans", [])),
        }
        coverages = extracted.get("coverages", [])
        if coverages:
            data_summary["sample_coverage"] = coverages[0]
        exclusions = extracted.get("permanent_exclusions", [])
        if exclusions:
            data_summary["sample_exclusion"] = exclusions[0]
        waiting_periods = extracted.get("waiting_periods", [])
        if waiting_periods:
            data_summary["sample_waiting_period"] = waiting_periods[0]
        supporting_docs = extracted.get("supporting_documents", [])
        if supporting_docs:
            data_summary["sample_supporting_doc"] = supporting_docs[0]
        general_excl = extracted.get("general_exclusions_not_coded", [])
        plans = extracted.get("plans", [])
        if plans:
            data_summary["sample_plan"] = plans[0]
        data_summary_str = json.dumps(data_summary, indent=2, default=str)

        # Extract a CONDENSED structural template from the reference bundle
        # instead of dumping the full 200KB file which overloads the local LLM
        reference_template = self._build_reference_template()

        if len(rulebook_ip) > 15000:
            rulebook_ip = rulebook_ip[:15000] + "\n... [truncated]"
        if len(rulebook_org) > 8000:
            rulebook_org = rulebook_org[:8000] + "\n... [truncated]"

        system = (
            "You are an expert FHIR developer specializing in NHCX/ABDM-compliant insurance bundles. "
            "Generate a complete, working Python script that reads extracted data from a JSON file and "
            "produces a valid FHIR InsurancePlanBundle JSON file. "
            "Study the REFERENCE BUNDLE carefully — it scores 95% on NHCX compliance. Your output must match its structure. "
            "Output ONLY the Python code — no markdown code blocks, no explanations."
        )

        snomed_map_str = json.dumps({k: {"code": v[0], "display": v[1]} for k, v in SNOMED_LOOKUP.items()}, indent=2)

        user = f"""Generate a Python3 script that reads extracted insurance data and creates an NHCX-compliant FHIR InsurancePlanBundle.

═══ FILE PATHS (CRITICAL — use exactly these) ═══
The script will have a global constant OUTPUT_PATH injected at the top. Use it as output path.
Input file (extracted data): {self.extracted_data_path}
Output file: use OUTPUT_PATH constant
Do NOT use os.getcwd() or construct your own paths.

═══ HOW THE SCRIPT MUST WORK ═══
1. Read extracted data from {self.extracted_data_path} using json.load()
2. Build the FHIR bundle from the data, following the REFERENCE BUNDLE structure exactly
3. Write the bundle to OUTPUT_PATH using json.dump()

═══ EXTRACTED DATA STRUCTURE ═══
{data_summary_str}

Key fields:
- data["insurer"] — dict with name, irdai_registration, rohini_id, address, contact, product_uin
- data["policy"] — dict with name, type, uin, insurance_type_code
- data["coverages"] — list of dicts, each with name, type_hint, conditions, sub_benefits, options, section_specific_exclusions, snomed_code/snomed_display
- data["permanent_exclusions"] — list with nhcx_code (Excl04-Excl18), name, description
- data["waiting_periods"] — list with code (Excl01-Excl03), name, period, description
- data["general_exclusions_not_coded"] — list of exclusions that don't map to Excl01-Excl18
- data["supporting_documents"] — list with category_code, category_display, document_code, document_display
- data["plans"] — list of plan variants with name, description, sum_insured, claim_documents

═══ REFERENCE STRUCTURAL TEMPLATE (extracted from a 95% compliant bundle — match this structure) ═══
{reference_template}

═══ KEY STRUCTURAL PATTERNS FROM REFERENCE (you MUST replicate these) ═══

1. INSURANCEPLAN TYPE: Use data["policy"]["insurance_type_code"] to set the type. Common codes:
   01=Hospitalisation Indemnity, 02=Hospital Cash, 03=Critical Illness, 09=Package Policy
   If the policy covers multiple benefit types, use "09".
   "type": [{{"coding": [{{"system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-insuranceplan-type", "code": "<code>", "display": "<display>"}}]}}]

2. ORGANIZATION IDENTIFIER: Use IRDAI registration number or ROHINI ID as the value:
   "identifier": [{{"type": {{"coding": [{{"system": "https://rohini.iib.gov.in/", "code": "ROHINI", "display": "ROHINI Id"}}]}}, "system": "https://irdai.gov.in", "value": "<irdai_reg_or_rohini>"}}]
   Check data["insurer"]["irdai_registration"] and data["insurer"]["rohini_id"].

3. COVERAGE SNOMED CODES — the LLM must DECIDE the best SNOMED code for each coverage based on its type_hint and description:
   Available SNOMED codes (use ONLY these — for anything else use text-only):
   737492002 = Outpatient care management (procedure) — for OPD, outpatient covers
   737481003 = Inpatient care management (procedure) — for hospitalization, hospital cash, income protection, major illness
   367336001 = Chemotherapy (procedure) — for cancer-specific covers involving treatment
   387713003 = Surgical procedure (procedure) — for surgical/procedure-based covers
   737850002 = Day care case management — for day care procedures
   409972000 = Pre-hospital care — for pre-hospitalization
   710967003 = Post-discharge management — for post-hospitalization
   49122002 = Ambulance — for ambulance cover
   105461009 = Organ donor
   Use your JUDGEMENT: read the coverage name and description, then pick the CLOSEST matching SNOMED code. If none fits well, use text-only.

4. CLAIM-EXCLUSION EXTENSIONS on InsurancePlan.extension[]:
   - One for each waiting period (Excl01, Excl02, Excl03)
   - One for each permanent exclusion (Excl04-Excl18)
   - One combined entry for all general exclusions using code "IIB20" with display "Not covered under the Terms and Conditions of the contract", with MULTIPLE "statement" sub-extensions (one per general exclusion)
   EXACT format:
   {{"extension": [{{"url": "category", "valueCodeableConcept": {{"coding": [{{"system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion", "code": "Excl01", "display": "Pre-Existing Diseases"}}]}}}}, {{"url": "statement", "valueString": "full text"}}], "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Exclusion"}}

5. CLAIM-CONDITION EXTENSIONS on each coverage.extension[]:
   Each coverage should have ONE Claim-Condition extension containing MULTIPLE sub-extensions:
   {{"extension": [{{"url": "claim-condition", "valueString": "condition 1"}}, {{"url": "claim-condition", "valueString": "condition 2"}}], "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Condition"}}

6. SUPPORTING INFO on InsurancePlan.extension[]:
   {{"extension": [{{"url": "category", "valueCodeableConcept": {{"coding": [{{"system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-supportinginfo-category", "code": "FCF", "display": "Filled claim form"}}]}}}}, {{"url": "code", "valueCodeableConcept": {{"coding": [{{"system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code", "code": "ADN", "display": "Adhaar number"}}]}}}}], "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-SupportingInfoRequirement"}}

7. PLAN ENTRIES: Each plan has identifier, type (code "03" = Group), generalCost:
   {{"identifier": [{{"use": "official", "value": "Plan Name"}}], "type": {{"coding": [{{"system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-plan-type", "code": "03", "display": "Group"}}]}}, "generalCost": [{{"cost": {{"value": 500000, "currency": "INR"}}}}]}}

═══ MANDATORY STRUCTURE RULES ═══
1. Bundle.type MUST be "collection"
2. Bundle entries MUST NOT have "request" or "response" properties
3. InsurancePlan.type MUST be an array with EXACTLY 1 entry
4. coverage.type is a CodeableConcept (NOT an array)
5. plan.type is a CodeableConcept (NOT an array)
6. Every resource MUST have text.status ("extensions" if has extensions, else "generated") and text.div (valid XHTML)

═══ CRITICAL: AVOID REPETITION LOOPS ═══
- Global exclusions (Excl01-Excl18, general exclusions) go on InsurancePlan.extension[] ONCE — do NOT repeat them inside each coverage
- Each coverage.extension[] should ONLY contain Claim-Condition extensions specific to THAT coverage
- Do NOT copy the same exclusion/extension block multiple times into the same array
- Keep the output compact — a valid bundle is typically 50-200KB, NOT megabytes

═══ SNOMED CODES (only use these — for anything else, use text-only) ═══
{snomed_map_str}

═══ EXCLUSION CODE DISPLAY NAMES (use EXACT display — wrong display causes cascade failures!) ═══
Excl01="Pre-Existing Diseases", Excl02="Specified disease/procedure waiting period", Excl03="30-day waiting period",
Excl04="Investigation Evaluation", Excl05="Rest Cure,Rehabilitation and Respite Care", Excl06="Obesity/Weight Control",
Excl07="Change-of-Gender treatments", Excl08="Cosmetic or plastic Surgery", Excl09="Hazardous or Adventure sports",
Excl10="Breach of law", Excl11="Excluded providers", Excl12="Rehabilitation", Excl13="Hydrotherapy",
Excl14="Non-prescription", Excl15="Refractive Error", Excl16="Unproven Treatments",
Excl17="Sterility and infertility", Excl18="Maternity expenses"
For exclusions without valid code, use text-only. NEVER use code "unknown".

═══ KEY URLS ═══
Bundle profile: "https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlanBundle"
InsurancePlan profile: "https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlan"
Organization profile: "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Organization"
IRDAI: "https://irdai.gov.in"
ROHINI: "https://rohini.iib.gov.in/"
InsurancePlan Type CS: "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-insuranceplan-type"
Plan Type CS: "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-plan-type"

═══ COMPLETENESS CHECKLIST ═══
1. ALL {len(coverages)} coverages → InsurancePlan.coverage[] with SNOMED-coded types + Claim-Condition extensions
2. ALL {len(exclusions)} permanent exclusions → InsurancePlan.extension[] as Claim-Exclusion with nhcx_code
3. ALL {len(waiting_periods)} waiting periods → InsurancePlan.extension[] as Claim-Exclusion
4. ALL {len(general_excl)} general exclusions → ONE Claim-Exclusion with code "IIB20" + multiple statements
5. ALL {len(supporting_docs)} supporting docs → InsurancePlan.extension[] as Claim-SupportingInfoRequirement
6. Plan entries → InsurancePlan.plan[] with type "03" (Group)
7. Organization with ROHINI identifier

═══ EXAMPLE BUNDLE (passes validation with 0 errors) ═══
{example[:8000]}

═══ InsurancePlan RULEBOOK ═══
{rulebook_ip}

═══ Organization RULEBOOK ═══
{rulebook_org}

═══ Bundle RULEBOOK ═══
{rulebook_bundle}

IMPORTANT: Read from {self.extracted_data_path}, iterate ALL items, write to OUTPUT_PATH.
Generate the complete Python script now. Output ONLY Python code."""

        response = self._llm_call(system, user, max_tokens=16384, temperature=0.15)
        return self._extract_code_from_response(response)

    def _llm_fix_python_error(self, code: str, error: str) -> str:
        """Ask LLM to fix a Python script error."""
        system = (
            "You are a Python debugging expert. Fix the error in the given script. "
            "Output ONLY the complete fixed Python script — no markdown, no explanations."
        )
        # Show relevant section around the error
        line_match = re.search(r'line (\d+)', error)
        context_hint = ""
        if line_match:
            error_line = int(line_match.group(1))
            lines = code.splitlines()
            start = max(0, error_line - 10)
            end = min(len(lines), error_line + 10)
            context_hint = f"\n\nLines {start+1}-{end} around the error:\n"
            for i in range(start, end):
                marker = ">>>" if i == error_line - 1 else "   "
                context_hint += f"{marker} {i+1}: {lines[i]}\n"

        # If the code is very long, truncate for the fix prompt but show enough context
        code_for_prompt = code
        if len(code) > 60000:
            code_for_prompt = code[:30000] + "\n\n... [middle truncated] ...\n\n" + code[-30000:]

        user = f"Fix this Python script error:\n\nERROR:\n{error[:3000]}\n{context_hint}\n\nFULL SCRIPT:\n{code_for_prompt}"

        response = self._llm_call(system, user, max_tokens=16384, temperature=0.1)
        return self._extract_code_from_response(response) or None

    # ═══════════════════════════════════════════════════════════
    # STAGE 2.5: POST-PROCESS (deterministic structural fixes)
    # ═══════════════════════════════════════════════════════════

    # Known valid SNOMED codes (verified against the example bundle)
    VALID_SNOMED_CODES = {
        "737481003", "737492002", "367336001", "387713003",
        "710967003", "409972000", "49122002", "737850002",
        "105461009", "309904001", "87612001", "24099007", "224663004",
        "60689008", "373784001", "373873005", "11429006", "165340005", "118189007",
        "133906008", "716186003", "86077009", "275926002", "713603004",
    }

    def stage_postprocess(self):
        """Light deterministic fixes for known cascade-causing patterns.
        Everything else is handled dynamically by the LLM in the validate-fix loop."""
        if not os.path.exists(self.bundle_path):
            raise RuntimeError(f"Bundle not found: {self.bundle_path}")

        with open(self.bundle_path, "r", encoding="utf-8") as f:
            bundle = json.load(f)

        fixes = 0

        # ── CRITICAL: Deduplicate arrays first (prevents 8MB bloat from LLM repetition loops) ──
        dedup_count = self._deduplicate_bundle_obj(bundle)
        if dedup_count > 0:
            fixes += dedup_count

        # ── Remove empty arrays/objects that serve no FHIR purpose ──
        empty_count = self._clean_empty_elements(bundle)
        fixes += empty_count

        # ── Cascade fix: Claim-Exclusion codes/display names ──
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") != "InsurancePlan":
                continue
            for ext in resource.get("extension", []):
                if "Claim-Exclusion" in ext.get("url", ""):
                    self._fix_exclusion_extension(ext)
                    fixes += 1

        # ── Cascade fix: Invalid SNOMED codes ──
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") != "InsurancePlan":
                continue
            for coverage in resource.get("coverage", []):
                if self._fix_snomed_in_codeable_concept(coverage.get("type", {})):
                    fixes += 1
                for benefit in coverage.get("benefit", []):
                    if self._fix_snomed_in_codeable_concept(benefit.get("type", {})):
                        fixes += 1

        # ── Fix SNOMED display names ──
        self._fix_snomed_display_names(bundle)

        with open(self.bundle_path, "w", encoding="utf-8") as f:
            json.dump(bundle, f, indent=2, ensure_ascii=False)

        file_size = os.path.getsize(self.bundle_path)
        print(f"  Postprocess: {fixes} fixes applied, bundle size: {file_size:,} bytes")

        if file_size > 500_000:
            print(f"  WARNING: Bundle is {file_size:,} bytes — likely has repetition. Running deep dedup...")
            self._deep_deduplicate()

    # ═══════════════════════════════════════════════════════════
    # DEDUPLICATION & SANITY CHECKS
    # ═══════════════════════════════════════════════════════════

    def _deduplicate_bundle_obj(self, bundle: dict) -> int:
        """Remove duplicate entries in all arrays throughout the bundle.
        LLM scripts often append the same exclusion/extension block hundreds of times."""
        total_removed = 0

        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") == "InsurancePlan":
                total_removed += self._dedup_array(resource, "extension")
                total_removed += self._dedup_array(resource, "coverage")
                total_removed += self._dedup_array(resource, "plan")
                for cov in resource.get("coverage", []):
                    total_removed += self._dedup_array(cov, "extension")
                    total_removed += self._dedup_array(cov, "benefit")
                    for ben in cov.get("benefit", []):
                        total_removed += self._dedup_array(ben, "extension")
                for plan in resource.get("plan", []):
                    total_removed += self._dedup_array(plan, "generalCost")
                    total_removed += self._dedup_array(plan, "specificCost")

        if total_removed > 0:
            print(f"  Dedup: removed {total_removed} duplicate array entries")
        return total_removed

    def _dedup_array(self, obj: dict, key: str) -> int:
        """Deduplicate entries in an array by content hash."""
        arr = obj.get(key)
        if not arr or not isinstance(arr, list) or len(arr) <= 1:
            return 0
        seen = set()
        unique = []
        for item in arr:
            h = json.dumps(item, sort_keys=True, default=str)
            if h not in seen:
                seen.add(h)
                unique.append(item)
        removed = len(arr) - len(unique)
        if removed > 0:
            obj[key] = unique
        return removed

    def _clean_empty_elements(self, obj) -> int:
        """Remove empty arrays [], empty strings, and empty objects from FHIR resources.
        These cause validation noise and bloat."""
        removed = 0
        if isinstance(obj, dict):
            keys_to_remove = []
            for k, v in list(obj.items()):
                if isinstance(v, list):
                    if not v:
                        keys_to_remove.append(k)
                        removed += 1
                    else:
                        removed += self._clean_empty_elements(v)
                elif isinstance(v, dict):
                    removed += self._clean_empty_elements(v)
                    if not v:
                        keys_to_remove.append(k)
                        removed += 1
                elif isinstance(v, str) and v == "" and k not in ("div", "value", "text"):
                    keys_to_remove.append(k)
                    removed += 1
            for k in keys_to_remove:
                del obj[k]
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, (dict, list)):
                    removed += self._clean_empty_elements(item)
        return removed

    def _deep_deduplicate(self):
        """Aggressive dedup for when the bundle is unreasonably large (>500KB).
        Reads, deduplicates all arrays recursively, removes empties, writes back."""
        with open(self.bundle_path, "r", encoding="utf-8") as f:
            bundle = json.load(f)

        original_size = os.path.getsize(self.bundle_path)

        self._recursive_dedup(bundle)
        self._clean_empty_elements(bundle)

        with open(self.bundle_path, "w", encoding="utf-8") as f:
            json.dump(bundle, f, indent=2, ensure_ascii=False)

        new_size = os.path.getsize(self.bundle_path)
        print(f"  Deep dedup: {original_size:,} → {new_size:,} bytes")

    def _recursive_dedup(self, obj):
        """Recursively deduplicate all arrays in a nested structure."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, list) and len(v) > 1:
                    seen = set()
                    unique = []
                    for item in v:
                        h = json.dumps(item, sort_keys=True, default=str)
                        if h not in seen:
                            seen.add(h)
                            unique.append(item)
                    obj[k] = unique
                    for item in unique:
                        self._recursive_dedup(item)
                elif isinstance(v, dict):
                    self._recursive_dedup(v)
        elif isinstance(obj, list):
            for item in obj:
                self._recursive_dedup(item)

    def _check_bundle_sanity(self) -> bool:
        """Check if the bundle is reasonable. Returns True if sane."""
        if not os.path.exists(self.bundle_path):
            return False
        file_size = os.path.getsize(self.bundle_path)
        if file_size > 500_000:
            print(f"  SANITY FAIL: Bundle is {file_size:,} bytes (max expected ~200KB)")
            return False
        with open(self.bundle_path, "r", encoding="utf-8") as f:
            bundle = json.load(f)
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") == "InsurancePlan":
                n_cov = len(resource.get("coverage", []))
                n_ext = len(resource.get("extension", []))
                n_plan = len(resource.get("plan", []))
                if n_cov > 30:
                    print(f"  SANITY FAIL: {n_cov} coverages (expected 8-15)")
                    return False
                if n_ext > 100:
                    print(f"  SANITY FAIL: {n_ext} extensions (expected 20-40)")
                    return False
        return True

    def _fix_snomed_display_names(self, bundle: dict):
        """Fix wrong SNOMED display names to valid ones."""
        SNOMED_DISPLAYS = {}
        for key, (code, display) in SNOMED_LOOKUP.items():
            SNOMED_DISPLAYS[code] = display
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") != "InsurancePlan":
                continue
            for coverage in resource.get("coverage", []):
                self._fix_cc_display(coverage.get("type", {}), SNOMED_DISPLAYS)
                for benefit in coverage.get("benefit", []):
                    self._fix_cc_display(benefit.get("type", {}), SNOMED_DISPLAYS)

    def _fix_cc_display(self, cc: dict, display_map: dict):
        """Fix display name in a CodeableConcept."""
        if not cc or not isinstance(cc, dict):
            return
        for coding in cc.get("coding", []):
            if coding.get("system") == "http://snomed.info/sct":
                code = coding.get("code", "")
                if code in display_map:
                    coding["display"] = display_map[code]

    def _fix_snomed_in_codeable_concept(self, cc: dict) -> bool:
        """Fix invalid SNOMED codes in a CodeableConcept. Returns True if fixed."""
        if not cc or not isinstance(cc, dict):
            return False
        codings = cc.get("coding", [])
        valid_codings = []
        for coding in codings:
            if coding.get("system") == "http://snomed.info/sct":
                code = coding.get("code", "")
                if code in self.VALID_SNOMED_CODES:
                    valid_codings.append(coding)
                else:
                    if not cc.get("text"):
                        cc["text"] = coding.get("display", code)
            else:
                valid_codings.append(coding)
        if len(valid_codings) != len(codings):
            if valid_codings:
                cc["coding"] = valid_codings
            else:
                cc.pop("coding", None)
            return True
        return False

    def _fix_exclusion_extension(self, ext: dict):
        """Fix a Claim-Exclusion extension in-place: correct codes and display names."""
        sub_exts = ext.get("extension", [])
        new_subs = []
        for se in sub_exts:
            if se.get("url") == "category":
                cc = se.get("valueCodeableConcept", {})
                codings = cc.get("coding", [])
                if codings:
                    code = codings[0].get("code", "")
                    if code in EXCLUSION_CODE_DISPLAY:
                        codings[0]["display"] = EXCLUSION_CODE_DISPLAY[code]
                        codings[0]["system"] = "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion"
                        new_subs.append(se)
                    elif code == "IIB20":
                        # Valid custom code for general exclusions — keep as-is
                        codings[0]["system"] = "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion"
                        new_subs.append(se)
                    else:
                        display = codings[0].get("display", cc.get("text", "Unknown exclusion"))
                        new_subs.append({
                            "url": "category",
                            "valueCodeableConcept": {"text": display}
                        })
                else:
                    new_subs.append(se)
            else:
                new_subs.append(se)

        ext["extension"] = new_subs
        ext["url"] = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Exclusion"

    def _fix_claim_condition_extensions(self, element: dict):
        """Ensure Claim-Condition extensions are correctly structured."""
        if not element or "extension" not in element:
            return

        fixed_extensions = []
        for ext in element.get("extension", []):
            url = ext.get("url", "")
            if url == "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Condition":
                # Must have nested extension with url="claim-condition" and valueString
                sub_exts = ext.get("extension", [])
                has_claim_condition = any(
                    se.get("url") == "claim-condition" and "valueString" in se
                    for se in sub_exts
                )
                if has_claim_condition:
                    # Correct structure — keep only the claim-condition sub-extension
                    clean_subs = [se for se in sub_exts if se.get("url") == "claim-condition"]
                    fixed_extensions.append({
                        "extension": clean_subs,
                        "url": url
                    })
                # else: malformed extension, drop it
            else:
                fixed_extensions.append(ext)

        element["extension"] = fixed_extensions
        if not fixed_extensions:
            del element["extension"]

    # ═══════════════════════════════════════════════════════════
    # STAGE 3: VALIDATE & FIX ERRORS
    # ═══════════════════════════════════════════════════════════

    def stage_validate_and_fix(self):
        """Dynamic validate-and-fix loop. Runs real FHIR validator, parses errors,
        applies deterministic fixes first, then uses LLM for remaining issues."""
        if not os.path.exists(self.bundle_path):
            raise RuntimeError(f"Bundle not found: {self.bundle_path}")

        max_rounds = self.settings.get("pipeline", {}).get("max_validation_rounds", 40)
        prev_error_count = float('inf')
        stall_count = 0

        for round_num in range(max_rounds):
            print(f"\n  ── Validation round {round_num + 1}/{max_rounds} ──")

            # Run real FHIR validator (takes 20-60 seconds)
            errors, warnings, raw_output = self._run_fhir_validator()
            error_count = len(errors)
            print(f"  Errors: {error_count}, Warnings: {len(warnings)}")

            if error_count == 0:
                print(f"  VALIDATION PASSED: 0 errors!")
                self.stage_results["validation_rounds"] = round_num + 1
                return

            # ── Step A: Always try postprocess first (catches structural issues) ──
            if round_num == 0 or error_count > 20:
                print(f"  Running postprocess to fix structural issues...")
                self.stage_postprocess()
                # Re-validate after postprocess
                errors2, _, _ = self._run_fhir_validator()
                new_count = len(errors2)
                print(f"  After postprocess: {new_count} errors (was {error_count})")
                if new_count == 0:
                    print(f"  VALIDATION PASSED after postprocess!")
                    self.stage_results["validation_rounds"] = round_num + 1
                    return
                if new_count < error_count:
                    errors = errors2
                    error_count = new_count
                    continue

            # ── Step B: Check for stall ──
            if error_count >= prev_error_count:
                stall_count += 1
            else:
                stall_count = 0
            prev_error_count = error_count

            if stall_count >= 4:
                print(f"  Stalled at {error_count} errors for {stall_count} rounds.")
                break

            # ── Step C: Build smart error summary for LLM ──
            error_summary = self._build_error_summary(errors)
            print(f"  Error categories: {len(error_summary['groups'])}")
            for group in error_summary["groups"][:8]:
                print(f"    [{group['count']}x] {group['message'][:90]}")

            # ── Step D: Ask LLM to generate a fix script ──
            print(f"  Asking LLM to fix {error_count} errors...")
            self._llm_fix_validation_errors(error_summary)

            # ── Step E: Re-run postprocess after LLM fix ──
            self.stage_postprocess()

        self.stage_results["validation_rounds"] = round_num + 1
        self.stage_results["final_errors"] = error_count
        print(f"  Completed {round_num + 1} rounds. {error_count} errors remain.")

    def _build_error_summary(self, errors: list) -> dict:
        """Build a compact, deduplicated error summary for the LLM."""
        groups = {}
        for err in errors:
            # Normalize: remove UUIDs, array indices, line numbers
            msg = err["message"]
            norm = re.sub(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', '<UUID>', msg)
            norm = re.sub(r'\[\d+\]', '[N]', norm)
            if norm not in groups:
                groups[norm] = {
                    "message": msg,
                    "count": 0,
                    "examples": [],
                }
            groups[norm]["count"] += 1
            if len(groups[norm]["examples"]) < 3:
                groups[norm]["examples"].append({
                    "path": err["path"],
                    "line": err.get("line", 0),
                    "message": msg,
                })

        sorted_groups = sorted(groups.values(), key=lambda g: g["count"], reverse=True)
        return {
            "total_errors": len(errors),
            "unique_types": len(sorted_groups),
            "groups": sorted_groups,
        }

    def _llm_fix_validation_errors(self, error_summary: dict):
        """LLM-driven: analyze errors, generate a SURGICAL fix script that patches the JSON in-place."""
        with open(self.bundle_path, "r", encoding="utf-8") as f:
            bundle_content = f.read()

        # Count lines to track data integrity
        original_lines = bundle_content.count('\n')
        original_size = len(bundle_content)

        if len(bundle_content) > 50000:
            bundle_excerpt = bundle_content[:25000] + "\n...[truncated]...\n" + bundle_content[-20000:]
        else:
            bundle_excerpt = bundle_content

        reference_path = os.path.join(self.examples_dir, "reference_95pct_bundle.json")
        example = self._read_file(reference_path)
        if not example or len(example) < 100:
            example = self._read_file(
                os.path.join(self.examples_dir, "Bundle-InsurancePlanBundle-example-01.json")
            )

        error_lines = []
        for group in error_summary["groups"][:30]:
            error_lines.append(f"[{group['count']}x] {group['message']}")
            for ex in group["examples"][:2]:
                error_lines.append(f"      at {ex['path']} (line {ex['line']})")
        error_text = "\n".join(error_lines)

        system = (
            "You are a FHIR R4 expert. Generate a Python script that SURGICALLY fixes "
            "validation errors in a FHIR bundle JSON. The script must ONLY fix the specific "
            "errors listed — it must NOT remove, simplify, or rewrite any data. "
            "Output ONLY Python code."
        )

        user = f"""Fix {error_summary['total_errors']} validation errors in an NHCX InsurancePlanBundle.

BUNDLE_PATH = r"{self.bundle_path}"
Current bundle: {original_lines} lines, {original_size:,} bytes

═══ VALIDATION ERRORS ═══
{error_text}

═══ REFERENCE EXAMPLE (0 errors — copy its structure for fixes) ═══
{example[:15000]}

═══ CURRENT BUNDLE ═══
{bundle_excerpt}

CRITICAL RULES FOR THE FIX SCRIPT:
1. Read the bundle JSON with json.load()
2. Walk the data structure and fix ONLY the specific errors listed
3. NEVER delete coverages, extensions, benefits, plans, or exclusions
4. NEVER rebuild the bundle from scratch — only modify the broken parts
5. After fixes, the bundle MUST have AT LEAST as many lines as before
6. Write back to the SAME path with json.dump(indent=2)
7. Print what was fixed so we can track progress

Common fix patterns:
- "Unknown code" → change code to valid one, or remove coding and keep text only
- "Unrecognized property" → move/rename the property to the correct FHIR location
- "minimum required = 1" → add the missing required element
- Wrong type (array vs object) → restructure without losing data
- Wrong display name → look up the correct display from the reference example

Output ONLY Python code."""

        response = self._llm_call(system, user, max_tokens=16384, temperature=0.1)
        code = self._extract_code_from_response(response)

        if not code:
            print(f"  LLM failed to generate fix script")
            return

        script_path = os.path.join(self.generated_dir, "validation_fix.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)

        returncode, stdout, stderr = self._run_command(f"python3 {script_path}", timeout=120)
        if returncode == 0:
            print(f"  Fix script ran successfully")
            # Verify data wasn't lost
            if os.path.exists(self.bundle_path):
                new_size = os.path.getsize(self.bundle_path)
                if new_size < original_size * 0.5:
                    print(f"  WARNING: Bundle shrank from {original_size:,} to {new_size:,} bytes!")
                    print(f"  This suggests data loss. Restoring may be needed.")
            self._run_dedup_pass()
        else:
            print(f"  Fix script error: {stderr[:200]}")
            fixed_code = self._llm_fix_python_error(code, stderr)
            if fixed_code:
                with open(script_path, "w", encoding="utf-8") as f:
                    f.write(fixed_code)
                rc2, _, se2 = self._run_command(f"python3 {script_path}", timeout=120)
                if rc2 == 0:
                    print(f"  Fix script ran after retry")
                    self._run_dedup_pass()
                else:
                    print(f"  Fix script failed after retry: {se2[:150]}")

    def _run_dedup_pass(self):
        """Quick dedup pass after any script modifies the bundle."""
        if not os.path.exists(self.bundle_path):
            return
        try:
            with open(self.bundle_path, "r", encoding="utf-8") as f:
                bundle = json.load(f)
            removed = self._deduplicate_bundle_obj(bundle)
            removed += self._clean_empty_elements(bundle)
            if removed > 0:
                with open(self.bundle_path, "w", encoding="utf-8") as f:
                    json.dump(bundle, f, indent=2, ensure_ascii=False)
                new_size = os.path.getsize(self.bundle_path)
                print(f"  Dedup pass: removed {removed} items, size now {new_size:,} bytes")
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Dedup pass failed: {e}")

    def _run_fhir_validator(self) -> tuple:
        """Run the FHIR validator and return (errors, warnings, raw_output)."""
        validator_jar = os.path.join(
            self.project_root,
            self.settings.get("paths", {}).get("validator_jar", "validator/validator_cli.jar")
        )

        if not os.path.exists(validator_jar):
            raise RuntimeError(f"Validator JAR not found: {validator_jar}")

        cmd = f"java -jar {validator_jar} {self.bundle_path} -ig ndhm.in"
        timeout = self.settings.get("validator", {}).get("timeout", 600)

        returncode, stdout, stderr = self._run_command(cmd, timeout=timeout)
        raw_output = stdout + "\n" + stderr

        errors = []
        warnings = []

        for line in raw_output.split('\n'):
            line = re.sub(r'\033\[[0-9;]*m', '', line)
            line = line.strip()
            if not line:
                continue

            # Pattern 1: Error @ <path> (line X, colY) ... : <message>
            match = re.match(
                r'(Error|Warning|Information|Fatal)\s+@\s+(.*?)\s+'
                r'\(line\s+(\d+),\s*col\s*(\d+)\)'
                r'(?:\s+in\s+\S+)?'
                r'\s*:\s*(.*)',
                line
            )
            if match:
                entry = {
                    "severity": match.group(1).lower(),
                    "path": match.group(2).strip(),
                    "line": int(match.group(3)),
                    "col": int(match.group(4)),
                    "message": match.group(5).strip(),
                    "raw": line,
                }
                if entry["severity"] in ("error", "fatal"):
                    errors.append(entry)
                elif entry["severity"] == "warning":
                    warnings.append(entry)
                continue

            # Pattern 2: Error @ <path> : <message> (no line/col, e.g. constraint errors)
            match2 = re.match(
                r'(Error|Warning|Information|Fatal)\s+@\s+(.*?)\s*:\s*(.*)',
                line
            )
            if match2:
                entry = {
                    "severity": match2.group(1).lower(),
                    "path": match2.group(2).strip(),
                    "line": 0,
                    "col": 0,
                    "message": match2.group(3).strip(),
                    "raw": line,
                }
                if entry["severity"] in ("error", "fatal"):
                    errors.append(entry)
                elif entry["severity"] == "warning":
                    warnings.append(entry)

        with open(os.path.join(self.logs_dir, "last_validator_output.txt"), "w") as f:
            f.write(raw_output)

        return errors, warnings, raw_output


    # ═══════════════════════════════════════════════════════════
    # STAGE 4: VERIFY COMPLETENESS
    # ═══════════════════════════════════════════════════════════

    def stage_verify(self):
        """Verify the generated bundle carries all information from the PDF."""
        extracted = self._load_json("extracted_data.json")
        if not extracted:
            print("  Cannot verify: extracted_data.json not found")
            return

        # ── Step 1: Programmatic check for missing structural elements ──
        with open(self.bundle_path, "r", encoding="utf-8") as f:
            bundle = json.load(f)

        ip_resource = None
        for entry in bundle.get("entry", []):
            if entry.get("resource", {}).get("resourceType") == "InsurancePlan":
                ip_resource = entry["resource"]
                break

        structural_missing = []
        if ip_resource:
            if not ip_resource.get("plan"):
                structural_missing.append("InsurancePlan.plan entries (sum insured, specific costs)")
            supp_count = sum(1 for e in ip_resource.get("extension", [])
                           if "SupportingInfoRequirement" in e.get("url", ""))
            if supp_count == 0:
                structural_missing.append("Claim-SupportingInfoRequirement extensions (supporting documents)")

        if structural_missing:
            print(f"  Structural gaps found: {len(structural_missing)}")
            for item in structural_missing:
                print(f"    - {item}")

        # ── Step 2: LLM verification ──
        bundle_content = json.dumps(bundle, indent=2)
        if len(bundle_content) > 60000:
            bundle_excerpt = bundle_content[:30000] + "\n...[truncated]...\n" + bundle_content[-30000:]
        else:
            bundle_excerpt = bundle_content

        extracted_json = json.dumps(extracted, indent=2, default=str)
        if len(extracted_json) > 40000:
            extracted_json = extracted_json[:40000] + "\n... [truncated]"

        system = (
            "You are verifying that a FHIR InsurancePlanBundle contains ALL information "
            "extracted from an insurance policy PDF. Compare the extracted data against the bundle "
            "and identify any missing information."
        )

        user = f"""Compare the EXTRACTED DATA against the GENERATED BUNDLE.

Check EACH of these specifically:
1. All coverage sections present? (each section from the PDF should be a coverage entry)
2. All sub-benefits present within each coverage?
3. All exclusions present as Claim-Exclusion extensions?
4. All waiting periods present?
5. InsurancePlan.plan entries present? (with sum insured amounts)
6. Claim-SupportingInfoRequirement extensions present? (supporting documents)
7. Organization details correct?
8. Policy identifiers correct?
9. Conditions/requirements for each coverage captured as Claim-Condition?

EXTRACTED DATA:
{extracted_json}

GENERATED BUNDLE:
{bundle_excerpt}

Return a JSON response:
{{
  "is_complete": true/false,
  "missing_items": ["list of SPECIFIC missing items — be detailed"],
  "present_items_count": number,
  "total_expected_items": number,
  "completeness_percentage": number
}}"""

        response = self._llm_call(system, user, temperature=0.1)
        result = self._extract_json_from_response(response)

        all_missing = list(structural_missing)
        if result:
            pct = result.get("completeness_percentage", 0)
            llm_missing = result.get("missing_items", [])
            all_missing.extend(llm_missing)

            if not all_missing:
                print(f"  Completeness verified: {pct}%")
                return
            else:
                print(f"  Completeness: {pct}% — {len(all_missing)} missing items")
                for item in all_missing[:15]:
                    print(f"    - {item}")

        if all_missing:
            # ── Step 3: Add missing items via LLM-generated Python script ──
            max_add_attempts = 3
            for attempt in range(max_add_attempts):
                print(f"\n  Adding missing items (attempt {attempt + 1}/{max_add_attempts})...")
                self._add_missing_items(all_missing, extracted)

                # Re-run postprocess to fix any structural issues from additions
                print("  Re-running postprocess after additions...")
                self.stage_postprocess()

                # Re-validate
                print("  Re-validating after additions...")
                errors, warnings, _ = self._run_fhir_validator()
                if errors:
                    print(f"  {len(errors)} errors after additions — running fix loop...")
                    self.stage_validate_and_fix()
                    # Run postprocess again after fixes
                    self.stage_postprocess()
                else:
                    print(f"  0 errors after additions")

                # Re-check completeness
                with open(self.bundle_path) as f:
                    updated = json.load(f)
                for entry in updated.get("entry", []):
                    if entry.get("resource", {}).get("resourceType") == "InsurancePlan":
                        has_plans = bool(entry["resource"].get("plan"))
                        has_supp = any("SupportingInfo" in e.get("url", "")
                                      for e in entry["resource"].get("extension", []))
                        if has_plans and has_supp:
                            print(f"  All structural elements now present")
                            return
                        break

    def _add_missing_items(self, missing_items: list, extracted: dict):
        """Add missing items to the bundle."""
        with open(self.bundle_path, "r", encoding="utf-8") as f:
            bundle = json.load(f)

        bundle_json = json.dumps(bundle, indent=2)
        if len(bundle_json) > 50000:
            bundle_json = bundle_json[:25000] + "\n...[truncated]...\n" + bundle_json[-25000:]

        extracted_json = json.dumps(extracted, indent=2, default=str)
        if len(extracted_json) > 30000:
            extracted_json = extracted_json[:30000] + "\n... [truncated]"

        system = (
            "You are adding missing data to a FHIR InsurancePlanBundle. "
            "Given the missing items and the current bundle, generate a Python script "
            "that reads the current bundle JSON, adds the missing items in the correct FHIR structure, "
            "and writes the updated bundle. Output ONLY Python code."
        )

        reference_path = os.path.join(self.examples_dir, "reference_95pct_bundle.json")
        example = self._read_file(reference_path)
        if not example or len(example) < 100:
            example = self._read_file(
                os.path.join(self.examples_dir, "Bundle-InsurancePlanBundle-example-01.json")
            )

        missing_text = "\n".join(f"- {item}" for item in missing_items[:25])
        user = f"""Add these missing items to the FHIR bundle:

CRITICAL: Use this EXACT path for reading and writing the bundle (do NOT construct your own):
BUNDLE_PATH = r"{self.bundle_path}"

MISSING ITEMS:
{missing_text}

CRITICAL RULES:
- Claim-Exclusion codes: ONLY use valid codes (Excl01-Excl18) with EXACT display names.
  For exclusions without a valid code, use text-only: {{"text": "description"}}
  NEVER use code "unknown" — it causes ALL validation to fail.
- InsurancePlan.plan must have: identifier, type (ndhm-plan-type), generalCost, specificCost
- Claim-SupportingInfoRequirement: use category (POI/POA) and code (ADN/PPN/DL etc.)
- SNOMED codes: only use known valid codes (737481003, 710967003, etc.) or text-only

EXAMPLE BUNDLE (reference for correct structure):
{example[:8000]}

EXTRACTED DATA (source of truth):
{extracted_json}

CURRENT BUNDLE:
{bundle_json}

Generate a Python script that:
1. Reads the bundle from BUNDLE_PATH = r"{self.bundle_path}"
2. Adds the missing items in correct FHIR structure
3. Preserves ALL existing data
4. Writes back to the SAME path

Output ONLY Python code."""

        response = self._llm_call(system, user, max_tokens=16384, temperature=0.15)
        code = self._extract_code_from_response(response)

        if code:
            script_path = os.path.join(self.generated_dir, "add_missing.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)


            returncode, stdout, stderr = self._run_command(f"python3 {script_path}", timeout=60)
            if returncode == 0:
                print(f"  Added missing items successfully")
                self._run_dedup_pass()
            else:
                print(f"  Script error, fixing: {stderr[:150]}")
                fixed_code = self._llm_fix_python_error(code, stderr)
                if fixed_code:
                    with open(script_path, "w", encoding="utf-8") as f:
                        f.write(fixed_code)
                    rc2, _, se2 = self._run_command(f"python3 {script_path}", timeout=120)
                    if rc2 == 0:
                        print(f"  Added missing items (after script fix)")
                        self._run_dedup_pass()
                    else:
                        print(f"  Failed to add missing items: {se2[:200]}")

    # ═══════════════════════════════════════════════════════════
    # HELPER METHODS
    # ═══════════════════════════════════════════════════════════

    def _build_reference_template(self) -> str:
        """Extract a condensed structural template from the reference bundle.
        Shows one example of each key pattern (coverage, exclusion, plan, org)
        instead of dumping the full 200KB file."""
        ref_path = os.path.join(self.examples_dir, "reference_95pct_bundle.json")
        ref_data = self._load_json_file(ref_path)
        if not ref_data:
            return "(reference bundle not available)"

        template_parts = []

        for entry in ref_data.get("entry", []):
            res = entry.get("resource", {})
            if res.get("resourceType") == "InsurancePlan":
                # Show InsurancePlan type
                ip_type = res.get("type", [])
                template_parts.append(f"InsurancePlan.type: {json.dumps(ip_type, indent=2)}")

                # Show first SupportingInfoRequirement extension
                for ext in res.get("extension", []):
                    if "SupportingInfoRequirement" in ext.get("url", ""):
                        template_parts.append(
                            f"EXAMPLE SupportingInfoRequirement extension:\n{json.dumps(ext, indent=2)}")
                        break

                # Show first Claim-Exclusion with coded category
                for ext in res.get("extension", []):
                    if "Claim-Exclusion" in ext.get("url", ""):
                        template_parts.append(
                            f"EXAMPLE Claim-Exclusion extension:\n{json.dumps(ext, indent=2)}")
                        break

                # Show first coverage with Claim-Condition
                covs = res.get("coverage", [])
                if covs:
                    first_cov = covs[0]
                    cov_compact = {
                        "type": first_cov.get("type"),
                        "benefit": first_cov.get("benefit", [])[:1],
                    }
                    if first_cov.get("extension"):
                        cov_compact["extension"] = first_cov["extension"][:1]
                    template_parts.append(
                        f"EXAMPLE coverage entry (1 of {len(covs)}):\n{json.dumps(cov_compact, indent=2)}")

                # Show first plan entry
                plans = res.get("plan", [])
                if plans:
                    template_parts.append(
                        f"EXAMPLE plan entry (1 of {len(plans)}):\n{json.dumps(plans[0], indent=2)}")

                # Summary
                n_ext = len(res.get("extension", []))
                n_excl = sum(1 for e in res.get("extension", []) if "Claim-Exclusion" in e.get("url", ""))
                n_supp = sum(1 for e in res.get("extension", []) if "SupportingInfo" in e.get("url", ""))
                template_parts.append(
                    f"Reference totals: {len(covs)} coverages, {n_excl} exclusions, "
                    f"{n_supp} supporting info, {len(plans)} plans, {n_ext} total extensions")

            elif res.get("resourceType") == "Organization":
                template_parts.append(f"EXAMPLE Organization:\n{json.dumps(res, indent=2)}")

        return "\n\n".join(template_parts)

    def _llm_call(self, system_prompt: str, user_prompt: str,
                  max_tokens: int = None, temperature: float = None) -> str:
        """Make a single LLM call with optional parameter overrides."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        orig_max = self.llm.max_tokens
        orig_temp = self.llm.temperature
        try:
            if max_tokens:
                self.llm.max_tokens = max_tokens
            if temperature is not None:
                self.llm.temperature = temperature

            result = self.llm.chat(messages)
            content = result.get("content", "") or ""
            return content
        finally:
            self.llm.max_tokens = orig_max
            self.llm.temperature = orig_temp

    def _extract_json_from_response(self, response: str) -> dict:
        """Extract JSON from an LLM response, handling code blocks and mixed text."""
        if not response:
            return None

        # Try markdown code blocks first
        for pattern in [r'```json\s*(.*?)\s*```', r'```\s*(.*?)\s*```']:
            match = re.search(pattern, response, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue

        # Try parsing the whole response
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # Find the outermost JSON object or array
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            first = response.find(start_char)
            if first < 0:
                continue
            # Find matching close, counting nesting
            depth = 0
            for i in range(first, len(response)):
                if response[i] == start_char:
                    depth += 1
                elif response[i] == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(response[first:i + 1])
                        except json.JSONDecodeError:
                            break

        return None

    def _extract_code_from_response(self, response: str) -> str:
        """Extract Python code from an LLM response."""
        if not response:
            return None

        # Try markdown code blocks
        for pattern in [r'```python\s*(.*?)\s*```', r'```py\s*(.*?)\s*```', r'```\s*(.*?)\s*```']:
            match = re.search(pattern, response, re.DOTALL)
            if match:
                code = match.group(1)
                if 'import' in code or 'def ' in code or 'json' in code:
                    return code

        # If no code blocks, check if the whole response is code
        if ('import ' in response or 'def ' in response) and ('json' in response or 'uuid' in response):
            # Strip any leading non-code text
            lines = response.split('\n')
            code_start = 0
            for i, line in enumerate(lines):
                if line.startswith(('import ', 'from ', '#!', '"""', "'''", '#')):
                    code_start = i
                    break
            return '\n'.join(lines[code_start:])

        return response

    def _read_file(self, path: str) -> str:
        """Read a file and return its content."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except FileNotFoundError:
            logger.error(f"File not found: {path}")
            return ""

    def _write_json(self, filename: str, data: dict):
        """Write JSON to workspace/generated/."""
        path = os.path.join(self.generated_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    def _load_json(self, filename: str) -> dict:
        """Load JSON from workspace/generated/."""
        path = os.path.join(self.generated_dir, filename)
        return self._load_json_file(path)

    def _load_json_file(self, path: str) -> dict:
        """Load JSON from any path."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load JSON from {path}: {e}")
            return None

    def _run_command(self, cmd: str, timeout: int = 120) -> tuple:
        """Run a shell command. Returns (returncode, stdout, stderr)."""
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=self.project_root,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"Command timed out after {timeout}s"
        except Exception as e:
            return -1, "", str(e)

    def _find_pdf_markdown(self, pdf_path: str = None) -> str:
        """Find the PDF markdown file."""
        if pdf_path:
            if pdf_path.endswith('.md') and os.path.exists(pdf_path):
                return pdf_path
            # Convert pdf path to md path
            md_path = os.path.splitext(pdf_path)[0] + '.md'
            if os.path.exists(md_path):
                return md_path

        # Search in pdf_dir
        if os.path.exists(self.pdf_dir):
            for f in sorted(os.listdir(self.pdf_dir)):
                if f.endswith('.md'):
                    return os.path.join(self.pdf_dir, f)

        return None

    def _print_stage_header(self, stage_num: int, title: str):
        print(f"\n{'═' * 60}")
        print(f"  STAGE {stage_num}: {title}")
        elapsed = (datetime.now() - self.start_time).total_seconds()
        print(f"  Elapsed: {elapsed:.0f}s | LLM calls: {self.llm.total_calls}")
        print(f"{'═' * 60}\n")

    def _print_final_summary(self, result: dict):
        print(f"\n{'═' * 60}")
        print(f"  PIPELINE COMPLETE")
        print(f"  Status: {result['status']}")
        print(f"  Duration: {result['duration_seconds']:.1f}s")
        print(f"  LLM calls: {result['llm_usage']['total_calls']}")
        print(f"  Total tokens: {result['llm_usage']['total_tokens']:,}")
        print(f"  Bundle: {self.bundle_path}")
        if os.path.exists(self.bundle_path):
            size = os.path.getsize(self.bundle_path)
            print(f"  Bundle size: {size:,} bytes")
        print(f"{'═' * 60}\n")
