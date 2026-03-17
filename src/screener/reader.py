"""Agent 2: Reader — Read the annual report and classify as programmatic acquirer."""

import asyncio
from pathlib import Path

from google import genai
from google.genai import types

from .config import (
    AGENTS_DIR,
    GEMINI_MODEL,
    MAX_CONCURRENT_REQUESTS,
    REQUESTS_PER_MINUTE,
    RESULTS_DIR,
    SEARCH_DIR,
    create_gemini_client,
)
from .models import ReaderResponse, ReaderResult, SearchResult

_INSTRUCTIONS = (AGENTS_DIR / "reader.md").read_text()

_READER_CONFIG = types.GenerateContentConfig(
    tools=[
        types.Tool(url_context=types.UrlContext()),
    ],
    response_mime_type="application/json",
    response_json_schema=ReaderResponse.model_json_schema(),
)


def _build_prompt(search: SearchResult) -> str:
    return _INSTRUCTIONS.format(
        company_name=search.company_name,
        ticker=search.ticker,
        source_url=search.source_url,
        report_year=search.report_year or "unknown",
    )


def _parse_reader_response(
    response, search: SearchResult
) -> ReaderResult:
    """Parse structured Gemini response into a ReaderResult."""
    text = response.text or ""

    try:
        data = ReaderResponse.model_validate_json(text)

        return ReaderResult(
            company_name=search.company_name,
            ticker=search.ticker,
            slug=search.slug,
            year=search.report_year,
            source_url=search.source_url,
            source_type=search.source_type,
            acquisitions_mentioned=data.acquisitions_mentioned,
            meets_quantitative_threshold=data.meets_quantitative_threshold,
            core_growth_driver=data.core_growth_driver,
            stated_programme=data.stated_programme,
            repeated_references=data.repeated_references,
            clear_processes=data.clear_processes,
            decentralized_model=data.decentralized_model,
            quantitative_goals=data.quantitative_goals,
            only_high_deal_count=data.only_high_deal_count,
            only_opportunistic=data.only_opportunistic,
            only_single_deal=data.only_single_deal,
            extracted_text=data.extracted_text,
            evidence=data.evidence,
            is_programmatic=data.is_programmatic,
            confidence=data.confidence,
            reasoning=data.reasoning,
            company_description=data.company_description,
        )
    except Exception:
        return ReaderResult(
            company_name=search.company_name,
            ticker=search.ticker,
            slug=search.slug,
            year=search.report_year,
            source_url=search.source_url,
            source_type=search.source_type,
            error=f"Failed to parse structured response: {text[:200]}",
        )


def _result_path(slug: str) -> Path:
    return RESULTS_DIR / f"{slug}.json"


def load_search_results() -> list[SearchResult]:
    """Load all search results from disk."""
    results = []
    for path in sorted(SEARCH_DIR.glob("*.json")):
        result = SearchResult.model_validate_json(path.read_text())
        results.append(result)
    return results


async def read_company(
    search: SearchResult,
    client: genai.Client,
    semaphore: asyncio.Semaphore,
) -> ReaderResult:
    """Read and classify a single company with rate limiting."""
    async with semaphore:
        prompt = _build_prompt(search)

        try:
            response = await client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=_READER_CONFIG,
            )
            result = _parse_reader_response(response, search)
        except Exception as e:
            result = ReaderResult(
                company_name=search.company_name,
                ticker=search.ticker,
                slug=search.slug,
                year=search.report_year,
                source_url=search.source_url,
                source_type=search.source_type,
                error=str(e),
            )

        output_path = _result_path(search.slug)
        output_path.write_text(result.model_dump_json(indent=2))

    await asyncio.sleep(60 / REQUESTS_PER_MINUTE)
    return result


async def read_companies(
    search_results: list[SearchResult] | None = None,
    skip_existing: bool = True,
) -> list[ReaderResult]:
    """Read and classify all searched companies."""
    if search_results is None:
        search_results = load_search_results()

    valid = [r for r in search_results if r.found and not r.error]
    skipped_no_source = len(search_results) - len(valid)
    if skipped_no_source:
        print(f"  Skipping {skipped_no_source} companies (no source found)")

    client = create_gemini_client()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    to_process = []
    results = []

    for search in valid:
        if skip_existing and _result_path(search.slug).exists():
            existing = ReaderResult.model_validate_json(
                _result_path(search.slug).read_text()
            )
            results.append(existing)
            print(f"  [skip] {search.company_name} (already read)")
        else:
            to_process.append(search)

    if to_process:
        print(f"\nReading {len(to_process)} companies...")
        tasks = [
            read_company(r, client, semaphore) for r in to_process
        ]
        new_results = await asyncio.gather(*tasks)
        results.extend(new_results)

    return results
