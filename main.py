"""Single entry point for the screening pipeline.

Usage:
    uv run python main.py                     # full pipeline, all companies
    uv run python main.py --sample 50         # full pipeline, random sample of 50
    uv run python main.py --year 2014         # search for 2014 annual reports
    uv run python main.py search              # search only (creates new run)
    uv run python main.py search --year 2014  # search for 2014 reports
    uv run python main.py read                # read + assemble (reuses latest run)
    uv run python main.py assemble            # assemble matrix from existing results
    uv run python main.py validate            # print random sample for review
    uv run python main.py validate --n 20     # validate 20 random results
"""

import argparse
import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from screener import config
from screener.config import COMPANIES_FILE, MAX_CONCURRENT_REQUESTS, create_gemini_client, init_run
from screener.companies import load_companies
from screener.models import Company
from screener.search import search_company
from screener.reader import read_company
from screener.assemble import assemble_matrix, print_summary, save_matrix


async def process_company(company: Company, client, semaphore: asyncio.Semaphore):
    """Full pipeline for one company: search then reader."""
    search_result = await search_company(company, client, semaphore)

    reader_result = None
    if search_result.status == "found" and not search_result.error:
        reader_result = await read_company(search_result, client, semaphore)

    return search_result, reader_result


async def cmd_run(args):
    """Full pipeline: search, read, assemble for all companies."""
    init_run(create_new=True)

    all_companies = _load_companies()

    if args.sample:
        random.seed(42)
        companies = random.sample(all_companies, min(args.sample, len(all_companies)))
        print(f"Sampled {len(companies)} of {len(all_companies)} companies")
    else:
        companies = all_companies

    client = create_gemini_client()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    print(f"\nProcessing {len(companies)} companies (search -> read)...\n")
    tasks = [process_company(c, client, semaphore) for c in companies]
    results = await asyncio.gather(*tasks)

    search_results = [r[0] for r in results]
    reader_results = [r[1] for r in results if r[1] is not None]

    found = sum(1 for r in search_results if r.status == "found")
    na = sum(1 for r in search_results if r.status == "not_applicable")
    search_errors = sum(1 for r in search_results if r.error)
    readers_ok = sum(1 for r in reader_results if not r.error)
    programmatic = sum(1 for r in reader_results if r.is_programmatic)

    print(f"\n{'='*60}")
    print(f"Search: {found}/{len(search_results)} found, {na} not applicable, {search_errors} errors")
    print(f"Reader: {readers_ok}/{len(reader_results)} read, {programmatic} programmatic")
    print(f"{'='*60}")

    df = assemble_matrix()
    output = save_matrix(df)
    print_summary(df)
    print(f"\nMatrix saved to: {output}")


async def cmd_search(args):
    """Search for annual reports only."""
    init_run(create_new=True)

    from screener.search import search_companies

    companies = _load_companies()
    results = await search_companies(companies, skip_existing=True)

    found = sum(1 for r in results if r.status == "found")
    na = sum(1 for r in results if r.status == "not_applicable")
    errors = sum(1 for r in results if r.error)
    print(f"\nDone: {found}/{len(results)} found, {na} not applicable, {errors} errors")


async def cmd_read(args):
    """Read and classify searched companies, then assemble."""
    init_run(create_new=False)

    from screener.reader import read_companies

    results = await read_companies(skip_existing=True)
    print(f"\nRead {len(results)} companies")

    df = assemble_matrix()
    output = save_matrix(df)
    print_summary(df)
    print(f"\nMatrix saved to: {output}")


def cmd_assemble(args):
    """Assemble matrix from existing search + reader results."""
    init_run(create_new=False)

    df = assemble_matrix()
    output = save_matrix(df)
    print_summary(df)
    print(f"\nMatrix saved to: {output}")


def cmd_validate(args):
    """Print random sample of results for manual review."""
    init_run(create_new=False)

    from screener.models import ReaderResult, SearchResult

    result_files = sorted(config.RESULTS_DIR.glob("*.json"))
    if not result_files:
        print("No results found. Run the pipeline first.")
        return

    random.seed(42)
    sample = random.sample(result_files, min(args.n, len(result_files)))

    for i, path in enumerate(sample, 1):
        r = ReaderResult.model_validate_json(path.read_text())
        search_path = config.SEARCH_DIR / f"{r.slug}.json"
        s = SearchResult.model_validate_json(search_path.read_text()) if search_path.exists() else None

        print(f"\n{'='*70}")
        print(f"[{i}/{len(sample)}] {r.company_name} ({r.ticker})")
        print(f"{'='*70}")
        print(f"Classification: {'PROGRAMMATIC' if r.is_programmatic else 'NOT PROGRAMMATIC'}")
        print(f"Confidence: {r.confidence}")
        print(f"Year: {r.year}")
        print(f"Reasoning: {r.reasoning}")
        print(f"Acquisitions: {r.acquisitions_mentioned} (threshold met: {r.meets_quantitative_threshold})")

        checklist = [
            ("core_growth_driver", r.core_growth_driver),
            ("stated_programme", r.stated_programme),
            ("repeated_references", r.repeated_references),
            ("clear_processes", r.clear_processes),
            ("decentralized_model", r.decentralized_model),
            ("quantitative_goals", r.quantitative_goals),
        ]
        print("\nChecklist:")
        for name, val in checklist:
            print(f"  {'[x]' if val else '[ ]'} {name}")

        disqualifiers = [(n, v) for n, v in [
            ("only_high_deal_count", r.only_high_deal_count),
            ("only_opportunistic", r.only_opportunistic),
            ("only_single_deal", r.only_single_deal),
        ] if v]
        if disqualifiers:
            print("\nDisqualifiers:")
            for name, _ in disqualifiers:
                print(f"  [!] {name}")

        if r.evidence:
            print("\nEvidence quotes:")
            for j, ev in enumerate(r.evidence, 1):
                print(f"  {j}. \"{ev[:150]}{'...' if len(ev) > 150 else ''}\"")

        if s:
            print(f"\nSource: {s.source_url}")
            print(f"Source type: {s.source_type}")

        if r.error:
            print(f"\nERROR: {r.error}")
        print()


def _load_companies() -> list[Company]:
    if not COMPANIES_FILE.exists():
        print(f"ERROR: Company list not found at {COMPANIES_FILE}")
        print(f"Place your Excel file (with 'acquirer' and 'ticker' columns) at:")
        print(f"  {COMPANIES_FILE}")
        sys.exit(1)
    companies = load_companies(COMPANIES_FILE)
    print(f"Loaded {len(companies)} companies from {COMPANIES_FILE}")
    return companies


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Screening pipeline for identifying programmatic acquirers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="Full pipeline: search -> read -> assemble (default)")

    sub.add_parser("search", help="Search for annual reports only (creates new run)")
    sub.add_parser("read", help="Read + classify + assemble (continues latest run)")
    sub.add_parser("assemble", help="Re-assemble matrix from existing results")

    val_p = sub.add_parser("validate", help="Print random sample for manual review")
    val_p.add_argument("--n", type=int, default=10, help="Number of results to review")

    # --sample is on the global parser (not just run subparser) so it works
    # when invoked without a subcommand: `python main.py --sample 5`
    parser.add_argument("--sample", type=int, default=None, help="Process a random sample of N companies instead of all")
    parser.add_argument("--year", type=int, default=None, help="Target fiscal year to search for (e.g. 2014). Defaults to current year.")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.year:
        config.TARGET_YEAR = args.year
        print(f"Target year: {args.year}")

    command = args.command or "run"

    if command == "run":
        asyncio.run(cmd_run(args))
    elif command == "search":
        asyncio.run(cmd_search(args))
    elif command == "read":
        asyncio.run(cmd_read(args))
    elif command == "assemble":
        cmd_assemble(args)
    elif command == "validate":
        cmd_validate(args)


if __name__ == "__main__":
    main()
