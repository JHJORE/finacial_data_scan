"""Run Agent 1: Search for annual reports for all companies."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from screener.companies import load_companies
from screener.config import COMPANIES_FILE
from screener.search import search_companies


async def main():
    if not COMPANIES_FILE.exists():
        print(f"ERROR: Company list not found at {COMPANIES_FILE}")
        sys.exit(1)

    companies = load_companies(COMPANIES_FILE)
    print(f"Loaded {len(companies)} companies")

    results = await search_companies(companies, skip_existing=True)

    found = sum(1 for r in results if r.found)
    errors = sum(1 for r in results if r.error)
    print(f"\nDone: {found}/{len(results)} sources found, {errors} errors")


if __name__ == "__main__":
    asyncio.run(main())
