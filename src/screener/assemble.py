"""Assemble search + reader results into a company x year matrix."""

from pathlib import Path

import pandas as pd

from . import config
from .models import ReaderResult, SearchResult


def load_all_search() -> dict[str, SearchResult]:
    results = {}
    for path in sorted(config.SEARCH_DIR.glob("*.json")):
        result = SearchResult.model_validate_json(path.read_text())
        results[result.slug] = result
    return results


def load_all_results() -> list[ReaderResult]:
    results = []
    for path in sorted(config.RESULTS_DIR.glob("*.json")):
        result = ReaderResult.model_validate_json(path.read_text())
        results.append(result)
    return results


def assemble_matrix() -> pd.DataFrame:
    """Build the final company x year matrix from search + reader results."""
    search_results = load_all_search()
    reader_results = load_all_results()

    # Build rows from reader results (companies that were read)
    seen_slugs = set()
    rows = []

    for r in reader_results:
        seen_slugs.add(r.slug)
        s = search_results.get(r.slug)

        rows.append(
            {
                "is_programmatic": r.is_programmatic,
                "acquirer": r.company_name,
                "ticker": r.ticker,
                "description": r.company_description or (
                    f"Description unavailable – reader error: {r.error}" if r.error
                    else "Description unavailable"
                ),
                "year": r.year,
                "source_url": r.source_url,
                "source_type": r.source_type,
                # Quantitative
                "acquisitions_mentioned": r.acquisitions_mentioned,
                "meets_quantitative_threshold": r.meets_quantitative_threshold,
                # Checklist
                "core_growth_driver": r.core_growth_driver,
                "stated_programme": r.stated_programme,
                "repeated_references": r.repeated_references,
                "clear_processes": r.clear_processes,
                "decentralized_model": r.decentralized_model,
                "quantitative_goals": r.quantitative_goals,
                # Disqualifiers
                "only_high_deal_count": r.only_high_deal_count,
                "only_opportunistic": r.only_opportunistic,
                "only_single_deal": r.only_single_deal,
                # Verdict
                "confidence": r.confidence,
                "reasoning": r.reasoning,
                "evidence": " | ".join(r.evidence),
                "search_queries": "; ".join(s.search_queries_used) if s else "",
                "status": "found",
                "url_retrieval_status": r.url_retrieval_status,
                "document_token_count": r.document_token_count,
                "error": r.error or "",
            }
        )

    # Include companies where search found nothing (not classified)
    for slug, s in search_results.items():
        if slug not in seen_slugs:
            desc = s.source_rationale
            if s.status == "not_applicable":
                desc = f"ACQUIRED: {desc}" if desc else "ACQUIRED"

            rows.append(
                {
                    "is_programmatic": False,
                    "acquirer": s.company_name,
                    "ticker": s.ticker,
                    "description": desc,
                    "year": s.report_year,
                    "source_url": s.source_url,
                    "source_type": s.source_type,
                    "acquisitions_mentioned": 0,
                    "meets_quantitative_threshold": False,
                    "core_growth_driver": False,
                    "stated_programme": False,
                    "repeated_references": False,
                    "clear_processes": False,
                    "decentralized_model": False,
                    "quantitative_goals": False,
                    "only_high_deal_count": False,
                    "only_opportunistic": False,
                    "only_single_deal": False,
                    "confidence": "",
                    "reasoning": {
                        "found": "",
                        "not_found": "No annual report found",
                        "not_applicable": "Company no longer files independently",
                    }[s.status],
                    "evidence": "",
                    "search_queries": "; ".join(s.search_queries_used),
                    "status": s.status,
                    "url_retrieval_status": "",
                    "document_token_count": 0,
                    "error": s.error or "",
                }
            )

    df = pd.DataFrame(rows)

    if not df.empty:
        df = df.sort_values(["is_programmatic", "acquirer", "year"], ascending=[False, True, True]).reset_index(drop=True)

    return df


def save_matrix(df: pd.DataFrame, filename: str = "matrix.csv") -> Path:
    output_path = config.OUTPUT_DIR / filename
    df.to_csv(output_path, index=False)
    return output_path


def print_summary(df: pd.DataFrame) -> None:
    total = len(df)
    if total == 0:
        print("No results found.")
        return

    classified = df[df["status"] == "found"] if "status" in df.columns else df
    not_applicable = (df["status"] == "not_applicable").sum() if "status" in df.columns else 0
    not_found = (df["status"] == "not_found").sum() if "status" in df.columns else 0

    programmatic = df["is_programmatic"].sum()

    print(f"\n{'='*60}")
    print("SCREENING SUMMARY")
    print(f"{'='*60}")
    print(f"Total companies:            {total}")
    print(f"Classified:                 {len(classified)}")
    if not_applicable:
        print(f"Not applicable:             {not_applicable}")
    if not_found:
        print(f"Not found:                  {not_found}")
    print(f"Programmatic acquirers:     {programmatic}")
    print(f"Not programmatic:           {len(classified) - programmatic}")

    if "confidence" in df.columns:
        has_confidence = classified[classified["confidence"] != ""]
        if len(has_confidence) > 0:
            print(f"\nConfidence distribution (of {len(has_confidence)} classified):")
            for level in ["high", "medium", "low"]:
                count = (has_confidence["confidence"] == level).sum()
                print(f"  {level:8s}: {count} ({count/len(has_confidence)*100:.1f}%)")

    errors = df["error"].astype(bool).sum()
    if errors:
        print(f"\nErrors: {errors}")

    print(f"{'='*60}")
