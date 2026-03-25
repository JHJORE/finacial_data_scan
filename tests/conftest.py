"""Shared fixtures for the screener test suite."""

import asyncio
import os

import pytest

from screener.config import create_gemini_client
from screener.models import SearchResult


@pytest.fixture(scope="session")
def gemini_client():
    """Session-scoped Gemini client. Skips if no GCP credentials."""
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        pytest.skip("GOOGLE_CLOUD_PROJECT not set — skipping integration test")
    return create_gemini_client()


@pytest.fixture
def semaphore():
    return asyncio.Semaphore(1)


@pytest.fixture
def acn_search_result():
    """SearchResult for Accenture PLC with a known SEC EDGAR filing URL."""
    return SearchResult(
        company_name="Accenture PLC",
        ticker="ACN US Equity",
        slug="accenture-plc-2023",
        first_entry="2024-02-01",
        status="found",
        report_year=2023,
        source_url="https://www.sec.gov/Archives/edgar/data/1467373/000146737323000324/acn-20230831.htm",
        source_type="sec_edgar",
    )
