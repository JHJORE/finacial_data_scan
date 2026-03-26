"""Agent 2: Reader — Read the annual report and classify as programmatic acquirer.

For SEC filings: checks EDGAR index for PDF, falls back to HTML parsed with
BeautifulSoup. Non-SEC PDFs are sent as native bytes via Part.from_bytes.
Non-SEC HTML (landing pages) use url_context.
"""

import asyncio
import io
import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import httpx
import pdfplumber
from google import genai
from google.genai import types

from . import config
from .config import (
    AGENTS_DIR, GEMINI_MODEL, MAX_CONCURRENT_REQUESTS,
    MIN_VIABLE_TOKENS, SEC_MAX_RETRIES,
    SEC_MIN_CHARS, SEC_MIN_VIABLE_TOKENS,
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

_BLANK_LINE_RE = re.compile(r'\n{3,}')


# ── HTML parsing ─────────────────────────────────────────────

def _table_to_text(table_tag) -> str:
    """Convert an HTML <table> to pipe-separated plain text."""
    rows = []
    for tr in table_tag.find_all("tr"):
        cells = []
        for td in tr.find_all(["td", "th"]):
            cell_text = td.get_text(separator=" ", strip=True)
            if cell_text:
                cells.append(cell_text)
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _parse_sec_html(raw_html: str) -> str:
    """Parse SEC filing HTML with BeautifulSoup, preserving table structure.

    Removes script/style/head content (not just tags), converts tables to
    pipe-separated text, decodes HTML entities, and normalizes whitespace.
    """
    soup = BeautifulSoup(raw_html, "html.parser")

    # Remove non-content elements and their contents
    for tag in soup.find_all(["script", "style", "head", "meta", "link", "noscript"]):
        tag.decompose()

    # Convert tables to readable text before extracting all text
    for table in soup.find_all("table"):
        table_text = _table_to_text(table)
        table.replace_with(table_text)

    # Extract text — BeautifulSoup auto-decodes entities
    text = soup.get_text(separator="\n")

    # Normalize: strip trailing spaces per line, collapse excessive blank lines
    lines = [line.rstrip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = _BLANK_LINE_RE.sub("\n\n", text).strip()

    return text


# ── Download helpers ─────────────────────────────────────────

async def _check_edgar_index_for_pdf(filing_url: str) -> str | None:
    """Check the EDGAR filing index for a PDF version of the document.

    Derives the index URL from the filing URL, fetches the index JSON,
    and looks for any .pdf file in the listing.
    """
    # Derive the directory URL from the filing URL
    # e.g., https://www.sec.gov/Archives/edgar/data/CIK/ACC/filename.htm
    #     → https://www.sec.gov/Archives/edgar/data/CIK/ACC/index.json
    base_url = filing_url.rsplit("/", 1)[0] + "/"
    index_url = base_url + "index.json"

    try:
        async with httpx.AsyncClient(headers=_SEC_HEADERS, timeout=15, follow_redirects=True) as http:
            resp = await http.get(index_url)
            if resp.status_code != 200:
                return None

            data = resp.json()
            items = data.get("directory", {}).get("item", [])

            for item in items:
                name = item.get("name", "")
                if name.lower().endswith(".pdf"):
                    return urljoin(base_url, name)

    except Exception:
        pass

    return None


async def _download_sec_filing(url: str) -> tuple[str | bytes, str]:
    """Download SEC filing, trying PDF first, falling back to HTML parsing.

    Returns:
        (content, format): content is either PDF bytes or parsed text string,
        format is either "pdf" or "html".
    """
    # Check if a PDF version exists on the EDGAR index
    pdf_url = await _check_edgar_index_for_pdf(url)
    if pdf_url:
        try:
            async with httpx.AsyncClient(
                headers=_SEC_HEADERS, timeout=60, follow_redirects=True
            ) as http:
                resp = await http.get(pdf_url)
                resp.raise_for_status()
                pdf_bytes = resp.content
                print(f"  [sec] downloaded PDF from EDGAR index: {len(pdf_bytes):,} bytes")
                return pdf_bytes, "pdf"
        except Exception as e:
            print(f"  [sec] PDF download failed ({e}), falling back to HTML")

    # Download and parse HTML
    async with httpx.AsyncClient(headers=_SEC_HEADERS, timeout=30, follow_redirects=True) as http:
        resp = await http.get(url)
        resp.raise_for_status()
        raw_chars = len(resp.text)

    text = _parse_sec_html(resp.text)
    print(f"  [sec] parsed HTML: {raw_chars:,} -> {len(text):,} chars")

    if len(text) < SEC_MIN_CHARS:
        raise ValueError(
            f"SEC filing text too short ({len(text):,} chars < {SEC_MIN_CHARS:,}), "
            f"download may have failed"
        )

    return text, "html"


async def _download_pdf_bytes(url: str) -> bytes:
    """Download a PDF and return raw bytes."""
    async with httpx.AsyncClient(
        headers=_BROWSER_HEADERS, timeout=60, follow_redirects=True
    ) as http:
        resp = await http.get(url)
        resp.raise_for_status()
        print(f"  [pdf] downloaded {len(resp.content):,} bytes")
        return resp.content


async def _download_pdf(url: str) -> str:
    """Download PDF and extract text using pdfplumber (fallback path)."""
    pdf_bytes = await _download_pdf_bytes(url)

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


def _verify_pdf_belongs_to_company(pdf_bytes: bytes, company_name: str) -> bool:
    """Check that the first pages of a PDF mention the company name.

    Extracts text from the first 5 pages and checks if any significant
    token from the company name appears. This catches wrong-company PDFs
    early (e.g., getting Guerbet's report when searching for Saint-Gobain).
    """
    skip_words = {"ab", "as", "asa", "plc", "inc", "ltd", "corp", "se", "sa",
                  "ag", "nv", "oyj", "the", "and", "of", "co", "group", "kga"}
    name_tokens = [
        w for w in company_name.lower().split()
        if w not in skip_words and len(w) >= 3
    ]
    if not name_tokens:
        # Very short/generic name — can't reliably verify, let it through
        return True

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_to_check = min(len(pdf.pages), 5)
            text = ""
            for i in range(pages_to_check):
                page_text = pdf.pages[i].extract_text() or ""
                text += page_text.lower() + " "

        # Check if any significant name token appears in the first pages
        for token in name_tokens:
            if token in text:
                return True

        return False
    except Exception:
        # If we can't parse the PDF, let it through — the LLM will catch it
        return True


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
        first_entry=search.first_entry,
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
        first_entry=search.first_entry,
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
            print(f"  [ok] {search.company_name} ({search.report_year}): {tag} ({result.confidence}) "
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


async def _read_pdf_native(
    search: SearchResult,
    client: genai.Client,
    pdf_bytes: bytes,
) -> ReaderResult:
    """Classify using Gemini's native PDF understanding via Part.from_bytes."""
    total_in, total_out = 0, 0
    max_retries = SEC_MAX_RETRIES

    base_prompt = _INSTRUCTIONS.format(
        company_name=search.company_name,
        ticker=search.ticker,
        source_url=search.source_url,
        report_year=search.report_year or "unknown",
    )
    prompt_text = (
        f"{base_prompt}\n\n"
        "The annual report PDF is attached above. "
        "Analyze the document directly — do NOT use url_context."
    )

    contents = [
        types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
        prompt_text,
    ]

    for attempt in range(max_retries):
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=contents,
                    config=_DIRECT_READER_CONFIG,
                ),
                timeout=300,
            )

            input_tok, output_tok = extract_token_usage(response)
            total_in += input_tok
            total_out += output_tok

            data = ReaderResponse.model_validate_json(response.text or "")
            result = _build_reader_result(
                data, search,
                url_retrieval_status="PDF_NATIVE",
                document_token_count=input_tok,
                total_input_tokens=total_in,
                total_output_tokens=total_out,
            )
            tag = "PROGRAMMATIC" if result.is_programmatic else "not programmatic"
            print(f"  [ok] {search.company_name} ({search.report_year}): {tag} ({result.confidence}) "
                  f"[{len(pdf_bytes):,} bytes via pdf_native]")
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
            print(f"  [ok] {search.company_name} ({search.report_year}): {tag} ({result.confidence}) "
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
            # SEC: try PDF from EDGAR index, fall back to parsed HTML
            try:
                content, fmt = await _download_sec_filing(search.source_url)
            except Exception as e:
                print(f"  [error] {search.company_name}: SEC download failed: {e}")
                result = _failed_result(search, 0, 0)
                _save_result(result)
                return result

            if fmt == "pdf":
                return await _read_pdf_native(search, client, content)
            return await _read_direct(search, client, content, "sec_html")

        # Non-SEC: check if the URL is a PDF
        is_pdf = await _is_pdf_url(search.source_url)
        if is_pdf:
            # Download PDF bytes and verify it's the right company
            try:
                pdf_bytes = await _download_pdf_bytes(search.source_url)

                if not _verify_pdf_belongs_to_company(pdf_bytes, search.company_name):
                    print(f"  [wrong doc] {search.company_name}: PDF does not mention "
                          f"company name in first 5 pages, rejecting")
                    result = _failed_result(search, 0, 0)
                    result.error = f"PDF does not belong to {search.company_name} (name not found in first 5 pages)"
                    _save_result(result)
                    return result

                return await _read_pdf_native(search, client, pdf_bytes)
            except Exception as e:
                print(f"  [error] {search.company_name}: PDF native failed: {e}")
                # Fall back to pdfplumber text extraction
                try:
                    pdf_text = await _download_pdf(search.source_url)
                    return await _read_direct(search, client, pdf_text, "pdf_download")
                except Exception as e2:
                    print(f"  [error] {search.company_name}: PDF text extraction failed: {e2}")
                    print(f"  [fallback] {search.company_name}: trying url_context instead")
                    return await _read_url_context(search, client)
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
            print(f"  [skip] {search.company_name} ({search.report_year}) (already read)")
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
