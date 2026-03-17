"""Agent 1: Search — Find the best annual report source using Google Search grounding."""

import asyncio
from datetime import datetime
from pathlib import Path

import aiohttp
from google import genai
from google.genai import types

from .config import (
    AGENTS_DIR,
    GEMINI_MODEL,
    MAX_CONCURRENT_REQUESTS,
    REQUESTS_PER_MINUTE,
    SEARCH_DIR,
    create_gemini_client,
)
from .models import Company, GroundingSource, SearchResponse, SearchResult

_INSTRUCTIONS = (AGENTS_DIR / "search.md").read_text()

_SEARCH_CONFIG = types.GenerateContentConfig(
    tools=[
        types.Tool(
            google_search=types.GoogleSearch(
                time_range_filter=types.Interval(
                    start_time=datetime(2024, 1, 1),
                    end_time=datetime(2025, 12, 31),
                ),
            ),
        ),
    ],
    response_mime_type="application/json",
    response_json_schema=SearchResponse.model_json_schema(),
)


def _build_prompt(company: Company) -> str:
    return _INSTRUCTIONS.format(
        company_name=company.name,
        ticker=company.ticker,
    )


def _extract_grounding_metadata(
    response,
) -> tuple[list[str], list[GroundingSource]]:
    """Extract search queries and sources from Gemini grounding metadata."""
    search_queries: list[str] = []
    sources: list[GroundingSource] = []

    if not hasattr(response, "candidates") or not response.candidates:
        return search_queries, sources

    candidate = response.candidates[0]
    metadata = getattr(candidate, "grounding_metadata", None)
    if not metadata:
        return search_queries, sources

    if hasattr(metadata, "web_search_queries") and metadata.web_search_queries:
        search_queries = list(metadata.web_search_queries)

    if hasattr(metadata, "grounding_chunks") and metadata.grounding_chunks:
        for chunk in metadata.grounding_chunks:
            web = getattr(chunk, "web", None)
            if web:
                uri = getattr(web, "uri", "")
                title = getattr(web, "title", "")
                if uri or title:
                    sources.append(GroundingSource(title=title, url=uri))

    return search_queries, sources


def _pick_primary_source(response, sources: list[GroundingSource]) -> str:
    """Pick the primary source URL from grounding metadata.

    Uses grounding_supports to find the chunk that actually backed the
    response, falling back to the first chunk.
    """
    if not sources:
        return ""

    candidate = response.candidates[0] if response.candidates else None
    metadata = getattr(candidate, "grounding_metadata", None) if candidate else None

    if metadata and hasattr(metadata, "grounding_supports") and metadata.grounding_supports:
        for support in metadata.grounding_supports:
            indices = getattr(support, "grounding_chunk_indices", None)
            if indices:
                idx = indices[0]
                if idx < len(sources) and sources[idx].url:
                    return sources[idx].url

    # Fallback: first source with a URL
    for s in sources:
        if s.url:
            return s.url
    return ""


async def _resolve_redirect(
    session: aiohttp.ClientSession, url: str
) -> str:
    """Follow a grounding redirect to get the actual URL."""
    if "grounding-api-redirect" not in url:
        return url
    try:
        async with session.head(
            url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=5)
        ) as resp:
            return str(resp.headers.get("Location", url))
    except Exception:
        return url


async def _resolve_sources(sources: list[GroundingSource]) -> list[GroundingSource]:
    """Resolve any redirect URLs to actual source URLs."""
    if not sources:
        return sources
    async with aiohttp.ClientSession() as session:
        tasks = [_resolve_redirect(session, s.url) for s in sources]
        resolved = await asyncio.gather(*tasks)
        return [
            GroundingSource(title=s.title, url=url)
            for s, url in zip(sources, resolved)
        ]


async def _parse_search_response(
    response, company: Company
) -> SearchResult:
    """Parse structured Gemini response into a SearchResult."""
    search_queries, sources = _extract_grounding_metadata(response)
    sources = await _resolve_sources(sources)
    primary_url = _pick_primary_source(response, sources)

    # Also resolve the primary URL if it's a redirect
    if primary_url and "grounding-api-redirect" in primary_url:
        async with aiohttp.ClientSession() as session:
            primary_url = await _resolve_redirect(session, primary_url)

    try:
        data = SearchResponse.model_validate_json(response.text or "")

        return SearchResult(
            company_name=company.name,
            ticker=company.ticker,
            slug=company.slug,
            found=data.found,
            report_year=data.report_year,
            source_url=primary_url,
            source_type=data.source_type,
            source_rationale=data.source_rationale,
            search_queries_used=search_queries,
            grounding_sources=sources,
        )
    except Exception:
        return SearchResult(
            company_name=company.name,
            ticker=company.ticker,
            slug=company.slug,
            source_url=primary_url,
            search_queries_used=search_queries,
            grounding_sources=sources,
            error="Failed to parse structured response",
        )


def _result_path(company: Company) -> Path:
    return SEARCH_DIR / f"{company.slug}.json"


async def search_company(
    company: Company, client: genai.Client, semaphore: asyncio.Semaphore
) -> SearchResult:
    """Search for a single company's annual report with rate limiting."""
    async with semaphore:
        prompt = _build_prompt(company)

        try:
            response = await client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=_SEARCH_CONFIG,
            )
            result = await _parse_search_response(response, company)
        except Exception as e:
            result = SearchResult(
                company_name=company.name,
                ticker=company.ticker,
                slug=company.slug,
                error=str(e),
            )

        output_path = _result_path(company)
        output_path.write_text(result.model_dump_json(indent=2))

    await asyncio.sleep(60 / REQUESTS_PER_MINUTE)
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
