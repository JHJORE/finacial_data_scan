"""Agent 1: Search — Find the best annual report source.

For US companies: SEC EDGAR EFTS API (no AI needed).
For non-US companies:
  1. Combined google_search + url_context (model searches AND reads pages)
  2. Fallback: explicit url_context read on landing pages if combined call
     didn't produce a valid URL.
"""

import asyncio
import re
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from google import genai
from google.genai import types

from . import config
from .config import AGENTS_DIR, GEMINI_MODEL, MAX_CONCURRENT_REQUESTS, create_gemini_client
from .models import Company, SearchResult
from .utils import backoff, extract_token_usage, is_retryable

_INSTRUCTIONS = (AGENTS_DIR / "search.md").read_text()

_REDIRECT_HOST = "vertexaisearch.cloud.google.com"

# Bloomberg exchange suffix → locale info for search hints
_LOCALE_MAP: dict[str, dict[str, str]] = {
    "SS": {"country": "Sweden", "language": "Swedish", "local_term": "årsredovisning"},
    "NO": {"country": "Norway", "language": "Norwegian", "local_term": "årsrapport"},
    "DC": {"country": "Denmark", "language": "Danish", "local_term": "årsrapport"},
    "FH": {"country": "Finland", "language": "Finnish", "local_term": "vuosikertomus"},
    "GR": {"country": "Germany", "language": "German", "local_term": "Geschäftsbericht"},
    "SW": {"country": "Switzerland", "language": "German", "local_term": "Geschäftsbericht"},
    "FP": {"country": "France", "language": "French", "local_term": "rapport annuel"},
    "NA": {"country": "Netherlands", "language": "Dutch", "local_term": "jaarverslag"},
    "BB": {"country": "Belgium", "language": "Dutch/French", "local_term": "jaarverslag / rapport annuel"},
    "IM": {"country": "Italy", "language": "Italian", "local_term": "relazione annuale"},
    "SM": {"country": "Spain", "language": "Spanish", "local_term": "informe anual"},
    "PL": {"country": "Poland", "language": "Polish", "local_term": "raport roczny"},
    "LN": {"country": "United Kingdom", "language": "English", "local_term": "annual report"},
    "ID": {"country": "Ireland", "language": "English", "local_term": "annual report"},
    "CN": {"country": "Canada", "language": "English/French", "local_term": "annual report / rapport annuel"},
    "CT": {"country": "Canada", "language": "English/French", "local_term": "annual report / rapport annuel"},
    "AT": {"country": "Australia", "language": "English", "local_term": "annual report"},
    "US": {"country": "United States", "language": "English", "local_term": "annual report"},
}


def _ticker_to_locale(ticker: str) -> dict[str, str]:
    """Extract locale info from Bloomberg ticker exchange suffix."""
    parts = ticker.strip().split()
    if len(parts) >= 2:
        exchange = parts[-2].upper() if parts[-1].upper() == "EQUITY" else parts[-1].upper()
        if exchange in _LOCALE_MAP:
            return _LOCALE_MAP[exchange]
    return {"country": "Unknown", "language": "English", "local_term": "annual report"}


_COMBINED_SYSTEM = (
    "<role>You are a web researcher with google_search and url_context tools.</role>\n"
    "<rules>\n"
    "  - You MUST use google_search (up to 3 searches) before responding\n"
    "  - Include the company ticker symbol in your search queries for better results\n"
    "  - Then use url_context to READ the most promising result page and find the annual report download link\n"
    "  - On the page, list ALL document links before selecting the best one\n"
    "  - Keep search queries simple: no filetype:, inurl:, or other operators\n"
    "  - After reading a page with url_context, include ALL URLs you found in your response text\n"
    "  - Even if you couldn't find the annual report, output the company website URL you visited\n"
    "  - Your response MUST contain at least one URL\n"
    "  - Return real download URLs, never Google redirect URLs\n"
    "  - The annual report PDF usually won't appear directly in search results — you typically need to navigate to it through the company's website\n"
    "  - If a search result IS already a direct PDF link, use it directly without further navigation\n"
    "</rules>"
)

_COMBINED_CONFIG = types.GenerateContentConfig(
    system_instruction=_COMBINED_SYSTEM,
    tools=[
        types.Tool(google_search=types.GoogleSearch()),
        types.Tool(url_context=types.UrlContext()),
    ],
)

_URL_READ_CONFIG = types.GenerateContentConfig(
    tools=[types.Tool(url_context=types.UrlContext())],
)

_URL_RE = re.compile(r'https?://[^\s<>"\')\]]+')

_SEC_HEADERS = {"User-Agent": "FinancialDataScan/1.0 (research tool)", "Accept": "application/json"}
_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
}

_MIN_PDF_SIZE = 10_000
_VALID_CONTENT_TYPES = {"application/pdf", "text/html"}
_URL_JUNK_TAIL = re.compile(r'[*\s]+$')


def _is_redirect_url(url: str) -> bool:
    return _REDIRECT_HOST in url


def _clean_url(url: str) -> str:
    """Strip trailing wildcards, whitespace, and other junk from URLs."""
    return _URL_JUNK_TAIL.sub("", url)


async def _find_sec_filing(company_name: str, target_year: int) -> str | None:
    """Use EDGAR EFTS API to find the correct 10-K/20-F URL for a US company."""
    start = f"{target_year - 1}-06-01"
    end = f"{target_year + 1}-06-01"
    query = quote(f'"{company_name}"')
    efts_url = (
        f"https://efts.sec.gov/LATEST/search-index?"
        f"q={query}&forms=10-K,20-F&dateRange=custom&startdt={start}&enddt={end}"
    )

    name_lower = company_name.lower().split()[0]

    try:
        async with httpx.AsyncClient(headers=_SEC_HEADERS, timeout=15) as http:
            resp = await http.get(efts_url)
            if resp.status_code != 200:
                return None

            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])

            for hit in hits:
                src = hit.get("_source", {})
                file_type = src.get("file_type", "")
                if file_type not in ("10-K", "20-F"):
                    continue

                display_names = src.get("display_names", [])
                if not any(name_lower in dn.lower() for dn in display_names):
                    continue

                hit_id = hit.get("_id", "")
                if ":" not in hit_id:
                    continue

                accession, filename = hit_id.split(":", 1)
                ciks = src.get("ciks", [])
                if not ciks:
                    continue

                cik = ciks[0].lstrip("0") or "0"
                acc_no_dashes = accession.replace("-", "")
                return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dashes}/{filename}"

    except Exception:
        pass

    return None


async def _validate_url(url: str) -> tuple[bool, str]:
    """Check that a URL returns HTTP 200 with a valid document content-type."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, headers=_BROWSER_HEADERS, timeout=15
        ) as http:
            resp = await http.head(url)
            if resp.status_code == 405:
                resp = await http.get(url, headers={**_BROWSER_HEADERS, "Range": "bytes=0-1024"})
            if resp.status_code not in (200, 206):
                return False, f"HTTP {resp.status_code}"

            content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type not in _VALID_CONTENT_TYPES:
                return False, f"bad content-type: {content_type}"

            if content_type == "application/pdf":
                length = int(resp.headers.get("content-length", "0"))
                if length and length < _MIN_PDF_SIZE:
                    return False, f"PDF too small ({length} bytes)"

            return True, "ok"
    except Exception as e:
        return False, str(e)[:100]


def _build_prompt(company: Company) -> str:
    target_year = config.TARGET_YEAR
    locale = _ticker_to_locale(company.ticker)
    # Extract short ticker (e.g., "CSU" from "CSU CN Equity")
    ticker_short = company.ticker.split()[0] if company.ticker else company.ticker
    locale_hint = f"{locale['country']} ({locale['language']})"
    return _INSTRUCTIONS.format(
        company_name=company.name,
        ticker=company.ticker,
        ticker_short=ticker_short,
        target_year=target_year,
        fallback_year=target_year - 1,
        locale_hint=locale_hint,
        local_term=locale["local_term"],
    )


def _get_grounding_metadata(response):
    if not hasattr(response, "candidates") or not response.candidates:
        return None
    return getattr(response.candidates[0], "grounding_metadata", None)


def _extract_search_queries(response) -> list[str]:
    metadata = _get_grounding_metadata(response)
    if metadata and hasattr(metadata, "web_search_queries") and metadata.web_search_queries:
        return list(metadata.web_search_queries)
    return []


def _was_url_context_used(response) -> bool:
    """Check url_context_metadata to see if the model actually read any pages."""
    if not hasattr(response, "candidates") or not response.candidates:
        return False
    url_meta = getattr(response.candidates[0], "url_context_metadata", None)
    if url_meta and hasattr(url_meta, "url_metadata") and url_meta.url_metadata:
        return True
    return False


def _extract_real_urls(text: str) -> list[str]:
    """Extract URLs from text, filtering out Google redirect URLs."""
    raw = _URL_RE.findall(text)
    cleaned = [_clean_url(u) for u in raw]
    return list(dict.fromkeys(u for u in cleaned if not _is_redirect_url(u)))


def _extract_grounding_urls(response) -> list[str]:
    """Extract URLs from grounding metadata (actual search result URLs)."""
    metadata = _get_grounding_metadata(response)
    if not metadata:
        return []
    chunks = getattr(metadata, "grounding_chunks", None)
    if not chunks:
        return []
    urls = []
    for chunk in chunks:
        web = getattr(chunk, "web", None)
        if web:
            uri = getattr(web, "uri", None)
            if uri and not _is_redirect_url(uri):
                urls.append(_clean_url(uri))
    return list(dict.fromkeys(urls))


def _extract_grounding_redirect_urls(response) -> list[str]:
    """Extract ALL grounding chunk URLs including redirect URLs."""
    metadata = _get_grounding_metadata(response)
    if not metadata:
        return []
    chunks = getattr(metadata, "grounding_chunks", None)
    if not chunks:
        return []
    urls = []
    for chunk in chunks:
        web = getattr(chunk, "web", None)
        if web:
            uri = getattr(web, "uri", None)
            if uri:
                urls.append(uri)
    return list(dict.fromkeys(urls))


async def _resolve_redirect(url: str) -> str | None:
    """Follow a redirect URL to get the real destination URL."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, headers=_BROWSER_HEADERS, timeout=5
        ) as http:
            resp = await http.head(url)
            final_url = str(resp.url)
            if final_url != url and not _is_redirect_url(final_url):
                return _clean_url(final_url)
    except Exception:
        pass
    return None


async def _resolve_grounding_redirects(response) -> list[str]:
    """Resolve grounding redirect URLs to real destination URLs.

    Grounding chunks from google_search always return redirect URLs through
    vertexaisearch.cloud.google.com. This function follows the redirects
    to get the actual search result URLs.
    """
    redirect_urls = _extract_grounding_redirect_urls(response)
    redirect_urls = [u for u in redirect_urls if _is_redirect_url(u)]
    if not redirect_urls:
        return []

    # Resolve up to 5 redirect URLs in parallel
    tasks = [_resolve_redirect(u) for u in redirect_urls[:5]]
    results = await asyncio.gather(*tasks)
    resolved = [u for u in results if u is not None]
    return list(dict.fromkeys(resolved))


def _classify_source_type(url: str) -> str:
    host = urlparse(url).hostname or ""
    if "sec.gov" in host:
        return "sec_edgar"
    return "investor_relations"


def _result_path(company: Company) -> Path:
    return config.SEARCH_DIR / f"{company.slug}.json"


def _make_result(company: Company, *, status: str = "not_found", **kwargs) -> SearchResult:
    return SearchResult(
        company_name=company.name,
        ticker=company.ticker,
        slug=company.slug,
        status=status,
        **kwargs,
    )


async def search_company(
    company: Company, client: genai.Client, semaphore: asyncio.Semaphore,
) -> SearchResult:
    """Search for a single company's annual report.

    SEC EDGAR for US companies, combined google_search + url_context for others.
    """
    async with semaphore:
        total_in, total_out = 0, 0
        target_year = config.TARGET_YEAR

        # ── SEC EDGAR for US companies (direct, no AI) ─────────────
        sec_url = await _find_sec_filing(company.name, target_year)
        if sec_url:
            result = _make_result(
                company, status="found",
                report_year=target_year,
                source_url=sec_url,
                source_type="sec_edgar",
                source_rationale="Verified via SEC EDGAR EFTS API",
                url_validated=True,
            )
            print(f"  [ok] {company.name}: sec_edgar → {sec_url[:90]}")
            _save_result(company, result)
            return result

        # ── Combined google_search + url_context ───────────────────
        prompt = _build_prompt(company)
        search_queries: list[str] = []
        real_urls: list[str] = []

        for attempt in range(2):
            try:
                resp = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=prompt,
                        config=_COMBINED_CONFIG,
                    ),
                    timeout=120,
                )
                in_tok, out_tok = extract_token_usage(resp)
                total_in += in_tok
                total_out += out_tok
                search_queries = _extract_search_queries(resp)

                url_ctx = _was_url_context_used(resp)
                real_urls = _extract_real_urls(resp.text or "")
                # Also pull URLs from grounding metadata (actual search results)
                grounding_urls = _extract_grounding_urls(resp)
                # Count redirect URLs in grounding (before filtering)
                all_grounding = _extract_grounding_redirect_urls(resp)
                num_redirects = len([u for u in all_grounding if _is_redirect_url(u)])
                # Merge: response URLs first (model's picks), then grounding URLs
                all_urls = list(dict.fromkeys(real_urls + grounding_urls))
                real_urls = all_urls

                if search_queries:
                    print(f"    [{company.name}] queries: {search_queries}")
                print(f"    [{company.name}] {len(real_urls)} real URLs"
                      f" ({len(grounding_urls)} direct + {num_redirects} redirect from grounding)"
                      f"{', url_context used' if url_ctx else ''}")
                break
            except Exception as e:
                error_str = str(e)
                if is_retryable(error_str) and attempt == 0:
                    wait = backoff(attempt)
                    print(f"  [retry] {company.name}: {error_str[:80]}")
                    await asyncio.sleep(wait)
                    continue
                print(f"  [error] {company.name}: search failed: {error_str[:120]}")
                result = _make_result(
                    company,
                    total_input_tokens=total_in,
                    total_output_tokens=total_out,
                    error=error_str,
                )
                _save_result(company, result)
                return result

        urls_from_redirects = False
        if not real_urls:
            # Try resolving grounding redirect URLs to get real search result URLs
            resolved_urls = await _resolve_grounding_redirects(resp)
            if resolved_urls:
                print(f"    [{company.name}] resolved {len(resolved_urls)} grounding redirect URLs: "
                      f"{', '.join(u[:60] for u in resolved_urls[:3])}")
                real_urls = resolved_urls
                urls_from_redirects = True
            else:
                result = _make_result(
                    company,
                    search_queries_used=search_queries,
                    total_input_tokens=total_in,
                    total_output_tokens=total_out,
                )
                print(f"  [not found] {company.name}: no real URLs in search results")
                _save_result(company, result)
                return result

        # ── Validate URLs from combined call ───────────────────────
        # Skip direct validation for redirect-resolved URLs (they're landing
        # pages that need navigation, not direct document links)
        if not urls_from_redirects:
            for url in real_urls:
                valid, reason = await _validate_url(url)
                if valid:
                    result = _make_result(
                        company, status="found",
                        report_year=target_year,
                        source_url=url,
                        source_type=_classify_source_type(url),
                        source_rationale="Found via combined google_search + url_context",
                        url_validated=True,
                        search_queries_used=search_queries,
                        total_input_tokens=total_in,
                        total_output_tokens=total_out,
                    )
                    print(f"  [ok] {company.name}: {result.source_type} → {url[:90]}")
                    _save_result(company, result)
                    return result
                print(f"    [validate] {url[:90]} → {reason}")

        # ── Fallback: navigate landing pages with url_context ────
        locale = _ticker_to_locale(company.ticker)
        nav_count = min(len(real_urls), 5)
        print(f"    [{company.name}] navigating {nav_count} landing pages")
        first_valid_page = None

        for page_url in real_urls[:5]:
            # Step 1: Enumerate all document links on the page
            enumerate_prompt = (
                f"<role>You are reading a company's web page to find an annual report download link.</role>\n"
                f"<context>\n"
                f"  <page_url>{page_url}</page_url>\n"
                f"  <company>{company.name}</company>\n"
                f"  <target_year>{target_year}</target_year>\n"
                f"  <fallback_year>{target_year - 1}</fallback_year>\n"
                f"</context>\n"
                f"<task>\n"
                f"  1. Read this page carefully\n"
                f"  2. List ALL downloadable document links you find (PDFs, download buttons, document archive links)\n"
                f"  3. For each link, state: the URL, the document title/label, and the year if visible\n"
                f"  4. Then identify which link is the annual report for fiscal year {target_year} (or {target_year - 1})\n"
                f"  5. Return ONLY that URL as the last line of your response\n"
                f"</task>\n"
                f"<hints>\n"
                f"  - Annual reports may be labeled: \"{locale['local_term']}\"\n"
                f"  - Look for PDF icons, download sections, document libraries\n"
                f"  - The link might be in a table, list, or card layout\n"
                f"  - If this is a navigation page, look for a link to the reports/documents section\n"
                f"</hints>"
            )

            try:
                read_resp = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=enumerate_prompt,
                        config=_URL_READ_CONFIG,
                    ),
                    timeout=120,
                )
                in_tok, out_tok = extract_token_usage(read_resp)
                total_in += in_tok
                total_out += out_tok

                pdf_urls = _extract_real_urls(read_resp.text or "")
                pdf_urls = [u for u in pdf_urls if u != page_url]

                for pdf_url in pdf_urls:
                    pdf_valid, pdf_reason = await _validate_url(pdf_url)
                    if pdf_valid:
                        result = _make_result(
                            company, status="found",
                            report_year=target_year,
                            source_url=pdf_url,
                            source_type=_classify_source_type(pdf_url),
                            source_rationale="Found via url_context page navigation (fallback)",
                            url_validated=True,
                            search_queries_used=search_queries,
                            total_input_tokens=total_in,
                            total_output_tokens=total_out,
                        )
                        print(f"  [ok] {company.name}: {result.source_type} → {pdf_url[:90]}")
                        _save_result(company, result)
                        return result
                    print(f"    [validate pdf] {pdf_url[:90]} → {pdf_reason}")

                if first_valid_page is None:
                    page_valid, _ = await _validate_url(page_url)
                    if page_valid:
                        first_valid_page = page_url

                print(f"    [navigate] {page_url[:60]}: no valid PDF link found")
            except Exception as e:
                print(f"    [navigate error] {page_url[:60]}: {str(e)[:80]}")
                continue

        # ── Last resort: return landing page for reader to try ─────
        if first_valid_page:
            result = _make_result(
                company, status="found",
                report_year=target_year,
                source_url=first_valid_page,
                source_type=_classify_source_type(first_valid_page),
                source_rationale="Landing page (no direct PDF link found)",
                url_validated=True,
                search_queries_used=search_queries,
                total_input_tokens=total_in,
                total_output_tokens=total_out,
            )
            print(f"  [ok] {company.name}: landing page → {first_valid_page[:90]}")
            _save_result(company, result)
            return result

        result = _make_result(
            company,
            search_queries_used=search_queries,
            total_input_tokens=total_in,
            total_output_tokens=total_out,
            error=f"No valid URL found ({len(real_urls)} URLs tried)",
        )
        print(f"  [not found] {company.name}: no valid URLs found")
        _save_result(company, result)
        return result


def _save_result(company: Company, result: SearchResult) -> None:
    output_path = _result_path(company)
    output_path.write_text(result.model_dump_json(indent=2))


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
