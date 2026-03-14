"""Stage 3: Assemble classifications into a company x year matrix."""

import json
from pathlib import Path

import pandas as pd

from .config import CLASSIFICATIONS_DIR, OUTPUT_DIR, RESEARCH_DIR
from .models import Classification, ResearchResult


def load_all_classifications() -> list[Classification]:
    results = []
    for path in sorted(CLASSIFICATIONS_DIR.glob("*.json")):
        result = Classification.model_validate_json(path.read_text())
        results.append(result)
    return results


def load_all_research() -> dict[str, ResearchResult]:
    results = {}
    for path in sorted(RESEARCH_DIR.glob("*.json")):
        result = ResearchResult.model_validate_json(path.read_text())
        results[result.slug] = result
    return results


def assemble_matrix() -> pd.DataFrame:
    """Build the final company x year matrix from classifications."""
    classifications = load_all_classifications()
    research = load_all_research()

    rows = []
    for c in classifications:
        r = research.get(c.slug)
        source_urls = "; ".join(r.source_urls) if r else ""
        search_queries = "; ".join(r.search_queries_used) if r else ""

        rows.append(
            {
                "acquirer": c.company_name,
                "ticker": c.ticker,
                "year": c.year,
                "is_programmatic": c.is_programmatic,
                "confidence": c.confidence,
                "reasoning": c.reasoning,
                "evidence": " | ".join(c.evidence),
                "source_urls": source_urls,
                "search_queries": search_queries,
                "error": c.error or "",
            }
        )

    df = pd.DataFrame(rows)

    if not df.empty:
        df = df.sort_values(["acquirer", "year"]).reset_index(drop=True)

    return df


def save_matrix(df: pd.DataFrame, filename: str = "matrix.csv") -> Path:
    output_path = OUTPUT_DIR / filename
    df.to_csv(output_path, index=False)
    return output_path


def print_summary(df: pd.DataFrame) -> None:
    total = len(df)
    if total == 0:
        print("No classifications found.")
        return

    programmatic = df["is_programmatic"].sum()
    not_programmatic = total - programmatic

    print(f"\n{'='*60}")
    print(f"CLASSIFICATION SUMMARY")
    print(f"{'='*60}")
    print(f"Total companies classified: {total}")
    print(f"Programmatic acquirers:     {programmatic} ({programmatic/total*100:.1f}%)")
    print(f"Not programmatic:           {not_programmatic} ({not_programmatic/total*100:.1f}%)")

    if "confidence" in df.columns:
        print(f"\nConfidence distribution:")
        for level in ["high", "medium", "low"]:
            count = (df["confidence"] == level).sum()
            print(f"  {level:8s}: {count} ({count/total*100:.1f}%)")

    errors = df["error"].astype(bool).sum()
    if errors:
        print(f"\nErrors: {errors}")

    print(f"{'='*60}")
