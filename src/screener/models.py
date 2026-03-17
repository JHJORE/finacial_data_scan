import re
from typing import Literal

from pydantic import BaseModel, Field


class Company(BaseModel):
    name: str
    ticker: str
    slug: str

    @classmethod
    def from_row(cls, name: str, ticker: str) -> "Company":
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80]
        return cls(name=name, ticker=ticker, slug=slug)


# --- Structured output schemas (sent to Gemini as response_json_schema) ---


class ResearchResponse(BaseModel):
    """Schema for the Gemini research stage structured output.

    Source URLs are NOT included here — they come from grounding metadata,
    which provides verified URLs from Google Search rather than model-generated ones.
    """

    annual_report_found: bool = Field(
        description="Whether an annual report or equivalent filing was found"
    )
    report_year: int | None = Field(
        default=None, description="Year of the annual report found"
    )
    extracted_text: str = Field(
        default="",
        description="Verbatim strategy/M&A excerpts from the annual report",
    )
    company_description: str = Field(
        default="",
        description="Brief 1-sentence description of what the company does",
    )


class ClassificationResponse(BaseModel):
    """Schema for the Gemini classification stage structured output."""

    is_programmatic: bool = Field(
        description="Whether the company is a programmatic acquirer"
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="Confidence level of the classification"
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Direct quotes from the annual report supporting the verdict",
    )
    reasoning: str = Field(
        default="", description="1-3 sentence explanation of the verdict"
    )


# --- Storage models (persisted to disk as JSON) ---


class GroundingSource(BaseModel):
    title: str = ""
    url: str = ""


class ResearchResult(BaseModel):
    company_name: str
    ticker: str
    slug: str
    annual_report_found: bool = False
    report_year: int | None = None
    source_urls: list[str] = []
    sources: list[GroundingSource] = []
    extracted_text: str = ""
    company_description: str = ""
    search_queries_used: list[str] = []
    error: str | None = None


class Classification(BaseModel):
    company_name: str
    ticker: str
    slug: str
    year: int | None = None
    is_programmatic: bool = False
    confidence: str = "low"
    evidence: list[str] = []
    reasoning: str = ""
    error: str | None = None
