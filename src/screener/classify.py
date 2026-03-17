"""Stage 2: Classify - Determine if company is a programmatic acquirer."""

import asyncio
from pathlib import Path

from google import genai

from .config import (
    CLASSIFICATIONS_DIR,
    GEMINI_MODEL,
    GOOGLE_API_KEY,
    MAX_CONCURRENT_REQUESTS,
    REQUESTS_PER_MINUTE,
    RESEARCH_DIR,
)
from .models import Classification, ClassificationResponse, ResearchResult
from .prompts import CLASSIFICATION_PROMPT

_CLASSIFICATION_CONFIG = {
    "response_mime_type": "application/json",
    "response_json_schema": ClassificationResponse.model_json_schema(),
}


def _build_prompt(research: ResearchResult) -> str:
    return CLASSIFICATION_PROMPT.format(
        company_name=research.company_name,
        year=research.report_year or "unknown",
        extracted_text=research.extracted_text,
    )


def _parse_classification_response(
    response, research: ResearchResult
) -> Classification:
    """Parse structured Gemini response into a Classification."""
    text = response.text or ""

    try:
        data = ClassificationResponse.model_validate_json(text)

        return Classification(
            company_name=research.company_name,
            ticker=research.ticker,
            slug=research.slug,
            year=research.report_year,
            is_programmatic=data.is_programmatic,
            confidence=data.confidence,
            evidence=data.evidence,
            reasoning=data.reasoning,
        )
    except Exception:
        return Classification(
            company_name=research.company_name,
            ticker=research.ticker,
            slug=research.slug,
            year=research.report_year,
            error=f"Failed to parse structured response: {text[:200]}",
        )


def _result_path(slug: str) -> Path:
    return CLASSIFICATIONS_DIR / f"{slug}.json"


def load_research_results() -> list[ResearchResult]:
    """Load all research results from disk."""
    results = []
    for path in sorted(RESEARCH_DIR.glob("*.json")):
        result = ResearchResult.model_validate_json(path.read_text())
        results.append(result)
    return results


async def classify_company(
    research: ResearchResult,
    client: genai.Client,
    semaphore: asyncio.Semaphore,
) -> Classification:
    """Classify a single company with rate limiting."""
    async with semaphore:
        prompt = _build_prompt(research)

        try:
            response = await client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=_CLASSIFICATION_CONFIG,
            )
            result = _parse_classification_response(response, research)
        except Exception as e:
            result = Classification(
                company_name=research.company_name,
                ticker=research.ticker,
                slug=research.slug,
                year=research.report_year,
                error=str(e),
            )

        output_path = _result_path(research.slug)
        output_path.write_text(result.model_dump_json(indent=2))

    await asyncio.sleep(60 / REQUESTS_PER_MINUTE)
    return result


async def classify_companies(
    research_results: list[ResearchResult] | None = None,
    skip_existing: bool = True,
) -> list[Classification]:
    """Classify all researched companies."""
    if research_results is None:
        research_results = load_research_results()

    valid = [r for r in research_results if r.annual_report_found and not r.error]
    skipped_no_report = len(research_results) - len(valid)
    if skipped_no_report:
        print(f"  Skipping {skipped_no_report} companies (no annual report found)")

    client = genai.Client(vertexai=True, api_key=GOOGLE_API_KEY)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    to_process = []
    results = []

    for research in valid:
        if skip_existing and _result_path(research.slug).exists():
            existing = Classification.model_validate_json(
                _result_path(research.slug).read_text()
            )
            results.append(existing)
            print(f"  [skip] {research.company_name} (already classified)")
        else:
            to_process.append(research)

    if to_process:
        print(f"\nClassifying {len(to_process)} companies...")
        tasks = [
            classify_company(r, client, semaphore) for r in to_process
        ]
        new_results = await asyncio.gather(*tasks)
        results.extend(new_results)

    return results
