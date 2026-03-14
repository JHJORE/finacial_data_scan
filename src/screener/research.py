"""Stage 1: Research - Find annual reports using Gemini + Google Search grounding."""

import asyncio
import json
import time
from pathlib import Path

from google import genai
from google.genai import types

from .config import (
    GEMINI_MODEL,
    GOOGLE_API_KEY,
    MAX_CONCURRENT_REQUESTS,
    REQUESTS_PER_MINUTE,
    RESEARCH_DIR,
)
from .models import Company, ResearchResult
from .prompts import RESEARCH_PROMPT


def _build_prompt(company: Company) -> str:
    return RESEARCH_PROMPT.format(
        company_name=company.name,
        ticker=company.ticker,
    )


def _extract_grounding_metadata(response) -> tuple[list[str], list[dict]]:
    """Extract search queries and grounding chunks from Gemini response."""
    search_queries = []
    grounding_chunks = []

    if not hasattr(response, "candidates") or not response.candidates:
        return search_queries, grounding_chunks

    candidate = response.candidates[0]
    metadata = getattr(candidate, "grounding_metadata", None)
    if not metadata:
        return search_queries, grounding_chunks

    if hasattr(metadata, "web_search_queries") and metadata.web_search_queries:
        search_queries = list(metadata.web_search_queries)

    if hasattr(metadata, "grounding_chunks") and metadata.grounding_chunks:
        for chunk in metadata.grounding_chunks:
            web = getattr(chunk, "web", None)
            if web:
                grounding_chunks.append(
                    {"uri": getattr(web, "uri", ""), "title": getattr(web, "title", "")}
                )

    return search_queries, grounding_chunks


def _parse_research_response(response, company: Company) -> ResearchResult:
    """Parse Gemini response into a ResearchResult."""
    search_queries, grounding_chunks = _extract_grounding_metadata(response)

    text = response.text or ""

    # Try to parse JSON from response
    try:
        # Strip markdown code fences if present
        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()

        data = json.loads(clean)

        source_urls = data.get("source_urls", [])
        # Also add URLs from grounding chunks
        for chunk in grounding_chunks:
            uri = chunk.get("uri", "")
            if uri and uri not in source_urls:
                source_urls.append(uri)

        return ResearchResult(
            company_name=company.name,
            ticker=company.ticker,
            slug=company.slug,
            annual_report_found=data.get("annual_report_found", False),
            report_year=data.get("report_year"),
            source_urls=source_urls,
            extracted_text=data.get("extracted_text", ""),
            company_description=data.get("company_description", ""),
            search_queries_used=search_queries,
            grounding_chunks=grounding_chunks,
            raw_response={"text": text},
        )
    except (json.JSONDecodeError, AttributeError):
        return ResearchResult(
            company_name=company.name,
            ticker=company.ticker,
            slug=company.slug,
            extracted_text=text,
            search_queries_used=search_queries,
            grounding_chunks=grounding_chunks,
            raw_response={"text": text},
            error="Failed to parse JSON from response",
        )


def _result_path(company: Company) -> Path:
    return RESEARCH_DIR / f"{company.slug}.json"


def research_company_sync(company: Company) -> ResearchResult:
    """Research a single company synchronously."""
    client = genai.Client(api_key=GOOGLE_API_KEY)
    tool = types.Tool(google_search=types.GoogleSearch())
    config = types.GenerateContentConfig(tools=[tool])

    prompt = _build_prompt(company)

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=config,
        )
        result = _parse_research_response(response, company)
    except Exception as e:
        result = ResearchResult(
            company_name=company.name,
            ticker=company.ticker,
            slug=company.slug,
            error=str(e),
        )

    # Save result
    output_path = _result_path(company)
    output_path.write_text(result.model_dump_json(indent=2))

    return result


async def research_company_async(
    company: Company, client: genai.Client, semaphore: asyncio.Semaphore
) -> ResearchResult:
    """Research a single company with rate limiting."""
    async with semaphore:
        tool = types.Tool(google_search=types.GoogleSearch())
        config = types.GenerateContentConfig(tools=[tool])
        prompt = _build_prompt(company)

        try:
            response = await client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=config,
            )
            result = _parse_research_response(response, company)
        except Exception as e:
            result = ResearchResult(
                company_name=company.name,
                ticker=company.ticker,
                slug=company.slug,
                error=str(e),
            )

        # Save result
        output_path = _result_path(company)
        output_path.write_text(result.model_dump_json(indent=2))

        # Rate limit pause
        await asyncio.sleep(60 / REQUESTS_PER_MINUTE)

        return result


async def research_companies(
    companies: list[Company], skip_existing: bool = True
) -> list[ResearchResult]:
    """Research multiple companies concurrently with rate limiting."""
    client = genai.Client(api_key=GOOGLE_API_KEY)
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
            research_company_async(c, client, semaphore) for c in to_process
        ]
        new_results = await asyncio.gather(*tasks)
        results.extend(new_results)

    return results
