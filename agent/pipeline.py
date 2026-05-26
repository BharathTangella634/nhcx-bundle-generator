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
SNOMED_LOOKUP = {
    "inpatient": ("737481003", "Inpatient care management (procedure)"),
    "inpatient_care": ("737481003", "Inpatient care management (procedure)"),
    "hospitalization": ("737481003", "Inpatient care management (procedure)"),
    "post_discharge": ("710967003", "Management of health status after discharge from hospital (procedure)"),
    "post_hospitalization": ("710967003", "Management of health status after discharge from hospital (procedure)"),
    "pre_hospital": ("409972000", "Pre-hospital care (situation)"),
    "pre_hospitalization": ("409972000", "Pre-hospital care (situation)"),
    "ambulance": ("49122002", "Ambulance, device (physical object)"),
    "day_care": ("737850002", "Day care case management"),
    "daycare": ("737850002", "Day care case management"),
    "organ_donor": ("105461009", "Organ donor"),
    "organ_transplant": ("105461009", "Organ donor"),
    "icu": ("309904001", "Intensive care unit (environment)"),
    "iccu": ("309904001", "Intensive care unit (environment)"),
    "blood": ("87612001", "Blood"),
    "oxygen": ("24099007", "Oxygen (substance)"),
    "single_room": ("224663004", "Single room (environment)"),
    "room_rent": ("224663004", "Single room (environment)"),
    "home_care": ("60689008", "Home care of patient"),
    "domiciliary": ("60689008", "Home care of patient"),
    "pharmacy": ("373784001", "Pharmacy service (procedure)"),
    "consultation": ("11429006", "Consultation (procedure)"),
    "doctor_consultation": ("11429006", "Consultation (procedure)"),
    "diagnostic": ("165340005", "Laboratory test finding (finding)"),
    "maternity": ("118189007", "Prenatal finding (finding)"),
    "newborn": ("133906008", "Newborn care (regime/therapy)"),
    "ayush": ("716186003", "No known allergy (situation)"),  # placeholder — no exact SNOMED for AYUSH
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

            # ──── STAGE 2.5: POST-PROCESS (deterministic fixes) ────
            self._print_stage_header(2, "POST-PROCESS BUNDLE (deterministic fixes)")
            self.stage_postprocess()
            self.stage_results["postprocess"] = "complete"

            # ──── STAGE 3: VALIDATE & FIX ────
            self._print_stage_header(3, "VALIDATE & FIX ERRORS")
            self.stage_validate_and_fix()
            self.stage_results["validate"] = "complete"

            # ──── STAGE 4: VERIFY COMPLETENESS ────
            self._print_stage_header(4, "VERIFY COMPLETENESS")
            self.stage_verify()
            self.stage_results["verify"] = "complete"

            # ──── STAGE 5: FINAL VALIDATION ────
            self._print_stage_header(5, "FINAL VALIDATION")
            errors, warnings, _ = self._run_fhir_validator()
            self.stage_results["final_errors"] = len(errors)
            self.stage_results["final_warnings"] = len(warnings)
            if len(errors) == 0:
                print(f"  VALIDATION PASSED: 0 errors, {len(warnings)} warnings")
            else:
                print(f"  {len(errors)} errors remain, {len(warnings)} warnings")
                for e in errors[:20]:
                    print(f"    - {e['message'][:120]}")

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
            for key in ["waiting_periods", "permanent_exclusions", "supporting_documents"]:
                if key in excl_wait:
                    extracted[key] = excl_wait[key]

        # Ensure required keys exist
        for key in ["insurer", "policy", "coverages", "waiting_periods",
                     "permanent_exclusions", "supporting_documents"]:
            if key not in extracted:
                extracted[key] = {} if key in ["insurer", "policy"] else []

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
        n_wait = len(extracted.get("waiting_periods", []))
        print(f"  Saved extracted_data.json: {n_cov} coverages, {n_excl} exclusions, {n_wait} waiting periods")

    def _extraction_prompt_org_policy(self) -> str:
        return """Extract the INSURER/ORGANIZATION and POLICY information from this insurance policy document.

Return a JSON object with EXACTLY this structure (fill in every field you can find; use null for unknown):
{
  "insurer": {
    "name": "Full legal name of the insurance company",
    "subsidiary_of": "Parent company if mentioned",
    "product_uin": "Product UIN number (e.g., ADIHLGP22023V032122)",
    "rohini_id": "ROHINI ID if mentioned, otherwise null",
    "irdai_registration": "IRDAI registration number if mentioned, otherwise null",
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
    "insurance_type_code": "01 for Hospitalization Indemnity, 02 for Group, etc."
  }
}

IMPORTANT:
- The UIN is often found in headers/footers like "Product UIN: ADIHLGP22023V032122"
- Extract the EXACT text, do not paraphrase
- Return ONLY the JSON, no markdown formatting"""

    def _extraction_prompt_coverages(self) -> str:
        return """Extract ALL coverage/benefit sections from this insurance policy.

The policy has multiple sections (Section II.1, II.2, II.3, etc.). Each section describes a type of coverage.
You MUST extract EVERY section — do not skip any.

Return a JSON object:
{
  "coverages": [
    {
      "section_id": "Section number like II.1",
      "name": "Coverage name like OPD Expenses",
      "type_hint": "One of: outpatient, cancer, critical_illness, inpatient, hospitalization, day_care, pre_hospitalization, post_hospitalization, ambulance, organ_donor, maternity, newborn, hospital_cash, domiciliary, ayush, preferred_provider, wellness, or other",
      "description": "Brief description of what this coverage provides",
      "conditions": ["List of conditions/requirements for this coverage to apply"],
      "sub_benefits": [
        {
          "name": "Sub-benefit name like Doctor Consultation",
          "what_is_covered": "Full description of what is covered",
          "what_is_not_covered": "Full description of what is NOT covered",
          "limit_description": "Any limits, sub-limits, co-pay mentioned"
        }
      ],
      "section_specific_exclusions": ["List of exclusions specific to this section"],
      "options": [
        {
          "option_name": "Option 1 or Option 2 etc.",
          "description": "What this option covers",
          "payout_details": "Payout amounts or percentages"
        }
      ],
      "snomed_code": "SNOMED CT code if you are CERTAIN of the correct code, otherwise null",
      "snomed_display": "SNOMED CT display text if code provided, otherwise null"
    }
  ]
}

CRITICAL RULES:
- Include EVERY section from II.1 through the last section number
- For each section, extract ALL sub-benefits, conditions, and exclusions
- Include ALL options/variants if a section has multiple options
- Do NOT skip tables — extract tabular data fully
- For SNOMED codes: ONLY include if you are 100% certain. These well-known codes are safe to use:
  * 737481003 = Inpatient care management
  * 737850002 = Day care case management
  * 409972000 = Pre-hospital care
  * 710967003 = Post-discharge management
  * 49122002 = Ambulance
  * 105461009 = Organ donor
  * 373784001 = Pharmacy service
  * 11429006 = Consultation
  For anything else, set snomed_code to null.
- Return ONLY the JSON, no markdown formatting"""

    def _extraction_prompt_exclusions(self) -> str:
        return """Extract ALL exclusions, waiting periods, and supporting document requirements from this insurance policy.

Return a JSON object:
{
  "waiting_periods": [
    {
      "code": "Exclusion code like Excl01, Excl02, Excl03",
      "name": "Name like Pre-Existing Diseases",
      "period": "Duration like 48 months, 24 months, 30 days",
      "description": "FULL verbatim description of this waiting period",
      "applicable_diseases": ["COMPLETE list of ALL diseases/conditions subject to this waiting period - extract EVERY disease from any tables"],
      "applicable_procedures": ["COMPLETE list of ALL procedures/surgeries subject to this waiting period"]
    }
  ],
  "permanent_exclusions": [
    {
      "code": "Exclusion code like Excl10, Excl09, Excl06, etc. Use null if no code mentioned",
      "name": "Short name of the exclusion",
      "description": "FULL verbatim description — do NOT summarize, include the complete text"
    }
  ],
  "supporting_documents": [
    {
      "category_code": "POI or POA or OTHER",
      "category_display": "Proof of Identity or Proof of Address etc.",
      "document_code": "ADN or PPN or DL etc.",
      "document_display": "Aadhaar number or Passport number etc."
    }
  ]
}

CRITICAL RULES:
- For the 2-year waiting period (Excl02), there is typically a LARGE TABLE of diseases and procedures. You MUST extract EVERY disease and procedure from that table. Do not summarize or truncate.
- For permanent exclusions (Section IV), extract EVERY numbered exclusion
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
        # Show a summary of the extracted data structure instead of the full data,
        # since the script will read from the file directly.
        data_summary = {
            "top_level_keys": list(extracted.keys()),
            "insurer": extracted.get("insurer", {}),
            "policy": extracted.get("policy", {}),
            "num_coverages": len(extracted.get("coverages", [])),
            "coverage_names": [c.get("name", c.get("category", "?")) for c in extracted.get("coverages", [])],
            "num_exclusions": len(extracted.get("permanent_exclusions", extracted.get("exclusions", []))),
            "num_waiting_periods": len(extracted.get("waiting_periods", [])),
            "num_supporting_docs": len(extracted.get("supporting_documents", [])),
        }
        # Show first coverage as structural example
        coverages = extracted.get("coverages", [])
        if coverages:
            data_summary["sample_coverage"] = coverages[0]
        exclusions = extracted.get("permanent_exclusions", extracted.get("exclusions", []))
        if exclusions:
            data_summary["sample_exclusion"] = exclusions[0]
        waiting_periods = extracted.get("waiting_periods", [])
        if waiting_periods:
            data_summary["sample_waiting_period"] = waiting_periods[0]
        supporting_docs = extracted.get("supporting_documents", [])
        if supporting_docs:
            data_summary["sample_supporting_doc"] = supporting_docs[0]
        data_summary_str = json.dumps(data_summary, indent=2, default=str)

        if len(rulebook_ip) > 40000:
            rulebook_ip = rulebook_ip[:40000] + "\n... [truncated]"
        if len(rulebook_org) > 15000:
            rulebook_org = rulebook_org[:15000] + "\n... [truncated]"

        system = (
            "You are an expert FHIR developer specializing in NHCX/ABDM-compliant insurance bundles. "
            "Generate a complete, working Python script that reads extracted data from a JSON file and "
            "produces a valid FHIR InsurancePlanBundle JSON file. "
            "The script must iterate over ALL coverages, exclusions, waiting periods, and supporting documents. "
            "Output ONLY the Python code — no markdown code blocks, no explanations."
        )

        snomed_map_str = json.dumps({k: {"code": v[0], "display": v[1]} for k, v in SNOMED_LOOKUP.items()}, indent=2)

        user = f"""Generate a Python3 script that reads extracted insurance data and creates an NHCX-compliant FHIR InsurancePlanBundle.

═══ FILE PATHS (CRITICAL — use exactly these) ═══
The script will have a global constant OUTPUT_PATH injected at the top. Use it as output path.
Input file (extracted data): {self.extracted_data_path}
Output file: {self.bundle_path} (use OUTPUT_PATH constant)
Do NOT use os.getcwd() or construct your own paths.

═══ HOW THE SCRIPT MUST WORK ═══
1. Read extracted data from {self.extracted_data_path} using json.load()
2. Build the FHIR bundle from the data by iterating over ALL items
3. Write the bundle to OUTPUT_PATH using json.dump()

═══ EXTRACTED DATA STRUCTURE (the script reads this from the JSON file) ═══
{data_summary_str}

Key fields in the extracted data:
- data["insurer"] — dict with name, registration_number, address, contact, etc.
- data["policy"] — dict with product_name, plan_type, etc.
- data["coverages"] — list of dicts, each with name/category, benefits, conditions, limits, sub_limits
- data["permanent_exclusions"] — list of dicts with name, description, category
- data["waiting_periods"] — list of dicts with name, period, description, category
- data["supporting_documents"] — list of dicts with category, document_type, description
- data.get("_verification_additions", {{}}) — may have extra items to merge

═══ MANDATORY STRUCTURE RULES ═══
1. Bundle.type MUST be "collection"
2. Bundle entries MUST NOT have "request" or "response" properties
3. InsurancePlan.type MUST be an array with EXACTLY 1 entry (max cardinality = 1)
4. coverage.type is a CodeableConcept (NOT an array)
5. plan.type is a CodeableConcept (NOT an array)

═══ EXTENSION PLACEMENT RULES (CRITICAL — violations cause errors) ═══
- Claim-SupportingInfoRequirement: goes on InsurancePlan.extension[] (top level)
- Claim-Exclusion: goes on InsurancePlan.extension[] (top level)
- Claim-Condition: goes on coverage.extension[] OR coverage.benefit.extension[]
  DO NOT put Claim-Condition on InsurancePlan.type, or anywhere else.

═══ EXACT EXTENSION FORMAT (from passing example) ═══

Claim-Condition on coverage:
{{"extension": [{{"extension": [{{"url": "claim-condition", "valueString": "condition text"}}], "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Condition"}}], "type": {{"coding": [{{"system": "http://snomed.info/sct", "code": "737481003", "display": "Inpatient care management (procedure)"}}]}}}}

Claim-Exclusion on InsurancePlan:
{{"extension": [{{"url": "category", "valueCodeableConcept": {{"coding": [{{"system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion", "code": "Excl01", "display": "Pre-Existing Diseases"}}]}}}}, {{"url": "statement", "valueString": "full exclusion text"}}], "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Exclusion"}}

Claim-SupportingInfoRequirement on InsurancePlan:
{{"extension": [{{"url": "category", "valueCodeableConcept": {{"coding": [{{"system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-supportinginfo-category", "code": "POI", "display": "Proof of identity"}}]}}}}, {{"url": "code", "valueCodeableConcept": {{"coding": [{{"system": "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code", "code": "ADN", "display": "Adhaar number"}}]}}}}], "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-SupportingInfoRequirement"}}

═══ SNOMED CODES (only use these — for anything else, use text-only) ═══
{snomed_map_str}

If a coverage type doesn't match any known SNOMED code, use text-only: {{"text": "description"}}
NEVER use code "unknown" with SNOMED CT system.

═══ KEY URLS ═══
- Bundle profile: "https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlanBundle"
- InsurancePlan profile: "https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlan"
- Organization profile: "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Organization"
- IRDAI: "https://irdai.gov.in"
- ROHINI: "https://rohini.iib.gov.in/"
- InsurancePlan Type CS: "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-insuranceplan-type"
- Plan Type CS: "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-plan-type"

═══ TEXT ELEMENT RULES ═══
- Every resource MUST have text.status and text.div
- If resource has extensions: text.status = "extensions"
- If resource has no extensions: text.status = "generated"
- text.div MUST be valid XHTML: <div xmlns="http://www.w3.org/1999/xhtml">...</div>

═══ PLAN ENTRIES ═══
Each InsurancePlan.plan entry MUST have:
- identifier (array with use="official", value=plan_name)
- type (CodeableConcept with ndhm-plan-type coding)
- generalCost (array with cost.value and cost.currency="INR")
- specificCost (optional, for sub-limits per benefit type)

═══ ORGANIZATION ═══
Must have: identifier with ROHINI type coding + system + value, name, telecom

═══ EXCLUSION CODES (use EXACT display names — wrong display causes cascade failures!) ═══
EXCLUSION_CODES dict to include in script:
Excl01 = "Pre-Existing Diseases"
Excl02 = "Specified disease/procedure waiting period"
Excl03 = "30-day waiting period"
Excl04 = "Investigation Evaluation"
Excl05 = "Rest Cure,Rehabilitation and Respite Care"
Excl06 = "Obesity/Weight Control"
Excl07 = "Change-of-Gender treatments"
Excl08 = "Cosmetic or plastic Surgery"
Excl09 = "Hazardous or Adventure sports"
Excl10 = "Breach of law"
Excl11 = "Excluded providers"
Excl12 = "Rehabilitation"
Excl13 = "Hydrotherapy"
Excl14 = "Non-prescription"
Excl15 = "Refractive Error"
Excl16 = "Unproven Treatments"
Excl17 = "Sterility and infertility"
Excl18 = "Maternity expenses"

For exclusions that don't match any of the above codes, use text-only: {{"text": "exclusion description"}}
NEVER use code "unknown" — it causes ALL validation to fail.

═══ INSURANCEPLAN TYPE CODES ═══
01 = "Hospitalisation Indemnity Policy"
02 = "Hospital Cash Plan"

═══ COMPLETENESS CHECKLIST (MUST include ALL of these from the extracted data file) ═══
1. Iterate over ALL data["coverages"] → InsurancePlan.coverage[]
   - Each coverage gets a SNOMED-coded type or text-only type
   - Each coverage.benefit should have benefit.type from the coverage data
   - Add Claim-Condition extensions on coverage for conditions/sub_limits
2. Iterate over ALL data["permanent_exclusions"] → InsurancePlan.extension[] as Claim-Exclusion
3. Iterate over ALL data["waiting_periods"] → InsurancePlan.extension[] as Claim-Exclusion (with Excl02/Excl03)
4. Plan entries with sum insured → InsurancePlan.plan[]
5. Iterate over ALL data["supporting_documents"] → InsurancePlan.extension[] as Claim-SupportingInfoRequirement

═══ EXAMPLE BUNDLE (use as EXACT structural reference — this passes validation with 0 errors) ═══
{example}

═══ InsurancePlan RULEBOOK (constraints) ═══
{rulebook_ip[:30000]}

═══ Organization RULEBOOK ═══
{rulebook_org[:10000]}

═══ Bundle RULEBOOK ═══
{rulebook_bundle}

IMPORTANT: The script MUST read from {self.extracted_data_path} and iterate over ALL items.
Do NOT hardcode data — read it from the JSON file.
The script must produce a bundle with ALL {len(coverages)} coverages, ALL {len(exclusions)} exclusions, ALL {len(waiting_periods)} waiting periods, and ALL {len(supporting_docs)} supporting documents.
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
        "737481003", "710967003", "409972000", "49122002", "737850002",
        "105461009", "309904001", "87612001", "24099007", "224663004",
        "60689008", "373784001", "11429006", "165340005", "118189007",
        "133906008", "716186003", "86077009",
    }

    def stage_postprocess(self):
        """Deterministic structural fixes applied before validation."""
        if not os.path.exists(self.bundle_path):
            raise RuntimeError(f"Bundle not found: {self.bundle_path}")

        with open(self.bundle_path, "r", encoding="utf-8") as f:
            bundle = json.load(f)

        fixes = 0

        # ── Fix 1: Remove request/response from entries (collection bundles) ──
        for entry in bundle.get("entry", []):
            if "request" in entry:
                del entry["request"]
                fixes += 1
            if "response" in entry:
                del entry["response"]
                fixes += 1
        if fixes:
            print(f"  [fix] Removed request/response from entries ({fixes} fixes)")

        # ── Fix 2: Ensure InsurancePlan.type has exactly 1 entry ──
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") != "InsurancePlan":
                continue

            type_arr = resource.get("type", [])
            if len(type_arr) > 1:
                resource["type"] = [type_arr[0]]
                print(f"  [fix] InsurancePlan.type trimmed from {len(type_arr)} to 1")
                fixes += 1

            # Ensure the single type entry has correct structure
            if resource.get("type"):
                t = resource["type"][0]
                # Remove any extension/benefit that got misplaced into type
                for bad_key in ["extension", "benefit", "type"]:
                    if bad_key in t and bad_key != "coding" and bad_key != "text":
                        del t[bad_key]
                        fixes += 1

        # ── Fix 3: Fix unknown SNOMED codes ──
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") != "InsurancePlan":
                continue

            for coverage in resource.get("coverage", []):
                self._fix_snomed_in_codeable_concept(coverage.get("type", {}))
                for benefit in coverage.get("benefit", []):
                    self._fix_snomed_in_codeable_concept(benefit.get("type", {}))

            for plan in resource.get("plan", []):
                for sc in plan.get("specificCost", []):
                    self._fix_snomed_in_codeable_concept(sc.get("category", {}))
                    for b in sc.get("benefit", []):
                        self._fix_snomed_in_codeable_concept(b.get("type", {}))

        # ── Fix 4: Fix text.status for resources with extensions ──
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("extension"):
                text = resource.get("text", {})
                if text.get("status") == "generated":
                    text["status"] = "extensions"
                    resource["text"] = text
                    fixes += 1

        # ── Fix 5: Ensure all entries have fullUrl ──
        for entry in bundle.get("entry", []):
            if "fullUrl" not in entry:
                resource = entry.get("resource", {})
                rid = resource.get("id", str(uuid.uuid4()))
                entry["fullUrl"] = f"urn:uuid:{rid}"
                fixes += 1

        # ── Fix 6: Ensure bundle has required fields ──
        if "type" not in bundle:
            bundle["type"] = "collection"
            fixes += 1
        if "timestamp" not in bundle:
            bundle["timestamp"] = datetime.now().isoformat() + "Z"
            fixes += 1

        # ── Fix 7: Ensure meta.profile on bundle ──
        if "meta" not in bundle:
            bundle["meta"] = {}
        if "profile" not in bundle.get("meta", {}):
            bundle["meta"]["profile"] = ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlanBundle"]
            fixes += 1
        if "security" not in bundle.get("meta", {}):
            bundle["meta"]["security"] = [{
                "system": "http://terminology.hl7.org/CodeSystem/v3-Confidentiality",
                "code": "V",
                "display": "very restricted"
            }]
            fixes += 1

        # ── Fix 8: Fix Claim-Exclusion codes and display names (CASCADE FIX) ──
        # Invalid codes or wrong display names cause ALL other extensions to fail
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") != "InsurancePlan":
                continue
            fixed_exts = []
            for ext in resource.get("extension", []):
                url = ext.get("url", "")
                if "Claim-Exclusion" in url:
                    ext = self._fix_exclusion_extension(ext)
                    if ext:
                        fixed_exts.append(ext)
                        fixes += 1
                else:
                    fixed_exts.append(ext)
            resource["extension"] = fixed_exts
        print(f"  [fix] Fixed Claim-Exclusion codes/display names")

        # ── Fix 9: Fix InsurancePlan type display names ──
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") != "InsurancePlan":
                continue
            for t in resource.get("type", []):
                for coding in t.get("coding", []):
                    system = coding.get("system", "")
                    code = coding.get("code", "")
                    if "ndhm-insuranceplan-type" in system and code in INSURANCEPLAN_TYPE_DISPLAY:
                        correct_display = INSURANCEPLAN_TYPE_DISPLAY[code]
                        if coding.get("display") != correct_display:
                            coding["display"] = correct_display
                            fixes += 1

        # ── Fix 10: Fix extension structure for Claim-Condition ──
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") != "InsurancePlan":
                continue
            for coverage in resource.get("coverage", []):
                self._fix_claim_condition_extensions(coverage)
                for benefit in coverage.get("benefit", []):
                    self._fix_claim_condition_extensions(benefit)

        # ── Fix 11: Ensure coverage.type is CodeableConcept (not array) ──
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") != "InsurancePlan":
                continue
            for coverage in resource.get("coverage", []):
                ctype = coverage.get("type")
                if isinstance(ctype, list):
                    coverage["type"] = ctype[0] if ctype else {"text": "Unknown"}
                    fixes += 1

        with open(self.bundle_path, "w", encoding="utf-8") as f:
            json.dump(bundle, f, indent=2, ensure_ascii=False)

        print(f"  Total deterministic fixes applied: {fixes}")

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

    def _fix_exclusion_extension(self, ext: dict) -> dict:
        """Fix a Claim-Exclusion extension: correct codes and display names."""
        sub_exts = ext.get("extension", [])
        new_subs = []
        for se in sub_exts:
            if se.get("url") == "category":
                cc = se.get("valueCodeableConcept", {})
                codings = cc.get("coding", [])
                if codings:
                    code = codings[0].get("code", "")
                    if code in EXCLUSION_CODE_DISPLAY:
                        # Valid code — fix the display name
                        codings[0]["display"] = EXCLUSION_CODE_DISPLAY[code]
                        codings[0]["system"] = "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-claim-exclusion"
                        new_subs.append(se)
                    else:
                        # Invalid code — convert to text-only
                        display = codings[0].get("display", cc.get("text", "Unknown exclusion"))
                        new_subs.append({
                            "url": "category",
                            "valueCodeableConcept": {"text": display}
                        })
                else:
                    new_subs.append(se)
            else:
                new_subs.append(se)

        return {
            "extension": new_subs,
            "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Exclusion"
        }

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
        """Run FHIR validator, parse errors, fix them iteratively."""
        if not os.path.exists(self.bundle_path):
            raise RuntimeError(f"Bundle not found: {self.bundle_path}")

        max_rounds = 50
        prev_error_count = float('inf')
        stall_count = 0
        comprehensive_attempts = 0
        max_comprehensive = 8

        for round_num in range(max_rounds):
            print(f"\n  ── Validation round {round_num + 1}/{max_rounds} ──")
            errors, warnings, raw_output = self._run_fhir_validator()

            error_count = len(errors)
            warning_count = len(warnings)
            print(f"  Errors: {error_count}, Warnings: {warning_count}")

            if error_count == 0:
                print(f"  VALIDATION PASSED: 0 errors!")
                self.stage_results["validation_rounds"] = round_num + 1
                self.stage_results["final_errors"] = 0
                self.stage_results["final_warnings"] = warning_count
                return

            # ── Step A: Try deterministic fixes first ──
            det_fixes = self._apply_deterministic_fixes(errors)
            if det_fixes > 0:
                print(f"  Applied {det_fixes} deterministic fixes. Re-validating...")
                continue

            # ── Step B: Check for stall ──
            if error_count >= prev_error_count:
                stall_count += 1
            else:
                stall_count = 0
            prev_error_count = error_count

            # ── Step C: Group errors and fix via LLM ──
            error_groups = self._group_errors(errors)
            print(f"  Unique error types: {len(error_groups)}")

            with open(self.bundle_path, "r", encoding="utf-8") as f:
                bundle = json.load(f)

            fixes_applied = 0
            for group_key, group_errors in sorted(error_groups.items(),
                                                    key=lambda x: len(x[1]), reverse=True):
                count = len(group_errors)
                sample = group_errors[0]
                print(f"    Fixing [{count}x]: {sample['message'][:100]}...")

                fixed_bundle = self._fix_error_group(bundle, group_errors)
                if fixed_bundle:
                    bundle = fixed_bundle
                    fixes_applied += 1

            if fixes_applied > 0:
                with open(self.bundle_path, "w", encoding="utf-8") as f:
                    json.dump(bundle, f, indent=2, ensure_ascii=False)
                print(f"  Applied {fixes_applied} LLM fix groups. Re-validating...")
                continue

            # ── Step D: Comprehensive fix when stalled or no individual fixes work ──
            if stall_count >= 3 or fixes_applied == 0:
                if comprehensive_attempts < max_comprehensive:
                    comprehensive_attempts += 1
                    print(f"  Trying comprehensive fix (attempt {comprehensive_attempts}/{max_comprehensive})...")
                    self._comprehensive_fix(errors, raw_output)
                    stall_count = 0
                else:
                    print(f"  Max comprehensive attempts reached.")
                    break

        self.stage_results["validation_rounds"] = round_num + 1
        self.stage_results["final_errors"] = error_count
        print(f"  Max validation rounds reached. {error_count} errors remain.")

    def _apply_deterministic_fixes(self, errors: list) -> int:
        """Apply deterministic (non-LLM) fixes for known error patterns. Returns fix count."""
        with open(self.bundle_path, "r", encoding="utf-8") as f:
            bundle = json.load(f)

        fixes = 0
        error_messages = [e["message"] for e in errors]

        # Fix: type cardinality (max allowed = 1)
        if any("InsurancePlan.type: max allowed = 1" in m for m in error_messages):
            for entry in bundle.get("entry", []):
                r = entry.get("resource", {})
                if r.get("resourceType") == "InsurancePlan" and len(r.get("type", [])) > 1:
                    r["type"] = [r["type"][0]]
                    fixes += 1

        # Fix: Unknown SNOMED codes
        if any("Unknown code" in m and "snomed" in m.lower() for m in error_messages):
            for entry in bundle.get("entry", []):
                r = entry.get("resource", {})
                if r.get("resourceType") != "InsurancePlan":
                    continue
                for coverage in r.get("coverage", []):
                    if self._fix_snomed_in_codeable_concept(coverage.get("type", {})):
                        fixes += 1
                    for benefit in coverage.get("benefit", []):
                        if self._fix_snomed_in_codeable_concept(benefit.get("type", {})):
                            fixes += 1

        # Fix: bdl-3 / bdl-4 (request/response in collection bundle)
        if any("bdl-3" in m or "bdl-4" in m for m in error_messages):
            for entry in bundle.get("entry", []):
                if "request" in entry:
                    del entry["request"]
                    fixes += 1
                if "response" in entry:
                    del entry["response"]
                    fixes += 1

        # Fix: Wrong display names and unknown codes in exclusions (causes cascade failures!)
        if any("Wrong Display Name" in m or ("Unknown code" in m and "ndhm-claim-exclusion" in m) for m in error_messages):
            for entry in bundle.get("entry", []):
                r = entry.get("resource", {})
                if r.get("resourceType") != "InsurancePlan":
                    continue
                fixed_exts = []
                for ext in r.get("extension", []):
                    if "Claim-Exclusion" in ext.get("url", ""):
                        fixed = self._fix_exclusion_extension(ext)
                        if fixed:
                            fixed_exts.append(fixed)
                            fixes += 1
                    else:
                        fixed_exts.append(ext)
                r["extension"] = fixed_exts
                # Also fix InsurancePlan type display
                for t in r.get("type", []):
                    for coding in t.get("coding", []):
                        code = coding.get("code", "")
                        if code in INSURANCEPLAN_TYPE_DISPLAY:
                            correct = INSURANCEPLAN_TYPE_DISPLAY[code]
                            if coding.get("display") != correct:
                                coding["display"] = correct
                                fixes += 1

        # Fix: extension not allowed at this point
        if any("extension" in m and "not allowed" in m for m in error_messages):
            for entry in bundle.get("entry", []):
                r = entry.get("resource", {})
                if r.get("resourceType") != "InsurancePlan":
                    continue
                for t in r.get("type", []):
                    if "extension" in t:
                        del t["extension"]
                        fixes += 1

        if fixes > 0:
            with open(self.bundle_path, "w", encoding="utf-8") as f:
                json.dump(bundle, f, indent=2, ensure_ascii=False)

        return fixes

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
            line = line.strip()
            line = re.sub(r'\033\[[0-9;]*m', '', line)
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

    def _group_errors(self, errors: list) -> dict:
        """Group errors by their normalized message (ignoring UUIDs, indices, line numbers)."""
        groups = {}
        for err in errors:
            # Normalize the message for grouping
            normalized = err["message"]
            normalized = re.sub(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', '<UUID>', normalized)
            normalized = re.sub(r'\[\d+\]', '[N]', normalized)

            if normalized not in groups:
                groups[normalized] = []
            groups[normalized].append(err)

        return groups

    def _fix_error_group(self, bundle: dict, error_group: list) -> dict:
        """Fix a group of similar errors in the bundle via LLM."""
        sample_error = error_group[0]

        section_json = self._extract_section_at_path(bundle, sample_error["path"])
        if section_json is None:
            section_json = "Could not extract section"
        else:
            section_json = json.dumps(section_json, indent=2)
            if len(section_json) > 12000:
                section_json = section_json[:12000] + "\n... [truncated]"

        error_details = f"Error: {sample_error['message']}\n"
        error_details += f"Path: {sample_error['path']}\n"
        error_details += f"Occurrences: {len(error_group)}\n"
        if len(error_group) > 1:
            error_details += "All affected paths:\n"
            for e in error_group[:15]:
                error_details += f"  - {e['path']}: {e['message'][:120]}\n"

        rulebook_hint = self._get_rulebook_hint_for_error(sample_error)

        # Get relevant example section for reference
        example_hint = ""
        if "coverage" in sample_error["path"].lower():
            example = self._read_file(
                os.path.join(self.examples_dir, "Bundle-InsurancePlanBundle-example-01.json")
            )
            try:
                ex_bundle = json.loads(example)
                for e in ex_bundle.get("entry", []):
                    if e.get("resource", {}).get("resourceType") == "InsurancePlan":
                        covs = e["resource"].get("coverage", [])
                        if covs:
                            example_hint = f"\nEXAMPLE of correct coverage structure (0 errors):\n{json.dumps(covs[0], indent=2)}"
                        break
            except Exception:
                pass

        system = (
            "You are a FHIR validation error fixer for NHCX InsurancePlan bundles. "
            "Fix the error while preserving ALL data. Never remove coverages, benefits, or exclusions. "
            "Output ONLY valid JSON — no markdown, no explanations."
        )

        user = f"""Fix this FHIR validation error:

{error_details}

CURRENT JSON at the error location:
{section_json}

{rulebook_hint}
{example_hint}

RULES:
- Do NOT remove any data — fix structure only
- SNOMED codes: only use these valid codes: 737481003, 710967003, 409972000, 49122002, 737850002, 105461009, 309904001, 87612001, 24099007, 224663004, 60689008, 373784001, 11429006
- If a SNOMED code is invalid, replace coding with text-only: {{"text": "description"}}
- InsurancePlan.type must have exactly 1 entry
- Claim-Condition extension format: {{"extension": [{{"url": "claim-condition", "valueString": "..."}}], "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Condition"}}
- text.status should be "extensions" if resource has extensions

Return a JSON object with:
{{
  "fix_type": "replace_section",
  "path": "the JSON path to fix (e.g., entry[0].resource.coverage[0])",
  "corrected_value": <the corrected JSON value>
}}"""

        response = self._llm_call(system, user, temperature=0.1)
        fix = self._extract_json_from_response(response)

        if fix and "corrected_value" in fix:
            try:
                return self._apply_fix_to_bundle(bundle, fix)
            except Exception as e:
                logger.warning(f"Failed to apply fix: {e}")
                return None

        return None

    def _comprehensive_fix(self, errors: list, raw_output: str):
        """When individual fixes stall, try a comprehensive approach using Python script."""
        with open(self.bundle_path, "r", encoding="utf-8") as f:
            bundle_content = f.read()

        if len(bundle_content) > 60000:
            bundle_excerpt = bundle_content[:30000] + "\n... [middle truncated] ...\n" + bundle_content[-30000:]
        else:
            bundle_excerpt = bundle_content

        # Read the example for reference
        example = self._read_file(
            os.path.join(self.examples_dir, "Bundle-InsurancePlanBundle-example-01.json")
        )

        error_summary = []
        seen = set()
        for err in errors:
            key = err["message"][:120]
            if key not in seen:
                seen.add(key)
                error_summary.append(f"  [{err['severity']}] {err['path']}: {err['message']}")
        error_text = "\n".join(error_summary[:40])

        system = (
            "You are a FHIR expert fixing NHCX InsurancePlanBundle validation errors. "
            "Generate a Python script that reads the bundle JSON, fixes ALL the listed errors, "
            "and writes the corrected bundle back. "
            "CRITICAL: The script must preserve ALL data — every coverage, exclusion, benefit, and plan. "
            "Only fix structural/coding issues. Output ONLY the Python code."
        )

        user = f"""Fix ALL these validation errors by generating a Python script:

CRITICAL: Use this EXACT path for reading and writing the bundle (do NOT construct your own):
BUNDLE_PATH = r"{self.bundle_path}"

ERRORS:
{error_text}

RULES:
- InsurancePlan.type array MUST have exactly 1 entry
- Bundle entries MUST NOT have "request" or "response" (it is a collection bundle)
- SNOMED codes must be valid — if not, replace with text-only CodeableConcept
- Claim-Condition extension structure: {{"extension": [{{"url": "claim-condition", "valueString": "..."}}], "url": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Claim-Condition"}}
- Claim-Condition is allowed on: InsurancePlan.coverage, InsurancePlan.coverage.benefit, InsurancePlan.plan
- Claim-Condition is NOT allowed on: InsurancePlan.type
- text.status should be "extensions" if resource has extensions, "generated" otherwise
- All resources must have meta.profile

VALID SNOMED CODES (only these): 737481003, 710967003, 409972000, 49122002, 737850002, 105461009, 309904001, 87612001, 24099007, 224663004, 60689008, 373784001, 11429006, 165340005, 118189007, 133906008

REFERENCE EXAMPLE (passes validation with 0 errors):
{example[:15000]}

CURRENT BUNDLE:
{bundle_excerpt}

Generate a Python script that:
1. Reads the bundle from BUNDLE_PATH = r"{self.bundle_path}"
2. Fixes ALL the listed errors
3. Preserves ALL existing data (coverages, benefits, exclusions, plans)
4. Writes corrected JSON back to the SAME path

Output ONLY Python code."""

        response = self._llm_call(system, user, max_tokens=16384, temperature=0.1)
        code = self._extract_code_from_response(response)

        if code:
            script_path = os.path.join(self.generated_dir, "comprehensive_fix.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)

            returncode, stdout, stderr = self._run_command(f"python3 {script_path}", timeout=120)
            if returncode == 0:
                # Verify the fix preserved data
                try:
                    with open(self.bundle_path) as f:
                        fixed_bundle = json.load(f)

                    ip_resource = None
                    for e in fixed_bundle.get("entry", []):
                        if e.get("resource", {}).get("resourceType") == "InsurancePlan":
                            ip_resource = e["resource"]
                            break

                    if ip_resource:
                        n_cov = len(ip_resource.get("coverage", []))
                        n_ext = len(ip_resource.get("extension", []))
                        print(f"  Comprehensive fix applied (coverages={n_cov}, extensions={n_ext})")
                    else:
                        print(f"  Comprehensive fix applied")
                except (json.JSONDecodeError, KeyError):
                    print(f"  Comprehensive fix produced invalid JSON — reverting")
            else:
                print(f"  Comprehensive fix script failed: {stderr[:200]}")
                # Try to fix the script error
                fixed_code = self._llm_fix_python_error(code, stderr)
                if fixed_code:
                    with open(script_path, "w", encoding="utf-8") as f:
                        f.write(fixed_code)
                    rc2, _, se2 = self._run_command(f"python3 {script_path}", timeout=120)
                    if rc2 == 0:
                        print(f"  Comprehensive fix applied (after script fix)")
                    else:
                        print(f"  Comprehensive fix script still failed: {se2[:200]}")

    def _extract_section_at_path(self, bundle: dict, fhir_path: str):
        """Navigate the bundle JSON to extract the section at a FHIR path."""
        try:
            path = fhir_path
            path = re.sub(r'\.ofType\([^)]+\)', '', path)
            # Remove /*ResourceType/UUID*/ annotations from validator paths
            path = re.sub(r'/\*[^*]+\*/', '', path)
            parts = re.split(r'\.', path)

            current = bundle
            for part in parts:
                if part in ('Bundle', '') or part.startswith('resource'):
                    if part == 'resource' and isinstance(current, dict) and 'resource' in current:
                        current = current['resource']
                    continue

                match = re.match(r'(\w+)\[(\d+)\]', part)
                if match:
                    key = match.group(1)
                    idx = int(match.group(2))
                    if isinstance(current, dict) and key in current:
                        current = current[key]
                        if isinstance(current, list) and idx < len(current):
                            current = current[idx]
                        else:
                            return current
                    else:
                        return None
                elif isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    return None

            return current
        except Exception:
            return None

    def _apply_fix_to_bundle(self, bundle: dict, fix: dict) -> dict:
        """Apply a fix to the bundle JSON."""
        fixed_bundle = copy.deepcopy(bundle)
        path_str = fix.get("path", "")
        corrected = fix["corrected_value"]

        if not path_str:
            return None

        # Navigate to parent and set the value
        parts = re.split(r'\.', path_str)
        current = fixed_bundle

        for i, part in enumerate(parts[:-1]):
            match = re.match(r'(\w+)\[(\d+)\]', part)
            if match:
                key = match.group(1)
                idx = int(match.group(2))
                current = current[key][idx]
            elif part in current:
                current = current[part]
            else:
                return None

        # Set the last part
        last_part = parts[-1]
        last_match = re.match(r'(\w+)\[(\d+)\]', last_part)
        if last_match:
            key = last_match.group(1)
            idx = int(last_match.group(2))
            if key in current and isinstance(current[key], list) and idx < len(current[key]):
                current[key][idx] = corrected
        else:
            current[last_part] = corrected

        return fixed_bundle

    def _get_rulebook_hint_for_error(self, error: dict) -> str:
        """Get relevant rulebook section for an error."""
        path = error["path"]
        message = error["message"]

        hints = []

        if "InsurancePlan" in path or "InsurancePlan" in message:
            # Read relevant InsurancePlan rulebook section
            try:
                rb = self._load_json_file(
                    os.path.join(self.rulebooks_dir, "StructureDefinition-InsurancePlan_updated.json")
                )
                if rb and "elements" in rb:
                    relevant = []
                    search_terms = []
                    if "coverage" in path.lower():
                        search_terms = ["InsurancePlan.coverage"]
                    elif "plan" in path.lower():
                        search_terms = ["InsurancePlan.plan"]
                    elif "extension" in path.lower():
                        search_terms = ["InsurancePlan.extension"]
                    else:
                        search_terms = ["InsurancePlan"]

                    for elem in rb["elements"]:
                        if any(term in elem.get("path", "") for term in search_terms):
                            relevant.append(elem)

                    if relevant:
                        hints.append(f"RELEVANT RULEBOOK CONSTRAINTS:\n{json.dumps(relevant[:5], indent=2)}")
            except Exception:
                pass

        if "Organization" in path:
            try:
                rb = self._load_json_file(
                    os.path.join(self.rulebooks_dir, "StructureDefinition-Organization_updated.json")
                )
                if rb and "elements" in rb:
                    relevant = [e for e in rb["elements"] if "Organization" in e.get("path", "")][:5]
                    if relevant:
                        hints.append(f"RELEVANT RULEBOOK CONSTRAINTS:\n{json.dumps(relevant, indent=2)}")
            except Exception:
                pass

        if "Claim-Condition" in message:
            hints.append(
                "RULE: Claim-Condition extension MUST be placed on InsurancePlan.coverage.benefit, "
                "NOT directly on InsurancePlan.coverage."
            )

        if "profile" in message.lower():
            hints.append(
                "RULE: All resources must have meta.profile. Use:\n"
                '  InsurancePlan: ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlan"]\n'
                '  Organization: ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/Organization"]\n'
                '  Bundle: ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlanBundle"]'
            )

        return "\n\n".join(hints) if hints else ""

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

        # Read example for reference
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
            else:
                print(f"  Script error, fixing: {stderr[:150]}")
                fixed_code = self._llm_fix_python_error(code, stderr)
                if fixed_code:
                    with open(script_path, "w", encoding="utf-8") as f:
                        f.write(fixed_code)
                    rc2, _, se2 = self._run_command(f"python3 {script_path}", timeout=120)
                    if rc2 == 0:
                        print(f"  Added missing items (after script fix)")
                    else:
                        print(f"  Failed to add missing items: {se2[:200]}")

    # ═══════════════════════════════════════════════════════════
    # HELPER METHODS
    # ═══════════════════════════════════════════════════════════

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
