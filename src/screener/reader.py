"""Agent 2: Reader — Read the annual report and classify as programmatic acquirer.

Uses url_context to read the document at the URL found by the search step.
The reader ONLY runs when search found a validated URL.
"""

import asyncio
from pathlib import Path

from google import genai
from google.genai import types

from . import config
from .config import (
    AGENTS_DIR, GEMINI_MODEL, MAX_CONCURRENT_REQUESTS,
    MIN_VIABLE_TOKENS, SEC_MAX_RETRIES, SEC_MIN_VIABLE_TOKENS,
    create_gemini_client,
)
from .models import ReaderResponse, ReaderResult, SearchResult
from .utils import backoff, extract_token_usage, is_retryable

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


def _build_reader_result(data: ReaderResponse, search: SearchResult, **extra) -> ReaderResult:
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
        **extra,
    )


def _failed_result(search: SearchResult, total_in: int, total_out: int) -> ReaderResult:
    return ReaderResult(
        company_name=search.company_name,
        ticker=search.ticker,
        slug=search.slug,
        year=search.report_year,
        source_url=search.source_url,
        source_type=search.source_type,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        error="not_found_correct_document",
    )


def _result_path(slug: str) -> Path:
    return config.RESULTS_DIR / f"{slug}.json"


def _save_result(result: ReaderResult) -> None:
    output_path = _result_path(result.slug)
    output_path.write_text(result.model_dump_json(indent=2))


def load_search_results() -> list[SearchResult]:
    results = []
    for path in sorted(config.SEARCH_DIR.glob("*.json")):
        result = SearchResult.model_validate_json(path.read_text())
        results.append(result)
    return results


async def read_company(
    search: SearchResult,
    client: genai.Client,
    semaphore: asyncio.Semaphore,
) -> ReaderResult:
    """Read and classify a single company via url_context."""
    async with semaphore:
        prompt = _build_prompt(search)
        total_in, total_out = 0, 0

        # SEC filings get more retries and a lower token threshold
        is_sec = search.source_type == "sec_edgar"
        max_retries = SEC_MAX_RETRIES if is_sec else 2
        min_tokens = SEC_MIN_VIABLE_TOKENS if is_sec else MIN_VIABLE_TOKENS

        for attempt in range(max_retries):
            try:
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=prompt,
                        config=_READER_CONFIG,
                    ),
                    timeout=120,
                )

                input_tok, output_tok = extract_token_usage(response)
                total_in += input_tok
                total_out += output_tok

                retrieval_status, tool_tokens = _extract_url_metadata(response)

                if tool_tokens < min_tokens:
                    if attempt < max_retries - 1:
                        wait = backoff(attempt)
                        print(f"  [retry] {search.company_name}: only {tool_tokens:,} tokens "
                              f"(need {min_tokens:,}), status={retrieval_status}, "
                              f"attempt {attempt + 1}/{max_retries}, waiting {wait:.0f}s...")
                        await asyncio.sleep(wait)
                        continue
                    print(f"  [fail] {search.company_name}: only {tool_tokens:,} tokens read "
                          f"after {max_retries} attempts (status={retrieval_status})")
                    break

                data = ReaderResponse.model_validate_json(response.text or "")
                result = _build_reader_result(
                    data, search,
                    url_retrieval_status=retrieval_status,
                    document_token_count=tool_tokens,
                    total_input_tokens=total_in,
                    total_output_tokens=total_out,
                )
                tag = "PROGRAMMATIC" if result.is_programmatic else "not programmatic"
                print(f"  [ok] {search.company_name}: {tag} ({result.confidence}) "
                      f"[{tool_tokens:,} tokens via url_context]")
                _save_result(result)
                return result

            except Exception as e:
                error_str = str(e)
                if is_retryable(error_str) and attempt < max_retries - 1:
                    wait = backoff(attempt)
                    await asyncio.sleep(wait)
                    continue
                print(f"  [error] {search.company_name}: {error_str[:120]}")
                break

        result = _failed_result(search, total_in, total_out)
        _save_result(result)
        return result


async def read_companies(
    search_results: list[SearchResult] | None = None,
    skip_existing: bool = True,
) -> list[ReaderResult]:
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
