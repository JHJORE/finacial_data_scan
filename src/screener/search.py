"""Agent 1: Search — Find the best annual report source.

For US companies: SEC EDGAR EFTS API (no AI needed).
For non-US companies:
  1. Search-only call (google_search) to find relevant pages
  2. Resolve grounding redirect URLs to get real destination URLs
  3. Navigate landing pages with url_context to find PDF links
"""

import asyncio
import re
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from google import genai
from google.genai import types

from . import config
from .config import AGENTS_DIR, GEMINI_MODEL, MAX_CONCURRENT_REQUESTS, THINKING_LEVEL, create_gemini_client
from .models import Company, SearchResult
from .utils import backoff, extract_token_usage, is_retryable

_INSTRUCTIONS = (AGENTS_DIR / "search.md").read_text()

_REDIRECT_HOST = "vertexaisearch.cloud.google.com"

# Bloomberg exchange suffix → country for search hints
_LOCALE_MAP: dict[str, dict[str, str]] = {
    "SS": {"country": "Sweden"},
    "NO": {"country": "Norway"},
    "DC": {"country": "Denmark"},
    "FH": {"country": "Finland"},
    "GR": {"country": "Germany"},
    "SW": {"country": "Switzerland"},
    "FP": {"country": "France"},
    "NA": {"country": "Netherlands"},
    "BB": {"country": "Belgium"},
    "IM": {"country": "Italy"},
    "SM": {"country": "Spain"},
    "PL": {"country": "Poland"},
    "LN": {"country": "United Kingdom"},
    "ID": {"country": "Ireland"},
    "CN": {"country": "Canada"},
    "CT": {"country": "Canada"},
    "AT": {"country": "Australia"},
    "US": {"country": "United States"},
}


def _ticker_to_locale(ticker: str) -> dict[str, str]:
    """Extract locale info from Bloomberg ticker exchange suffix."""
    parts = ticker.strip().split()
    if len(parts) >= 2:
        exchange = parts[-2].upper() if parts[-1].upper() == "EQUITY" else parts[-1].upper()
        if exchange in _LOCALE_MAP:
            return _LOCALE_MAP[exchange]
    return {"country": "Unknown"}


_THINKING = types.ThinkingConfig(thinking_level=THINKING_LEVEL)

_SEARCH_ONLY_CONFIG = types.GenerateContentConfig(
    thinking_config=_THINKING,
    tools=[types.Tool(google_search=types.GoogleSearch())],
)

_URL_READ_CONFIG = types.GenerateContentConfig(
    thinking_config=_THINKING,
    tools=[types.Tool(url_context=types.UrlContext())],
)

_URL_RE = re.compile(r'https?://[^\s<>"\')\]]+')

_SEC_HEADERS = {"User-Agent": "FinancialDataScan/1.0 research@financialdatascan.com", "Accept": "application/json"}
_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
}

_MIN_PDF_SIZE = 10_000
_VALID_CONTENT_TYPES = {"application/pdf", "text/html"}
_URL_JUNK_TAIL = re.compile(r'[`*\s]+$')


def _is_redirect_url(url: str) -> bool:
    return _REDIRECT_HOST in url


# Broad IR keywords — used to prevent homepage detection for shallow IR URLs
_IR_PATH_KEYWORDS = {
    "investor", "investors", "ir", "reports", "financial",
    "documents", "publications", "annual", "download", "downloads",
    "filings", "presentations", "governance",
}

# Stricter keywords — for last-resort landing page acceptance, require
# report-specific pages (not just /investor/ which is an IR hub)
_REPORT_PATH_KEYWORDS = {
    "reports", "financial", "documents", "publications", "annual",
    "download", "downloads", "filings", "presentations",
}


def _is_homepage_url(url: str) -> bool:
    """Check if a URL is a generic homepage (not a specific document/report page).

    Returns True for URLs like https://company.com/ or https://company.com
    that would lead the reader to classify based on marketing content.
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    # Root domain or empty path
    if not path or path == "":
        return True
    # Single shallow segment like /about, /contact, /news, or even /investor
    segments = [s for s in path.split("/") if s]
    if len(segments) <= 1 and not any(kw in path.lower() for kw in _REPORT_PATH_KEYWORDS):
        return True
    return False


def _is_ir_landing_page(url: str) -> bool:
    """Check if a URL looks like a reports/documents page (not just an IR hub).

    For last-resort fallback, we need pages that actually list report downloads,
    not generic IR pages like /investor/ that the reader can't classify from.
    """
    path = urlparse(url).path.lower()
    return any(kw in path for kw in _REPORT_PATH_KEYWORDS)


def _url_plausibly_belongs_to(url: str, company: Company) -> bool:
    """Check if a URL plausibly belongs to the given company.

    Prevents accepting PDFs from unrelated companies (e.g., H&M's report
    when searching for Lifco). Checks the domain and path against the
    company name and ticker.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    url_text = f"{host} {path}"

    # Extract candidate name tokens from the company name
    # e.g., "Lifco AB" -> ["lifco"], "AF Gruppen ASA" -> ["af", "gruppen"]
    skip_words = {"ab", "as", "asa", "plc", "inc", "ltd", "corp", "se", "sa", "ag", "nv", "oyj"}
    name_tokens = [
        w for w in company.name.lower().split()
        if w not in skip_words and len(w) >= 2
    ]

    # Ticker short (e.g., "LIFCO" from "LIFCO BS SS Equity")
    ticker_short = company.ticker.split()[0].lower() if company.ticker else ""

    # Check if any name token or the ticker appears in the domain or path
    for token in name_tokens:
        if token in url_text:
            return True
    if ticker_short and ticker_short in url_text:
        return True

    # Also accept neutral hosts (news aggregators, stock exchanges, CDNs, IR platforms)
    # where we can't tell from the domain alone
    neutral_hosts = {
        "nasdaq.com", "news.eu.nasdaq.com", "attachment.news.eu.nasdaq.com",
        "view.news.eu.nasdaq.com", "newsweb.oslobors.no", "cision.com",
        "news.cision.com", "mfn.se", "storage.mfn.se", "globenewswire.com",
        "cdn.prod.website-files.com", "live.euronext.com",
        "q4cdn.com",  # Q4 Inc IR hosting platform
        "annualreports.com",
    }
    for nh in neutral_hosts:
        if host == nh or host.endswith(f".{nh}"):
            return True

    return False


def _clean_url(url: str) -> str:
    """Strip trailing wildcards, whitespace, and other junk from URLs."""
    return _URL_JUNK_TAIL.sub("", url)


async def _find_sec_filing(company_name: str, target_year: int) -> str | None:
    """Use EDGAR EFTS API to find the correct 10-K/20-F URL for a US company.

    Tries quoted search first (exact match), then unquoted (broader) if
    no results — handles cases where the Excel name doesn't match EDGAR's
    registered entity name exactly (e.g. "3D Systems Corp" vs "3D SYSTEMS INC").
    """
    start = f"{target_year - 1}-06-01"
    end = f"{target_year + 1}-06-01"

    name_lower = company_name.lower().split()[0]

    # Try quoted first (precise), then unquoted (broader)
    queries = [
        quote(f'"{company_name}"'),
        quote(company_name),
    ]

    try:
        async with httpx.AsyncClient(headers=_SEC_HEADERS, timeout=15) as http:
            for query in queries:
                efts_url = (
                    f"https://efts.sec.gov/LATEST/search-index?"
                    f"q={query}&forms=10-K,20-F&dateRange=custom&startdt={start}&enddt={end}"
                )
                resp = await http.get(efts_url)
                if resp.status_code != 200:
                    continue

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
            if resp.status_code in (403, 405):
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


async def _get_content_type(url: str) -> str:
    """Get the content-type of a URL without downloading the full body."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, headers=_BROWSER_HEADERS, timeout=10
        ) as http:
            resp = await http.head(url)
            if resp.status_code in (403, 405):
                resp = await http.get(url, headers={**_BROWSER_HEADERS, "Range": "bytes=0-1024"})
            return resp.headers.get("content-type", "").split(";")[0].strip().lower()
    except Exception:
        return ""


def _build_prompt(company: Company) -> str:
    target_year = company.target_year
    locale = _ticker_to_locale(company.ticker)
    ticker_short = company.ticker.split()[0] if company.ticker else company.ticker
    return _INSTRUCTIONS.format(
        company_name=company.name,
        ticker_short=ticker_short,
        target_year=target_year,
        fallback_year_next=target_year + 1,
        fallback_year_prev=target_year - 1,
        country=locale["country"],
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


def _extract_real_urls(text: str) -> list[str]:
    """Extract URLs from text, filtering out Google redirect URLs."""
    raw = _URL_RE.findall(text)
    cleaned = [_clean_url(u) for u in raw]
    return list(dict.fromkeys(u for u in cleaned if not _is_redirect_url(u)))


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

    # Resolve up to 10 redirect URLs in parallel
    tasks = [_resolve_redirect(u) for u in redirect_urls[:10]]
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
        first_entry=company.first_entry,
        status=status,
        **kwargs,
    )


async def search_company(
    company: Company, client: genai.Client, semaphore: asyncio.Semaphore,
) -> SearchResult:
    """Search for a single company's annual report.

    SEC EDGAR for US companies, search -> navigate for others.
    """
    async with semaphore:
        total_in, total_out = 0, 0
        target_year = company.target_year

        # ── SEC EDGAR for US companies (direct, no AI) ─────────────
        for try_year in (target_year, target_year + 1, target_year - 1):
            sec_url = await _find_sec_filing(company.name, try_year)
            if sec_url:
                if try_year != target_year:
                    print(f"    [{company.name}] fallback: {target_year} not found on EDGAR, using {try_year}")
                result = _make_result(
                    company, status="found",
                    report_year=try_year,
                    source_url=sec_url,
                    source_type="sec_edgar",
                    source_rationale=f"Verified via SEC EDGAR EFTS API (year: {try_year})",
                    url_validated=True,
                )
                print(f"  [ok] {company.name} ({try_year}): sec_edgar -> {sec_url[:90]}")
                _save_result(company, result)
                return result

        # ── Search-only call (google_search, no url_context) ──────
        prompt = _build_prompt(company)
        search_queries: list[str] = []
        real_urls: list[str] = []

        max_retries = 5
        for attempt in range(max_retries):
            try:
                resp = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=prompt,
                        config=_SEARCH_ONLY_CONFIG,
                    ),
                    timeout=60,
                )
                in_tok, out_tok = extract_token_usage(resp)
                total_in += in_tok
                total_out += out_tok
                search_queries = _extract_search_queries(resp)

                # URLs come from grounding metadata (redirect URLs we must resolve)
                all_grounding = _extract_grounding_redirect_urls(resp)
                # Also check response text for any real URLs the model mentioned
                text_urls = _extract_real_urls(resp.text or "")

                if search_queries:
                    print(f"    [{company.name}] queries ({len(search_queries)}): {search_queries[:5]}"
                          f"{'...' if len(search_queries) > 5 else ''}")
                    if len(search_queries) > 3:
                        print(f"    [{company.name}] WARNING: model used {len(search_queries)} "
                              f"search queries (limit is 3)")

                # Resolve grounding redirect URLs to real destinations
                resolved_urls = await _resolve_grounding_redirects(resp)
                # Merge: text URLs first, then resolved grounding URLs
                real_urls = list(dict.fromkeys(text_urls + resolved_urls))

                print(f"    [{company.name}] {len(real_urls)} real URLs"
                      f" ({len(text_urls)} from text + {len(resolved_urls)} resolved"
                      f" from {len(all_grounding)} grounding)")
                if resolved_urls:
                    for u in resolved_urls[:3]:
                        print(f"      -> {u[:90]}")
                break
            except Exception as e:
                error_str = str(e) or type(e).__name__
                if is_retryable(error_str) and attempt < max_retries - 1:
                    wait = backoff(attempt)
                    if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                        # For quota/rate-limit we pause longer to let the service recover.
                        wait *= 3
                    print(
                        f"  [retry] {company.name}: {error_str[:80]}, "
                        f"attempt {attempt + 1}/{max_retries}, waiting {wait:.0f}s..."
                    )
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

        if not real_urls:
            result = _make_result(
                company,
                search_queries_used=search_queries,
                total_input_tokens=total_in,
                total_output_tokens=total_out,
                error="No URLs found from search",
            )
            print(f"  [not found] {company.name} ({target_year}): no real URLs in search results")
            _save_result(company, result)
            return result

        # ── Check for direct PDF hits, collect HTML pages for navigation ──
        landing_pages: list[str] = []
        for url in real_urls:
            valid, reason = await _validate_url(url)
            if valid:
                content_type = await _get_content_type(url)
                if content_type == "application/pdf":
                    if not _url_plausibly_belongs_to(url, company):
                        print(f"    [{company.name}] REJECTED PDF from unrelated domain: {url[:90]}")
                        continue
                    result = _make_result(
                        company, status="found",
                        report_year=target_year,
                        source_url=url,
                        source_type=_classify_source_type(url),
                        source_rationale="PDF found directly in search results",
                        url_validated=True,
                        search_queries_used=search_queries,
                        total_input_tokens=total_in,
                        total_output_tokens=total_out,
                    )
                    print(f"  [ok] {company.name} ({target_year}): {result.source_type} -> {url[:90]}")
                    _save_result(company, result)
                    return result
                else:
                    landing_pages.append(url)
                    print(f"    [html page] {url[:90]} -> will navigate")
            else:
                print(f"    [validate] {url[:90]} -> {reason}")

        if not landing_pages:
            # All URLs failed validation — try navigating them anyway
            landing_pages = real_urls

        # ── Navigate landing pages with url_context to find PDF links ──
        locale = _ticker_to_locale(company.ticker)
        nav_count = min(len(landing_pages), 5)
        print(f"    [{company.name}] navigating {nav_count} landing pages")
        first_valid_page = None

        for page_url in landing_pages[:5]:
            # Step 1: Enumerate all document links on the page
            enumerate_prompt = (
                f"<role>You are reading a company's web page to find an annual report download link.</role>\n"
                f"<context>\n"
                f"  <page_url>{page_url}</page_url>\n"
                f"  <company>{company.name}</company>\n"
                f"  <target_year>{target_year}</target_year>\n"
                f"  <fallback_years>{target_year + 1} or {target_year - 1}</fallback_years>\n"
                f"</context>\n"
                f"<task>\n"
                f"  1. Read this page carefully\n"
                f"  2. List ALL downloadable document links you find (PDFs, download buttons, document archive links)\n"
                f"  3. For each link, state: the URL, the document title/label, and the year if visible\n"
                f"  4. Then identify which link is the annual report for fiscal year {target_year} (or {target_year + 1} or {target_year - 1})\n"
                f"  5. Return ONLY that URL as the last line of your response\n"
                f"</task>\n"
                f"<hints>\n"
                f"  - The company is based in {locale['country']} — the annual report may be in the local language\n"
                f"  - Look for PDF icons, download sections, document libraries\n"
                f"  - The link might be in a table, list, or card layout\n"
                f"  - If this is a navigation page, look for a link to the reports/documents section\n"
                f"</hints>"
            )

            async def _try_navigate():
                """Navigate a page and return found PDF URL or None."""
                resp = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=enumerate_prompt,
                        config=_URL_READ_CONFIG,
                    ),
                    timeout=60,
                )
                nonlocal total_in, total_out
                in_tok, out_tok = extract_token_usage(resp)
                total_in += in_tok
                total_out += out_tok

                found_urls = _extract_real_urls(resp.text or "")
                found_urls = [u for u in found_urls if u != page_url]
                if found_urls:
                    print(f"    [navigate] {page_url[:60]}: found {len(found_urls)} URLs")
                    for u in found_urls[:3]:
                        print(f"      -> {u[:90]}")

                for pdf_url in found_urls:
                    if not _url_plausibly_belongs_to(pdf_url, company):
                        print(f"    [navigate] rejected unrelated URL: {pdf_url[:90]}")
                        continue
                    pdf_valid, pdf_reason = await _validate_url(pdf_url)
                    if pdf_valid:
                        return pdf_url
                    print(f"    [validate pdf] {pdf_url[:90]} -> {pdf_reason}")
                return None

            try:
                pdf_url = await _try_navigate()
                if pdf_url:
                    result = _make_result(
                        company, status="found",
                        report_year=target_year,
                        source_url=pdf_url,
                        source_type=_classify_source_type(pdf_url),
                        source_rationale="Found via url_context page navigation",
                        url_validated=True,
                        search_queries_used=search_queries,
                        total_input_tokens=total_in,
                        total_output_tokens=total_out,
                    )
                    print(f"  [ok] {company.name} ({target_year}): {result.source_type} -> {pdf_url[:90]}")
                    _save_result(company, result)
                    return result

                if first_valid_page is None:
                    page_valid, _ = await _validate_url(page_url)
                    if page_valid:
                        first_valid_page = page_url

                print(f"    [navigate] {page_url[:60]}: no valid PDF link found")
            except Exception as e:
                error_str = str(e) or type(e).__name__
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    # Rate limit is often transient; retry the url_context navigation a few times.
                    max_nav_retries = 5
                    for nav_attempt in range(max_nav_retries):
                        wait = backoff(nav_attempt) * 3
                        print(
                            f"    [navigate rate-limit] {page_url[:60]}: waiting {wait:.0f}s... "
                            f"(attempt {nav_attempt + 1}/{max_nav_retries})"
                        )
                        await asyncio.sleep(wait)
                        try:
                            pdf_url = await _try_navigate()
                            if pdf_url:
                                result = _make_result(
                                    company,
                                    status="found",
                                    report_year=target_year,
                                    source_url=pdf_url,
                                    source_type=_classify_source_type(pdf_url),
                                    source_rationale="Found via url_context page navigation (retry)",
                                    url_validated=True,
                                    search_queries_used=search_queries,
                                    total_input_tokens=total_in,
                                    total_output_tokens=total_out,
                                )
                                print(
                                    f"  [ok] {company.name} ({target_year}): {result.source_type} -> {pdf_url[:90]}"
                                )
                                _save_result(company, result)
                                return result
                            print(f"    [navigate] {page_url[:60]}: no valid PDF found on retry attempt")
                            break
                        except Exception as e2:
                            # Keep looping until attempt budget is exhausted.
                            last_err = str(e2) or type(e2).__name__
                            if nav_attempt == max_nav_retries - 1:
                                print(f"    [navigate error] {page_url[:60]}: retries failed: {last_err[:80]}")
                            continue
                    continue
                print(f"    [navigate error] {page_url[:60]}: {error_str[:80]}")
                continue

        # ── Last resort: return landing page only if it's an IR/report page ─────
        if first_valid_page and not _is_homepage_url(first_valid_page) and _is_ir_landing_page(first_valid_page):
            result = _make_result(
                company, status="found",
                report_year=target_year,
                source_url=first_valid_page,
                source_type=_classify_source_type(first_valid_page),
                source_rationale="IR landing page (no direct PDF link found)",
                url_validated=True,
                search_queries_used=search_queries,
                total_input_tokens=total_in,
                total_output_tokens=total_out,
            )
            print(f"  [ok] {company.name} ({target_year}): IR landing page -> {first_valid_page[:90]}")
            _save_result(company, result)
            return result
        elif first_valid_page:
            print(f"    [{company.name}] rejecting non-IR page as result: {first_valid_page[:90]}")

        result = _make_result(
            company,
            search_queries_used=search_queries,
            total_input_tokens=total_in,
            total_output_tokens=total_out,
            error=f"No valid URL found ({len(landing_pages)} landing pages navigated)",
        )
        print(f"  [not found] {company.name} ({target_year}): no valid URLs found")
        _save_result(company, result)
        return result


def _save_result(company: Company, result: SearchResult) -> None:
    output_path = _result_path(company)
    # Ensure Unicode is always safely persisted on Windows consoles/codepages.
    output_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")


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
            print(f"  [skip] {company.name} ({company.target_year}) (already searched)")
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
