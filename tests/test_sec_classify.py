"""Tests for SEC filing parsing, downloading, and classification.

Unit tests (test_parse_*) require no network or credentials.
Network tests (test_download_*) require internet access.
Integration tests (test_classify_*) require Vertex AI credentials.
"""

import re

import pytest

from screener.reader import _parse_sec_html, _download_sec_filing


# ── Unit tests: HTML parsing ─────────────────────────────────


def test_parse_sec_html_removes_scripts():
    """Script and style content is fully removed, not just the tags."""
    html = """
    <html>
    <head><title>Test</title></head>
    <body>
        <script>var x = 1; function foo() { return x; }</script>
        <style>.hidden { display: none; } @media print { body { font-size: 12pt; } }</style>
        <noscript>Enable JavaScript</noscript>
        <p>Annual Report for FY2023</p>
        <p>Revenue was $10 billion.</p>
    </body>
    </html>
    """
    text = _parse_sec_html(html)

    # Script/style content must be gone
    assert "var x" not in text
    assert "function foo" not in text
    assert "display: none" not in text
    assert "@media" not in text
    assert "Enable JavaScript" not in text

    # Actual content must remain
    assert "Annual Report for FY2023" in text
    assert "Revenue was $10 billion" in text


def test_parse_sec_html_preserves_tables():
    """Table data appears as pipe-separated readable text."""
    html = """
    <html><body>
        <table>
            <tr><th>Year</th><th>Acquisitions</th><th>Revenue</th></tr>
            <tr><td>2023</td><td>25</td><td>$64.1B</td></tr>
            <tr><td>2022</td><td>30</td><td>$61.6B</td></tr>
        </table>
    </body></html>
    """
    text = _parse_sec_html(html)

    # Table structure preserved as pipe-separated values
    assert "Year | Acquisitions | Revenue" in text
    assert "2023 | 25 | $64.1B" in text
    assert "2022 | 30 | $61.6B" in text


def test_parse_sec_html_decodes_entities():
    """HTML entities are properly decoded."""
    html = """
    <html><body>
        <p>Mergers &amp; Acquisitions</p>
        <p>Revenue &gt; $50B</p>
        <p>Total&nbsp;Assets</p>
    </body></html>
    """
    text = _parse_sec_html(html)

    assert "Mergers & Acquisitions" in text
    assert "Revenue > $50B" in text
    # &nbsp; should become a regular space (or be normalized)
    assert "&amp;" not in text
    assert "&gt;" not in text
    assert "&nbsp;" not in text


# ── Network test: Accenture filing download ──────────────────


async def test_download_accenture_filing():
    """Download Accenture 10-K and verify it's clean and within size bounds."""
    url = "https://www.sec.gov/Archives/edgar/data/1467373/000146737323000324/acn-20230831.htm"

    content, fmt = await _download_sec_filing(url)

    # Format should be html (no PDF on EDGAR for this filing)
    assert fmt == "html", f"Expected html format, got {fmt}"
    assert isinstance(content, str)

    # Must be substantial (not an empty/failed download)
    assert len(content) > 50_000, f"Text too short: {len(content):,} chars"

    # No script/style artifacts — these prove BeautifulSoup decompose works
    assert "<script" not in content.lower(), "Raw <script> tag found in output"
    assert "<style" not in content.lower(), "Raw <style> tag found in output"
    # Verify JS code from <script> blocks is NOT leaking into text
    # (the old regex approach would leave this behind)
    assert "function(" not in content, "JavaScript function leaked into text"

    # Contains expected company content
    assert "Accenture" in content

    # No raw HTML tags should remain
    html_tags = re.findall(r"<[a-zA-Z/][^>]*>", content)
    assert len(html_tags) == 0, f"Found {len(html_tags)} HTML tags: {html_tags[:5]}"

    print(f"\n  Accenture filing: {len(content):,} chars after parsing")


# ── Integration test: Accenture classification ───────────────


@pytest.mark.integration
async def test_classify_accenture(gemini_client, semaphore, acn_search_result, tmp_path):
    """Full pipeline: download SEC filing → parse → classify with Gemini 3 Flash.

    Uses gemini-3-flash-preview with medium thinking.
    Accenture is a known programmatic acquirer with 25+ acquisitions/year.
    """
    from screener import config
    from screener.reader import read_company

    # Point results directory to tmp_path so _save_result doesn't fail
    config.RESULTS_DIR = tmp_path / "results"
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    result = await read_company(acn_search_result, gemini_client, semaphore)

    print(f"\n  Company: {result.company_name}")
    print(f"  Programmatic: {result.is_programmatic}")
    print(f"  Confidence: {result.confidence}")
    print(f"  Acquisitions: {result.acquisitions_mentioned}")
    print(f"  Reasoning: {result.reasoning}")
    print(f"  Error: {result.error}")

    # Accenture should be classified as programmatic
    assert result.error is None, f"Classification failed with error: {result.error}"
    assert result.is_programmatic is True, (
        f"Accenture should be programmatic but got is_programmatic={result.is_programmatic}, "
        f"reasoning: {result.reasoning}"
    )
    assert result.confidence in ("high", "medium"), (
        f"Expected high/medium confidence, got {result.confidence}"
    )
    assert result.acquisitions_mentioned >= 5, (
        f"Expected >=5 acquisitions, got {result.acquisitions_mentioned}"
    )
    assert result.meets_quantitative_threshold is True
    assert result.core_growth_driver is True
    assert len(result.evidence) > 0, "Expected evidence quotes"
    assert len(result.extracted_text) > 0, "Expected extracted text"
