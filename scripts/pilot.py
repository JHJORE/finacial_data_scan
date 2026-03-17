"""Pilot: Run a sample of companies through the full pipeline to validate quality."""

import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from screener.companies import load_companies
from screener.config import COMPANIES_FILE
from screener.search import search_companies
from screener.reader import read_companies
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

    # Agent 1: Search
    print(f"\n{'='*60}")
    print("AGENT 1: SEARCH")
    print(f"{'='*60}")
    search_results = await search_companies(pilot, skip_existing=True)

    found = sum(1 for r in search_results if r.found)
    errors = sum(1 for r in search_results if r.error)
    print(f"\nSearch: {found}/{len(search_results)} sources found, {errors} errors")

    for r in search_results:
        status = "found" if r.found else "NOT FOUND"
        print(f"  [{status}] {r.company_name}: {r.source_type} — {r.source_rationale[:60]}")

    # Agent 2: Reader
    print(f"\n{'='*60}")
    print("AGENT 2: READER")
    print(f"{'='*60}")
    reader_results = await read_companies(search_results, skip_existing=True)

    for r in reader_results:
        tag = "PROGRAMMATIC" if r.is_programmatic else "not programmatic"
        print(f"  [{tag}] ({r.confidence}) {r.company_name}: {r.reasoning[:80]}")

    # Assemble
    print(f"\n{'='*60}")
    print("ASSEMBLE")
    print(f"{'='*60}")
    df = assemble_matrix()
    output = save_matrix(df, "pilot_matrix.csv")
    print_summary(df)
    print(f"\nPilot matrix saved to: {output}")


if __name__ == "__main__":
    asyncio.run(main())
