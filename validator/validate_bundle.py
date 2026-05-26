import subprocess
import sys
import argparse
import os

def main():
    parser = argparse.ArgumentParser(description="Validate a FHIR bundle JSON file.")
    parser.add_argument(
        "--json",
        type=str,
        required=True,
        help="Path to the JSON file to validate"
    )
    args = parser.parse_args()

    json_path = os.path.abspath(args.json)
    if not os.path.exists(json_path):
        print(f"Error: The file '{json_path}' does not exist.")
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    validator_jar = os.path.join(script_dir, "validator_cli.jar")

    if not os.path.exists(validator_jar):
        print(f"Error: validator_cli.jar not found at {validator_jar}")
        sys.exit(1)

    cmd = [
        "java", "-jar", validator_jar,
        json_path,
        "-ig", "ndhm.in"
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        output = result.stdout + result.stderr
        for line in output.splitlines():
            cleaned = line.replace('\033[0;39m', '').replace('\033[39m', '').strip()
            if cleaned:
                print(cleaned)
        sys.exit(result.returncode)
    except subprocess.TimeoutExpired:
        print("Validator timed out after 600 seconds")
        sys.exit(1)
    except FileNotFoundError:
        print("Java or validator_cli.jar not found. Please verify your environment setup.")
        sys.exit(1)

if __name__ == "__main__":
    main()
