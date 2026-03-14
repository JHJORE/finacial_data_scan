"""Validate: Random sample of classifications for manual review."""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from screener.config import CLASSIFICATIONS_DIR, RESEARCH_DIR
from screener.models import Classification, ResearchResult


def validate(n: int = 10, seed: int = 42):
    classification_files = sorted(CLASSIFICATIONS_DIR.glob("*.json"))

    if not classification_files:
        print("No classifications found. Run the pipeline first.")
        return

    random.seed(seed)
    sample = random.sample(
        classification_files, min(n, len(classification_files))
    )

    for i, path in enumerate(sample, 1):
        c = Classification.model_validate_json(path.read_text())

        # Load corresponding research
        research_path = RESEARCH_DIR / f"{c.slug}.json"
        r = None
        if research_path.exists():
            r = ResearchResult.model_validate_json(research_path.read_text())

        print(f"\n{'='*70}")
        print(f"[{i}/{len(sample)}] {c.company_name} ({c.ticker})")
        print(f"{'='*70}")
        print(f"Classification: {'PROGRAMMATIC' if c.is_programmatic else 'NOT PROGRAMMATIC'}")
        print(f"Confidence: {c.confidence}")
        print(f"Year: {c.year}")
        print(f"Reasoning: {c.reasoning}")

        if c.evidence:
            print(f"\nEvidence quotes:")
            for j, ev in enumerate(c.evidence, 1):
                print(f"  {j}. \"{ev[:150]}{'...' if len(ev) > 150 else ''}\"")

        if r:
            print(f"\nSource URLs:")
            for url in r.source_urls:
                print(f"  - {url}")
            print(f"Search queries used: {r.search_queries_used}")

        if c.error:
            print(f"\nERROR: {c.error}")

        print()


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    validate(n)
