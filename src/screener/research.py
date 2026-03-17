"""Stage 1: Research - Find annual reports using Gemini + Google Search grounding."""

import asyncio
from pathlib import Path

import aiohttp
from google import genai

from .config import (
    GEMINI_MODEL,
    GOOGLE_API_KEY,
    MAX_CONCURRENT_REQUESTS,
    REQUESTS_PER_MINUTE,
    RESEARCH_DIR,
)
from .models import Company, GroundingSource, ResearchResponse, ResearchResult
from .prompts import RESEARCH_PROMPT

_RESEARCH_CONFIG = {
    "tools": [{"google_search": {}}],
    "response_mime_type": "application/json",
    "response_json_schema": ResearchResponse.model_json_schema(),
}


def _build_prompt(company: Company) -> str:
    return RESEARCH_PROMPT.format(
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


async def _resolve_redirect(
    session: aiohttp.ClientSession, url: str
) -> str:
    """Follow a Vertex AI grounding redirect to get the actual URL."""
    if "grounding-api-redirect" not in url:
        return url
    try:
        async with session.head(url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            return str(resp.headers.get("Location", url))
    except Exception:
        return url


async def _resolve_sources(sources: list[GroundingSource]) -> list[GroundingSource]:
    """Resolve all Vertex AI redirect URLs to actual source URLs."""
    if not sources:
        return sources
    async with aiohttp.ClientSession() as session:
        tasks = [_resolve_redirect(session, s.url) for s in sources]
        resolved = await asyncio.gather(*tasks)
        return [
            GroundingSource(title=s.title, url=url)
            for s, url in zip(sources, resolved)
        ]


async def _parse_research_response(
    response, company: Company
) -> ResearchResult:
    """Parse structured Gemini response into a ResearchResult.

    Source URLs come from grounding metadata (verified Google Search results),
    not from the model's structured output. Vertex AI returns redirect proxy
    URLs, so we resolve them to actual source URLs.
    """
    search_queries, sources = _extract_grounding_metadata(response)
    sources = await _resolve_sources(sources)
    source_urls = [s.url for s in sources if s.url]

    try:
        data = ResearchResponse.model_validate_json(response.text or "")

        return ResearchResult(
            company_name=company.name,
            ticker=company.ticker,
            slug=company.slug,
            annual_report_found=data.annual_report_found,
            report_year=data.report_year,
            source_urls=source_urls,
            sources=sources,
            extracted_text=data.extracted_text,
            company_description=data.company_description,
            search_queries_used=search_queries,
        )
    except Exception:
        return ResearchResult(
            company_name=company.name,
            ticker=company.ticker,
            slug=company.slug,
            source_urls=source_urls,
            sources=sources,
            search_queries_used=search_queries,
            error="Failed to parse structured response",
        )


def _result_path(company: Company) -> Path:
    return RESEARCH_DIR / f"{company.slug}.json"


async def research_company(
    company: Company, client: genai.Client, semaphore: asyncio.Semaphore
) -> ResearchResult:
    """Research a single company with rate limiting."""
    async with semaphore:
        prompt = _build_prompt(company)

        try:
            response = await client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=_RESEARCH_CONFIG,
            )
            result = await _parse_research_response(response, company)
        except Exception as e:
            result = ResearchResult(
                company_name=company.name,
                ticker=company.ticker,
                slug=company.slug,
                error=str(e),
            )

        output_path = _result_path(company)
        output_path.write_text(result.model_dump_json(indent=2))

    await asyncio.sleep(60 / REQUESTS_PER_MINUTE)
    return result


async def research_companies(
    companies: list[Company], skip_existing: bool = True
) -> list[ResearchResult]:
    """Research multiple companies concurrently with rate limiting."""
    client = genai.Client(vertexai=True, api_key=GOOGLE_API_KEY)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    to_process = []
    results = []

    for company in companies:
        if skip_existing and _result_path(company).exists():
            existing = ResearchResult.model_validate_json(
                _result_path(company).read_text()
            )
            results.append(existing)
            print(f"  [skip] {company.name} (already researched)")
        else:
            to_process.append(company)

    if to_process:
        print(f"\nResearching {len(to_process)} companies...")
        tasks = [
            research_company(c, client, semaphore) for c in to_process
        ]
        new_results = await asyncio.gather(*tasks)
        results.extend(new_results)

    return results
