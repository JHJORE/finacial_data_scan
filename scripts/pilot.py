"""Pilot: Run a sample of companies through the full pipeline to validate quality."""

import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from screener.companies import load_companies
from screener.config import COMPANIES_FILE, MAX_CONCURRENT_REQUESTS, create_gemini_client, init_run
from screener.models import Company
from screener.search import search_company
from screener.reader import read_company
from screener.assemble import assemble_matrix, print_summary, save_matrix


async def process_company(
    company: Company,
    client,
    semaphore: asyncio.Semaphore,
):
    """Full pipeline for one company: search → reader, back-to-back."""
    search_result = await search_company(company, client, semaphore)

    reader_result = None
    if search_result.status == "found" and not search_result.error:
        reader_result = await read_company(search_result, client, semaphore)

    return search_result, reader_result


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    init_run(create_new=True)

    if not COMPANIES_FILE.exists():
        print(f"ERROR: Company list not found at {COMPANIES_FILE}")
        print(f"Please place your Excel file at: {COMPANIES_FILE}")
        sys.exit(1)

    all_companies = load_companies(COMPANIES_FILE)
    print(f"Loaded {len(all_companies)} companies from {COMPANIES_FILE}")

    random.seed(42)
    pilot = random.sample(all_companies, min(n, len(all_companies)))

    client = create_gemini_client()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    print(f"\nProcessing {len(pilot)} companies (search → read, parallel)...\n")

    tasks = [process_company(c, client, semaphore) for c in pilot]
    results = await asyncio.gather(*tasks)

    search_results = [r[0] for r in results]
    reader_results = [r[1] for r in results if r[1] is not None]

    # Summary
    found = sum(1 for r in search_results if r.status == "found")
    na = sum(1 for r in search_results if r.status == "not_applicable")
    search_errors = sum(1 for r in search_results if r.error)
    readers_ok = sum(1 for r in reader_results if not r.error)
    programmatic = sum(1 for r in reader_results if r.is_programmatic)

    print(f"\n{'='*60}")
    print(f"Search: {found}/{len(search_results)} found, {na} not applicable, {search_errors} errors")
    print(f"Reader: {readers_ok}/{len(reader_results)} read, {programmatic} programmatic")
    print(f"{'='*60}")

    # Assemble
    df = assemble_matrix()
    output = save_matrix(df)
    print_summary(df)
    print(f"\nPilot matrix saved to: {output}")


if __name__ == "__main__":
    asyncio.run(main())
