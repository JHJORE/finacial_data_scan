"""Pilot: Run 50 companies through the full pipeline to validate quality."""

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

# Known programmatic acquirers for validation
KNOWN_POSITIVES = [
    "constellation software",
    "lifco",
    "danaher",
    "roper technologies",
    "transdigm",
    "addtech",
    "halma",
    "judges scientific",
    "sdiptech",
    "vitec software",
]


def select_pilot_companies(all_companies, n=50):
    """Select pilot companies: known positives + random sample."""
    positives = []
    rest = []

    for c in all_companies:
        name_lower = c.name.lower()
        if any(pos in name_lower for pos in KNOWN_POSITIVES):
            positives.append(c)
        else:
            rest.append(c)

    print(f"Found {len(positives)} known programmatic acquirers in company list:")
    for p in positives:
        print(f"  + {p.name} ({p.ticker})")

    # Fill remaining slots with random companies
    remaining = max(0, n - len(positives))
    if remaining > 0 and rest:
        random.seed(42)  # Reproducible sampling
        sample = random.sample(rest, min(remaining, len(rest)))
        pilot = positives + sample
    else:
        pilot = positives[:n]

    print(f"\nPilot set: {len(pilot)} companies ({len(positives)} known positives)")
    return pilot


async def main():
    # Load companies
    if not COMPANIES_FILE.exists():
        print(f"ERROR: Company list not found at {COMPANIES_FILE}")
        print(f"Please place your Excel file at: {COMPANIES_FILE}")
        sys.exit(1)

    all_companies = load_companies(COMPANIES_FILE)
    print(f"Loaded {len(all_companies)} companies from {COMPANIES_FILE}")

    # Select pilot
    pilot = select_pilot_companies(all_companies, n=50)

    # Stage 1: Research
    print(f"\n{'='*60}")
    print("STAGE 1: RESEARCH (Gemini + Google Search)")
    print(f"{'='*60}")
    research_results = await research_companies(pilot, skip_existing=True)

    found = sum(1 for r in research_results if r.annual_report_found)
    errors = sum(1 for r in research_results if r.error)
    print(f"\nResearch complete: {found}/{len(research_results)} reports found, {errors} errors")

    # Show what was found
    for r in research_results:
        status = "OK" if r.annual_report_found else "NOT FOUND"
        if r.error:
            status = f"ERROR: {r.error[:50]}"
        print(f"  [{status}] {r.company_name} - year={r.report_year}, urls={len(r.source_urls)}")

    # Stage 2: Classify
    print(f"\n{'='*60}")
    print("STAGE 2: CLASSIFY")
    print(f"{'='*60}")
    classifications = await classify_companies(research_results, skip_existing=True)

    # Show classifications
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
