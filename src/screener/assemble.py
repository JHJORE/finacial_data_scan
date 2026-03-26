"""Assemble search + reader results into the final output CSV."""

from pathlib import Path

import pandas as pd

from . import config
from .models import ReaderResult, SearchResult


def load_all_search() -> dict[str, SearchResult]:
    results = {}
    for path in sorted(config.SEARCH_DIR.glob("*.json")):
        result = SearchResult.model_validate_json(path.read_text(encoding="utf-8"))
        results[result.slug] = result
    return results


def load_all_results() -> list[ReaderResult]:
    results = []
    for path in sorted(config.RESULTS_DIR.glob("*.json")):
        result = ReaderResult.model_validate_json(path.read_text(encoding="utf-8"))
        results.append(result)
    return results


def assemble_matrix() -> pd.DataFrame:
    """Build the simplified output matrix from search + reader results."""
    search_results = load_all_search()
    reader_results = load_all_results()

    seen_slugs = set()
    rows = []

    for r in reader_results:
        seen_slugs.add(r.slug)
        s = search_results.get(r.slug)

        is_doc_missing = r.error == "not_found_correct_document"
        status = "not_found_correct_document" if is_doc_missing else "found"

        rows.append(
            {
                "acquirer": r.company_name,
                "ticker": r.ticker,
                "first_entry": r.first_entry,
                "report_year_found": r.year,
                "is_programmatic": r.is_programmatic,
                "confidence": r.confidence,
                "reasoning": r.reasoning,
                "evidence": " | ".join(r.evidence),
                "source_url": r.source_url,
                "status": status,
                "error": (r.error or "") if not is_doc_missing else "",
            }
        )

    # Include companies where search found nothing (not classified)
    for slug, s in search_results.items():
        if slug not in seen_slugs:
            rows.append(
                {
                    "acquirer": s.company_name,
                    "ticker": s.ticker,
                    "first_entry": s.first_entry,
                    "report_year_found": s.report_year,
                    "is_programmatic": False,
                    "confidence": "",
                    "reasoning": {
                        "found": "",
                        "not_found": "No annual report found",
                        "not_applicable": "Company no longer files independently",
                    }[s.status],
                    "evidence": "",
                    "source_url": s.source_url,
                    "status": s.status,
                    "error": s.error or "",
                }
            )

    df = pd.DataFrame(rows)

    if not df.empty:
        df = df.sort_values(
            ["is_programmatic", "acquirer", "first_entry"],
            ascending=[False, True, True],
        ).reset_index(drop=True)

    return df


def save_matrix(df: pd.DataFrame, filename: str = "results.csv") -> Path:
    output_path = config.OUTPUT_DIR / filename
    df.to_csv(output_path, index=False)
    return output_path


def print_summary(df: pd.DataFrame) -> None:
    total = len(df)
    if total == 0:
        print("No results found.")
        return

    classified = df[df["status"] == "found"] if "status" in df.columns else df
    not_found = (df["status"] == "not_found").sum() if "status" in df.columns else 0
    not_found_doc = (df["status"] == "not_found_correct_document").sum() if "status" in df.columns else 0

    programmatic = df["is_programmatic"].sum()

    print(f"\n{'='*60}")
    print("SCREENING SUMMARY")
    print(f"{'='*60}")
    print(f"Total rows:                 {total}")
    print(f"Classified:                 {len(classified)}")
    if not_found:
        print(f"Not found:                  {not_found}")
    if not_found_doc:
        print(f"Not found correct document: {not_found_doc}")
    print(f"Programmatic acquirers:     {programmatic}")
    print(f"Not programmatic:           {len(classified) - programmatic}")

    if "confidence" in df.columns:
        has_confidence = classified[classified["confidence"] != ""]
        if len(has_confidence) > 0:
            print(f"\nConfidence distribution (of {len(has_confidence)} classified):")
            for level in ["high", "medium", "low"]:
                count = (has_confidence["confidence"] == level).sum()
                print(f"  {level:8s}: {count} ({count/len(has_confidence)*100:.1f}%)")

    error_rows = df[df["error"].fillna("").astype(str).str.len() > 0]
    if len(error_rows) > 0:
        print(f"\nErrors: {len(error_rows)}")
        for _, row in error_rows.iterrows():
            print(f"  - {row['acquirer']}: {str(row['error'])[:100]}")

    print(f"{'='*60}")
