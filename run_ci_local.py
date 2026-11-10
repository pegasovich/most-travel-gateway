"""MOST CI/CD Local Pipeline Runner.

Simulates the exact stages executed by AWS CodePipeline, Jenkins, and Bitrise:
1. Static Code Analysis (flake8)
2. SAST Security Scan under PCI-DSS compliance (bandit)
3. Automated QA Suite (pytest async)
4. Quality Gate Assertion (Coverage >= 85%)
5. Test Artifact Generation (JUnit XML & Cobertura XML)
"""

import os
import subprocess
import sys
import xml.etree.ElementTree as ET

# Enable UTF-8 for Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

MIN_COVERAGE = 85.0


def print_stage(name: str):
    print("\n" + "=" * 65)
    print(f"  >> [CI/CD STAGE]: {name}")
    print("=" * 65)


def run_command(cmd: str, stage_name: str):
    print_stage(stage_name)
    print(f"Executing: {cmd}\n")
    ret = subprocess.run(cmd, shell=True)
    if ret.returncode != 0:
        print(f"\n[FAILED] Stage '{stage_name}' failed with exit code {ret.returncode}!")
        sys.exit(ret.returncode)
    print(f"\n[PASSED] Stage '{stage_name}' completed successfully.")


def check_quality_gate():
    print_stage("Quality Gate: Line Coverage Assertion")
    coverage_file = "reports/coverage.xml"
    if not os.path.exists(coverage_file):
        print(f"[FAILED] Coverage report '{coverage_file}' not found!")
        sys.exit(1)

    tree = ET.parse(coverage_file)
    root = tree.getroot()
    line_rate = float(root.attrib.get("line-rate", 0)) * 100
    print(f"Measured Total Line Coverage: {line_rate:.2f}% (Threshold: {MIN_COVERAGE}%)")

    if line_rate < MIN_COVERAGE:
        print(f"[FAILED] Quality Gate: {line_rate:.2f}% is below required threshold ({MIN_COVERAGE}%)!")
        sys.exit(1)

    print(f"[PASSED] Quality Gate: Coverage {line_rate:.2f}% meets required threshold ({MIN_COVERAGE}%).")


def main():
    print("\n" + "#" * 65)
    print("  MOST TRAVEL PAYMENT GATEWAY - LOCAL CI/CD PIPELINE RUNNER")
    print("#" * 65)

    os.makedirs("reports", exist_ok=True)

    # Stage 1: Flake8 Linting
    run_command("flake8 app tests --max-line-length=135 --ignore=E203,W503", "1. Flake8 Lint & Style Check")

    # Stage 2: Bandit SAST Security (PCI-DSS)
    run_command("bandit -r app -ll -ii", "2. Bandit Security Audit (PCI-DSS Compliance)")

    # Stage 3: Pytest Suite with JUnit and Cobertura reporting
    run_command(
        "pytest -v --cov=app --cov-report=term-missing --cov-report=xml:reports/coverage.xml --junitxml=reports/junit.xml",
        "3. Automated Pytest Suite & Report Generation",
    )

    # Stage 4: Quality Gate
    check_quality_gate()

    print("\n" + "=" * 65)
    print("  ALL CI/CD GATES PASSED! Build is verified and ready for release.")
    print("  Artifacts created:")
    print("    - reports/junit.xml (Test results)")
    print("    - reports/coverage.xml (Cobertura coverage)")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
