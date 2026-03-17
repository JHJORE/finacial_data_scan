"""Agent 1: Search — Find the best annual report source using Google Search grounding."""

import asyncio
import json
from pathlib import Path

from google import genai
from google.genai import types

from . import config
from .config import AGENTS_DIR, GEMINI_MODEL, MAX_CONCURRENT_REQUESTS, create_gemini_client
from .models import Company, SearchResponse, SearchResult

_INSTRUCTIONS = (AGENTS_DIR / "search.md").read_text()

_GROUNDING_CONFIG = types.GenerateContentConfig(
    tools=[types.Tool(google_search=types.GoogleSearch())],
)

_STRUCTURE_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    response_json_schema=SearchResponse.model_json_schema(),
)

_STRUCTURE_PROMPT = """Based on the search results below, fill in the structured response.

IMPORTANT: Set `source_url` to the best URL found — strongly prefer sec.gov links.

Search result:
{search_text}
"""


def _build_prompt(company: Company) -> str:
    return _INSTRUCTIONS.format(
        company_name=company.name,
        ticker=company.ticker,
    )


def _extract_search_queries(response) -> list[str]:
    """Extract the search queries used from grounding metadata."""
    if not hasattr(response, "candidates") or not response.candidates:
        return []
    metadata = getattr(response.candidates[0], "grounding_metadata", None)
    if metadata and hasattr(metadata, "web_search_queries") and metadata.web_search_queries:
        return list(metadata.web_search_queries)
    return []


def _result_path(company: Company) -> Path:
    return config.SEARCH_DIR / f"{company.slug}.json"


async def search_company(
    company: Company, client: genai.Client, semaphore: asyncio.Semaphore,
    max_retries: int = 3,
) -> SearchResult:
    """Search for a single company's annual report.

    Two-step process because Gemini cannot combine grounding with structured output:
      1. Grounding call — real Google Search, returns free text with findings
      2. Structure call — formats the search findings into our JSON schema
    """
    async with semaphore:
        prompt = _build_prompt(company)

        for attempt in range(max_retries):
            try:
                # Step 1: Search with grounding (no structured output)
                grounding_resp = await client.aio.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config=_GROUNDING_CONFIG,
                )
                search_text = grounding_resp.text or ""
                search_queries = _extract_search_queries(grounding_resp)

                # Step 2: Structure the response (no grounding)
                structure_prompt = _STRUCTURE_PROMPT.format(search_text=search_text)
                structure_resp = await client.aio.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=structure_prompt,
                    config=_STRUCTURE_CONFIG,
                )

                data = SearchResponse.model_validate_json(structure_resp.text or "")
                result = SearchResult(
                    company_name=company.name,
                    ticker=company.ticker,
                    slug=company.slug,
                    status=data.status,
                    report_year=data.report_year,
                    source_url=data.source_url,
                    source_type=data.source_type,
                    source_rationale=data.source_rationale,
                    search_queries_used=search_queries,
                )
                url_preview = result.source_url[:80] if result.source_url else "(none)"
                tag = {"found": "ok", "not_applicable": "n/a", "not_found": "not found"}[result.status]
                print(f"  [{tag}] {company.name}: {result.source_type or 'no source'} → {url_preview}")
                break
            except Exception as e:
                error_str = str(e)
                is_rate_limit = "429" in error_str or "RESOURCE_EXHAUSTED" in error_str
                if is_rate_limit and attempt < max_retries - 1:
                    wait = 2 ** (attempt + 1) * 10
                    print(f"  [rate limit] {company.name} — retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait)
                    continue
                print(f"  [ERROR] {company.name}: {error_str}")
                result = SearchResult(
                    company_name=company.name,
                    ticker=company.ticker,
                    slug=company.slug,
                    error=error_str,
                )
                break

        output_path = _result_path(company)
        output_path.write_text(result.model_dump_json(indent=2))

    return result


async def search_companies(
    companies: list[Company], skip_existing: bool = True
) -> list[SearchResult]:
    """Search for annual reports for multiple companies."""
    client = create_gemini_client()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    to_process = []
    results = []

    for company in companies:
        if skip_existing and _result_path(company).exists():
            existing = SearchResult.model_validate_json(
                _result_path(company).read_text()
            )
            results.append(existing)
            print(f"  [skip] {company.name} (already searched)")
        else:
            to_process.append(company)

    if to_process:
        print(f"\nSearching {len(to_process)} companies...")
        tasks = [
            search_company(c, client, semaphore) for c in to_process
        ]
        new_results = await asyncio.gather(*tasks)
        results.extend(new_results)

    return results
