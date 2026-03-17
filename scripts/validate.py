"""Validate: Random sample of results for manual review."""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from screener.config import RESULTS_DIR, SEARCH_DIR
from screener.models import ReaderResult, SearchResult


def validate(n: int = 10, seed: int = 42):
    result_files = sorted(RESULTS_DIR.glob("*.json"))

    if not result_files:
        print("No results found. Run the pipeline first.")
        return

    random.seed(seed)
    sample = random.sample(result_files, min(n, len(result_files)))

    for i, path in enumerate(sample, 1):
        r = ReaderResult.model_validate_json(path.read_text())

        # Load corresponding search result
        search_path = SEARCH_DIR / f"{r.slug}.json"
        s = None
        if search_path.exists():
            s = SearchResult.model_validate_json(search_path.read_text())

        print(f"\n{'='*70}")
        print(f"[{i}/{len(sample)}] {r.company_name} ({r.ticker})")
        print(f"{'='*70}")
        print(f"Classification: {'PROGRAMMATIC' if r.is_programmatic else 'NOT PROGRAMMATIC'}")
        print(f"Confidence: {r.confidence}")
        print(f"Year: {r.year}")
        print(f"Reasoning: {r.reasoning}")
        print(f"Quantitative threshold met: {r.meets_quantitative_threshold} ({r.acquisitions_mentioned} acquisitions)")

        # Checklist
        checklist = [
            ("core_growth_driver", r.core_growth_driver),
            ("stated_programme", r.stated_programme),
            ("repeated_references", r.repeated_references),
            ("clear_processes", r.clear_processes),
            ("decentralized_model", r.decentralized_model),
            ("quantitative_goals", r.quantitative_goals),
        ]
        print(f"\nChecklist:")
        for name, val in checklist:
            print(f"  {'[x]' if val else '[ ]'} {name}")

        disqualifiers = [
            ("only_high_deal_count", r.only_high_deal_count),
            ("only_opportunistic", r.only_opportunistic),
            ("only_single_deal", r.only_single_deal),
        ]
        active_disqualifiers = [(n, v) for n, v in disqualifiers if v]
        if active_disqualifiers:
            print(f"\nDisqualifiers:")
            for name, _ in active_disqualifiers:
                print(f"  [!] {name}")

        if r.evidence:
            print(f"\nEvidence quotes:")
            for j, ev in enumerate(r.evidence, 1):
                print(f"  {j}. \"{ev[:150]}{'...' if len(ev) > 150 else ''}\"")

        if s:
            print(f"\nSource: {s.source_url}")
            print(f"Source type: {s.source_type}")
            print(f"Source rationale: {s.source_rationale}")
            print(f"Search queries: {s.search_queries_used}")

        if r.error:
            print(f"\nERROR: {r.error}")

        print()


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    validate(n)
