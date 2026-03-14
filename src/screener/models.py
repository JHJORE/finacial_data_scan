from pydantic import BaseModel
import re


class Company(BaseModel):
    name: str
    ticker: str
    slug: str

    @classmethod
    def from_row(cls, name: str, ticker: str) -> "Company":
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80]
        return cls(name=name, ticker=ticker, slug=slug)


class ResearchResult(BaseModel):
    company_name: str
    ticker: str
    slug: str
    year: int | None = None
    annual_report_found: bool = False
    report_year: int | None = None
    source_urls: list[str] = []
    extracted_text: str = ""
    company_description: str = ""
    search_queries_used: list[str] = []
    grounding_chunks: list[dict] = []
    raw_response: dict = {}
    error: str | None = None


class Classification(BaseModel):
    company_name: str
    ticker: str
    slug: str
    year: int | None = None
    is_programmatic: bool = False
    confidence: str = "low"  # high, medium, low
    evidence: list[str] = []
    reasoning: str = ""
    error: str | None = None
