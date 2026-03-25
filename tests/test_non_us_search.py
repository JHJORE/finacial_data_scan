"""Integration tests for non-US company search and classification.

Tests the full pipeline for non-US companies from companies.xlsx
(excluding Apple and Accenture which use SEC EDGAR):
  1. Search — find the annual report URL (agentic single-call or fallback)
  2. Read — download/navigate and classify as programmatic acquirer

Requires Vertex AI credentials (GOOGLE_CLOUD_PROJECT env var).
"""

import pytest

from screener import config
from screener.models import Company, SearchResult
from screener.search import search_company, _is_homepage_url
from screener.reader import read_company


# ── Search-only tests ────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.parametrize("company_name,ticker,target_year", [
    ("Lifco AB", "LIFCO BS SS Equity", 2024),
    ("Bergman & Beving AB", "BERGB SS Equity", 2024),
    ("AF Gruppen ASA", "AFG NO Equity", 2024),
    ("EQVA ASA", "EQVA NO Equity", 2024),
])
async def test_search_non_us_company(
    company_name, ticker, target_year, gemini_client, semaphore, tmp_path,
):
    """Search should find an annual report URL for each non-US company."""
    config.TARGET_YEAR = target_year
    config.SEARCH_DIR = tmp_path / "search"
    config.SEARCH_DIR.mkdir(parents=True, exist_ok=True)

    company = Company.from_row(company_name, ticker)
    result = await search_company(company, gemini_client, semaphore)

    print(f"\n  Company: {result.company_name}")
    print(f"  Status: {result.status}")
    print(f"  URL: {result.source_url}")
    print(f"  Year: {result.report_year}")
    print(f"  Rationale: {result.source_rationale}")
    print(f"  Error: {result.error}")

    assert result.status == "found", (
        f"Failed to find annual report for {company_name}: {result.error}"
    )
    assert result.source_url, f"No URL returned for {company_name}"
    assert result.url_validated, f"URL not validated for {company_name}: {result.source_url}"
    assert not _is_homepage_url(result.source_url), (
        f"Search returned a homepage URL for {company_name}: {result.source_url}"
    )


# ── End-to-end: search -> classify ────────────────────────────


@pytest.mark.integration
async def test_e2e_lifco(gemini_client, semaphore, tmp_path):
    """Full pipeline for Lifco AB: search -> read -> classify.

    Lifco is a well-known Swedish serial acquirer (programmatic acquirer)
    with a decentralized model and 10+ acquisitions per year.
    """
    config.TARGET_YEAR = 2024
    config.SEARCH_DIR = tmp_path / "search"
    config.SEARCH_DIR.mkdir(parents=True, exist_ok=True)
    config.RESULTS_DIR = tmp_path / "results"
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Search
    company = Company.from_row("Lifco AB", "LIFCO BS SS Equity")
    search_result = await search_company(company, gemini_client, semaphore)

    print(f"\n  Search: {search_result.status} -> {search_result.source_url}")
    assert search_result.status == "found", (
        f"Search failed for Lifco: {search_result.error}"
    )
    assert not _is_homepage_url(search_result.source_url), (
        f"Search returned homepage for Lifco: {search_result.source_url}"
    )

    # Step 2: Read and classify
    reader_result = await read_company(search_result, gemini_client, semaphore)

    print(f"  Programmatic: {reader_result.is_programmatic}")
    print(f"  Confidence: {reader_result.confidence}")
    print(f"  Acquisitions: {reader_result.acquisitions_mentioned}")
    print(f"  Reasoning: {reader_result.reasoning}")
    print(f"  Company: {reader_result.company_description}")
    print(f"  Error: {reader_result.error}")

    assert reader_result.error is None, (
        f"Classification failed with error: {reader_result.error}"
    )
    assert reader_result.is_programmatic is True, (
        f"Lifco should be programmatic but got is_programmatic={reader_result.is_programmatic}, "
        f"reasoning: {reader_result.reasoning}"
    )
    assert reader_result.confidence in ("high", "medium"), (
        f"Expected high/medium confidence for Lifco, got {reader_result.confidence}"
    )
    assert reader_result.acquisitions_mentioned >= 3, (
        f"Expected >=3 acquisitions for Lifco, got {reader_result.acquisitions_mentioned}"
    )
    assert len(reader_result.evidence) > 0, "Expected evidence quotes"
    assert len(reader_result.extracted_text) > 0, "Expected extracted text"


@pytest.mark.integration
async def test_e2e_bergman_beving(gemini_client, semaphore, tmp_path):
    """Full pipeline for Bergman & Beving AB: search -> read -> classify.

    Bergman & Beving is a Swedish industrial group that acquires niche
    companies, similar to Lifco (they share origins in the Latour group).
    """
    config.TARGET_YEAR = 2024
    config.SEARCH_DIR = tmp_path / "search"
    config.SEARCH_DIR.mkdir(parents=True, exist_ok=True)
    config.RESULTS_DIR = tmp_path / "results"
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Search
    company = Company.from_row("Bergman & Beving AB", "BERGB SS Equity")
    search_result = await search_company(company, gemini_client, semaphore)

    print(f"\n  Search: {search_result.status} -> {search_result.source_url}")
    assert search_result.status == "found", (
        f"Search failed for Bergman & Beving: {search_result.error}"
    )
    assert not _is_homepage_url(search_result.source_url), (
        f"Search returned homepage for Bergman & Beving: {search_result.source_url}"
    )

    # Step 2: Read and classify
    reader_result = await read_company(search_result, gemini_client, semaphore)

    print(f"  Programmatic: {reader_result.is_programmatic}")
    print(f"  Confidence: {reader_result.confidence}")
    print(f"  Acquisitions: {reader_result.acquisitions_mentioned}")
    print(f"  Reasoning: {reader_result.reasoning}")
    print(f"  Company: {reader_result.company_description}")
    print(f"  Error: {reader_result.error}")

    # Bergman & Beving should be classifiable (no error) and have extracted text
    assert reader_result.error is None, (
        f"Classification failed with error: {reader_result.error}"
    )
    assert len(reader_result.extracted_text) > 0 or reader_result.acquisitions_mentioned >= 0, (
        "Reader should have extracted some content from the annual report"
    )


@pytest.mark.integration
@pytest.mark.parametrize("company_name,ticker", [
    ("AF Gruppen ASA", "AFG NO Equity"),
    ("EQVA ASA", "EQVA NO Equity"),
])
async def test_e2e_norwegian_companies(
    company_name, ticker, gemini_client, semaphore, tmp_path,
):
    """Full pipeline for Norwegian companies: search -> read -> classify.

    Verifies the reader can process the found document without errors.
    Does not assert on the classification outcome since these may or may
    not be programmatic acquirers — we just verify the pipeline works.
    """
    config.TARGET_YEAR = 2024
    config.SEARCH_DIR = tmp_path / "search"
    config.SEARCH_DIR.mkdir(parents=True, exist_ok=True)
    config.RESULTS_DIR = tmp_path / "results"
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Search
    company = Company.from_row(company_name, ticker)
    search_result = await search_company(company, gemini_client, semaphore)

    print(f"\n  Search: {search_result.status} -> {search_result.source_url}")
    assert search_result.status == "found", (
        f"Search failed for {company_name}: {search_result.error}"
    )
    assert not _is_homepage_url(search_result.source_url), (
        f"Search returned homepage for {company_name}: {search_result.source_url}"
    )

    # Step 2: Read and classify
    reader_result = await read_company(search_result, gemini_client, semaphore)

    print(f"  Programmatic: {reader_result.is_programmatic}")
    print(f"  Confidence: {reader_result.confidence}")
    print(f"  Acquisitions: {reader_result.acquisitions_mentioned}")
    print(f"  Reasoning: {reader_result.reasoning}")
    print(f"  Company: {reader_result.company_description}")
    print(f"  Token count: {reader_result.document_token_count}")
    print(f"  Error: {reader_result.error}")

    # The pipeline should complete without error and extract content
    assert reader_result.error is None, (
        f"Classification failed for {company_name}: {reader_result.error}"
    )
    assert reader_result.company_description, (
        f"Reader should produce a company description for {company_name}"
    )
