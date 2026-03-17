"""Pilot: Run a sample of companies through the full pipeline to validate quality."""

import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from screener.companies import load_companies
from screener.config import COMPANIES_FILE
from screener.research import research_companies
from screener.classify import classify_companies
from screener.assemble import assemble_matrix, print_summary, save_matrix


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50

    if not COMPANIES_FILE.exists():
        print(f"ERROR: Company list not found at {COMPANIES_FILE}")
        print(f"Please place your Excel file at: {COMPANIES_FILE}")
        sys.exit(1)

    all_companies = load_companies(COMPANIES_FILE)
    print(f"Loaded {len(all_companies)} companies from {COMPANIES_FILE}")

    random.seed(42)
    pilot = random.sample(all_companies, min(n, len(all_companies)))
    print(f"Pilot set: {len(pilot)} companies (random sample)")

    # Stage 1: Research
    print(f"\n{'='*60}")
    print("STAGE 1: RESEARCH")
    print(f"{'='*60}")
    research_results = await research_companies(pilot, skip_existing=True)

    found = sum(1 for r in research_results if r.annual_report_found)
    errors = sum(1 for r in research_results if r.error)
    print(f"\nResearch: {found}/{len(research_results)} reports found, {errors} errors")

    # Stage 2: Classify
    print(f"\n{'='*60}")
    print("STAGE 2: CLASSIFY")
    print(f"{'='*60}")
    classifications = await classify_companies(research_results, skip_existing=True)

    for c in classifications:
        tag = "PROGRAMMATIC" if c.is_programmatic else "not programmatic"
        print(f"  [{tag}] ({c.confidence}) {c.company_name}: {c.reasoning[:80]}")

    # Stage 3: Assemble
    print(f"\n{'='*60}")
    print("STAGE 3: ASSEMBLE")
    print(f"{'='*60}")
    df = assemble_matrix()
    output = save_matrix(df, "pilot_matrix.csv")
    print_summary(df)
    print(f"\nPilot matrix saved to: {output}")


if __name__ == "__main__":
    asyncio.run(main())
