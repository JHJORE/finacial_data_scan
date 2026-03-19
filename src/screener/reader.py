"""Agent 2: Reader — Read the annual report and classify as programmatic acquirer.

For SEC filings: downloads HTML directly, strips tags, passes as text.
For non-SEC PDFs: downloads PDF, extracts text with pdfplumber, passes as text.
For non-SEC HTML (landing pages): uses url_context to read the page.
"""

import asyncio
import io
import re
from pathlib import Path

import httpx
import pdfplumber
from google import genai
from google.genai import types

from . import config
from .config import (
    AGENTS_DIR, GEMINI_MODEL, MAX_CONCURRENT_REQUESTS,
    MIN_VIABLE_TOKENS, SEC_MAX_RETRIES, SEC_MIN_VIABLE_TOKENS,
    THINKING_LEVEL, create_gemini_client,
)
from .models import ReaderResponse, ReaderResult, SearchResult
from .utils import backoff, extract_token_usage, is_retryable

_INSTRUCTIONS = (AGENTS_DIR / "reader.md").read_text()

_THINKING = types.ThinkingConfig(thinking_level=THINKING_LEVEL)

# Config with url_context — for HTML landing pages only
_READER_CONFIG = types.GenerateContentConfig(
    tools=[
        types.Tool(url_context=types.UrlContext()),
    ],
    thinking_config=_THINKING,
    response_mime_type="application/json",
    response_json_schema=ReaderResponse.model_json_schema(),
)

# Config without url_context — for SEC filings and downloaded PDFs
_DIRECT_READER_CONFIG = types.GenerateContentConfig(
    thinking_config=_THINKING,
    response_mime_type="application/json",
    response_json_schema=ReaderResponse.model_json_schema(),
)

_SEC_HEADERS = {"User-Agent": "FinancialDataScan/1.0 research@financialdatascan.com", "Accept": "text/html"}
_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/pdf,*/*;q=0.8",
}

_HTML_TAG_RE = re.compile(r'<[^>]+>')
_WHITESPACE_RE = re.compile(r'\s+')


# ── Download helpers ─────────────────────────────────────────

async def _download_sec_filing(url: str) -> str:
    """Download SEC filing HTML, strip tags, and return plain text."""
    async with httpx.AsyncClient(headers=_SEC_HEADERS, timeout=30, follow_redirects=True) as http:
        resp = await http.get(url)
        resp.raise_for_status()
        raw_chars = len(resp.text)
        text = _HTML_TAG_RE.sub(' ', resp.text)
        text = _WHITESPACE_RE.sub(' ', text).strip()
        print(f"  [sec] stripped HTML: {raw_chars:,} → {len(text):,} chars")
        return text


async def _download_pdf(url: str) -> str:
    """Download PDF and extract text using pdfplumber."""
    async with httpx.AsyncClient(
        headers=_BROWSER_HEADERS, timeout=60, follow_redirects=True
    ) as http:
        resp = await http.get(url)
        resp.raise_for_status()
        pdf_bytes = resp.content

    text_parts = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
        num_pages = len(pdf.pages)

    text = "\n".join(text_parts)
    print(f"  [pdf] extracted {len(text):,} chars from {num_pages} pages")
    return text


async def _is_pdf_url(url: str) -> bool:
    """Check if a URL points to a PDF by inspecting content-type."""
    if url.lower().endswith('.pdf'):
        return True
    try:
        async with httpx.AsyncClient(
            headers=_BROWSER_HEADERS, timeout=10, follow_redirects=True
        ) as http:
            resp = await http.head(url)
            content_type = resp.headers.get("content-type", "").lower()
            return "application/pdf" in content_type
    except Exception:
        return False


# ── Prompt builders ──────────────────────────────────────────

def _build_prompt(search: SearchResult) -> str:
    """Build prompt for url_context-based reading (HTML pages)."""
    return _INSTRUCTIONS.format(
        company_name=search.company_name,
        ticker=search.ticker,
        source_url=search.source_url,
        report_year=search.report_year or "unknown",
    )


def _build_direct_prompt(search: SearchResult, document_text: str, tag: str = "annual_report") -> str:
    """Build prompt with document text embedded directly (SEC filings and PDFs)."""
    base = _INSTRUCTIONS.format(
        company_name=search.company_name,
        ticker=search.ticker,
        source_url=search.source_url,
        report_year=search.report_year or "unknown",
    )
    return (
        f"{base}\n\n"
        f"<{tag}>\n"
        f"The document content is provided below. "
        f"Analyze this text directly — do NOT use url_context.\n\n"
        f"{document_text}\n"
        f"</{tag}>"
    )


# ── Result helpers ───────────────────────────────────────────

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


# ── Reader functions ─────────────────────────────────────────

async def _read_direct(
    search: SearchResult,
    client: genai.Client,
    document_text: str,
    source_label: str,
) -> ReaderResult:
    """Classify a company using directly-provided document text (SEC or PDF)."""
    total_in, total_out = 0, 0
    doc_chars = len(document_text)
    max_retries = SEC_MAX_RETRIES

    for attempt in range(max_retries):
        try:
            prompt = _build_direct_prompt(search, document_text)
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config=_DIRECT_READER_CONFIG,
                ),
                timeout=180,
            )

            input_tok, output_tok = extract_token_usage(response)
            total_in += input_tok
            total_out += output_tok

            data = ReaderResponse.model_validate_json(response.text or "")
            result = _build_reader_result(
                data, search,
                url_retrieval_status="DIRECT_DOWNLOAD",
                document_token_count=doc_chars,
                total_input_tokens=total_in,
                total_output_tokens=total_out,
            )
            tag = "PROGRAMMATIC" if result.is_programmatic else "not programmatic"
            print(f"  [ok] {search.company_name}: {tag} ({result.confidence}) "
                  f"[{doc_chars:,} chars via {source_label}]")
            _save_result(result)
            return result

        except Exception as e:
            error_str = str(e) or type(e).__name__
            if is_retryable(error_str) and attempt < max_retries - 1:
                wait = backoff(attempt)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    wait *= 3
                print(f"  [retry] {search.company_name}: {error_str[:80]}, "
                      f"attempt {attempt + 1}/{max_retries}, waiting {wait:.0f}s...")
                await asyncio.sleep(wait)
                continue
            print(f"  [error] {search.company_name}: {error_str[:120]}")
            break

    result = _failed_result(search, total_in, total_out)
    _save_result(result)
    return result


async def _read_url_context(
    search: SearchResult,
    client: genai.Client,
) -> ReaderResult:
    """Read and classify via url_context (for HTML landing pages)."""
    prompt = _build_prompt(search)
    total_in, total_out = 0, 0
    max_retries = 2

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

            if tool_tokens < MIN_VIABLE_TOKENS:
                if attempt < max_retries - 1:
                    wait = backoff(attempt)
                    print(f"  [retry] {search.company_name}: only {tool_tokens:,} tokens "
                          f"(need {MIN_VIABLE_TOKENS:,}), status={retrieval_status}, "
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
            error_str = str(e) or type(e).__name__
            if is_retryable(error_str) and attempt < max_retries - 1:
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    wait = backoff(attempt) * 3
                else:
                    wait = backoff(attempt)
                print(f"  [retry] {search.company_name}: {error_str[:80]}, "
                      f"attempt {attempt + 1}/{max_retries}, waiting {wait:.0f}s...")
                await asyncio.sleep(wait)
                continue
            print(f"  [error] {search.company_name}: {error_str[:120]}")
            break

    result = _failed_result(search, total_in, total_out)
    _save_result(result)
    return result


# ── Public API ───────────────────────────────────────────────

async def read_company(
    search: SearchResult,
    client: genai.Client,
    semaphore: asyncio.Semaphore,
) -> ReaderResult:
    """Read and classify a single company."""
    async with semaphore:
        if search.source_type == "sec_edgar":
            # SEC: download HTML, strip tags, pass text directly
            try:
                filing_text = await _download_sec_filing(search.source_url)
            except Exception as e:
                print(f"  [error] {search.company_name}: SEC download failed: {e}")
                result = _failed_result(search, 0, 0)
                _save_result(result)
                return result
            return await _read_direct(search, client, filing_text, "sec_html")

        # Non-SEC: check if the URL is a PDF
        is_pdf = await _is_pdf_url(search.source_url)
        if is_pdf:
            # Download PDF, extract text, pass directly
            try:
                pdf_text = await _download_pdf(search.source_url)
            except Exception as e:
                print(f"  [error] {search.company_name}: PDF download failed: {e}")
                # Fall back to url_context
                print(f"  [fallback] {search.company_name}: trying url_context instead")
                return await _read_url_context(search, client)
            return await _read_direct(search, client, pdf_text, "pdf_download")
        else:
            # HTML landing page: use url_context
            print(f"  [url_context] {search.company_name}: reading HTML page at {search.source_url[:80]}")
            return await _read_url_context(search, client)


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
