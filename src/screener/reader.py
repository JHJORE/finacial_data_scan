"""Agent 2: Reader — Read the annual report and classify as programmatic acquirer."""

import asyncio
from pathlib import Path

from google import genai
from google.genai import types

from . import config
from .config import AGENTS_DIR, GEMINI_MODEL, MAX_CONCURRENT_REQUESTS, create_gemini_client
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


def _extract_url_metadata(response) -> tuple[str, int]:
    """Extract url_retrieval_status and document token count from the response."""
    retrieval_status = ""
    if hasattr(response, "candidates") and response.candidates:
        url_meta = getattr(response.candidates[0], "url_context_metadata", None)
        if url_meta and hasattr(url_meta, "url_metadata") and url_meta.url_metadata:
            raw_status = url_meta.url_metadata[0].url_retrieval_status
            retrieval_status = str(raw_status).rsplit(".", 1)[-1] if raw_status else ""

    tool_tokens = 0
    usage = getattr(response, "usage_metadata", None)
    if usage:
        tool_tokens = getattr(usage, "tool_use_prompt_token_count", 0) or 0

    return retrieval_status, tool_tokens


def _parse_reader_response(
    response, search: SearchResult
) -> ReaderResult:
    """Parse structured Gemini response into a ReaderResult."""
    text = response.text or ""
    retrieval_status, tool_tokens = _extract_url_metadata(response)

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
            url_retrieval_status=retrieval_status,
            document_token_count=tool_tokens,
        )
    except Exception:
        return ReaderResult(
            company_name=search.company_name,
            ticker=search.ticker,
            slug=search.slug,
            year=search.report_year,
            source_url=search.source_url,
            source_type=search.source_type,
            url_retrieval_status=retrieval_status,
            document_token_count=tool_tokens,
            error=f"Failed to parse structured response: {text[:200]}",
        )


def _result_path(slug: str) -> Path:
    return config.RESULTS_DIR / f"{slug}.json"


def load_search_results() -> list[SearchResult]:
    """Load all search results from disk."""
    results = []
    for path in sorted(config.SEARCH_DIR.glob("*.json")):
        result = SearchResult.model_validate_json(path.read_text())
        results.append(result)
    return results


async def read_company(
    search: SearchResult,
    client: genai.Client,
    semaphore: asyncio.Semaphore,
    max_retries: int = 3,
) -> ReaderResult:
    """Read and classify a single company with rate limiting and retries."""
    async with semaphore:
        prompt = _build_prompt(search)

        for attempt in range(max_retries):
            try:
                response = await client.aio.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config=_READER_CONFIG,
                )
                result = _parse_reader_response(response, search)
                tag = "PROGRAMMATIC" if result.is_programmatic else "not programmatic"
                tokens = f"{result.document_token_count:,} tokens read"
                print(f"  [ok] {search.company_name}: {tag} ({result.confidence}) [{tokens}]")
                if result.url_retrieval_status and "SUCCESS" not in result.url_retrieval_status:
                    print(f"  [WARN] {search.company_name}: URL retrieval status: {result.url_retrieval_status}")
                if 0 < result.document_token_count < 5_000:
                    print(f"  [WARN] {search.company_name}: Very few tokens read ({result.document_token_count}) — may not be the full filing")
                break
            except Exception as e:
                error_str = str(e)
                is_rate_limit = "429" in error_str or "RESOURCE_EXHAUSTED" in error_str
                if is_rate_limit and attempt < max_retries - 1:
                    wait = 2 ** (attempt + 1) * 10
                    print(f"  [rate limit] {search.company_name} — retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait)
                    continue
                print(f"  [ERROR] {search.company_name}: {error_str}")
                result = ReaderResult(
                    company_name=search.company_name,
                    ticker=search.ticker,
                    slug=search.slug,
                    year=search.report_year,
                    source_url=search.source_url,
                    source_type=search.source_type,
                    error=error_str,
                )
                break

        output_path = _result_path(search.slug)
        output_path.write_text(result.model_dump_json(indent=2))
        return result


async def read_companies(
    search_results: list[SearchResult] | None = None,
    skip_existing: bool = True,
) -> list[ReaderResult]:
    """Read and classify all searched companies."""
    if search_results is None:
        search_results = load_search_results()

    valid = [r for r in search_results if r.status == "found" and not r.error]
    skipped = len(search_results) - len(valid)
    if skipped:
        print(f"  Skipping {skipped} companies (not found / not applicable / error)")

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
