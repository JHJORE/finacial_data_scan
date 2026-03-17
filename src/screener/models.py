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


# --- Search agent schemas ---


class SearchResponse(BaseModel):
    """Structured output schema for the search agent."""

    status: Literal["found", "not_found", "not_applicable"] = Field(
        description=(
            "'found' if an annual report was located, "
            "'not_applicable' if the company was acquired/merged/delisted and no longer files independently, "
            "'not_found' if no report could be located"
        )
    )
    report_year: int | None = Field(
        default=None, description="Fiscal year the report covers"
    )
    source_url: str = Field(
        default="",
        description="The direct URL to the annual report filing page or PDF",
    )
    source_type: str = Field(
        default="",
        description="Type of source: investor_relations, sec_edgar, stock_exchange, regulatory_filing, other",
    )
    source_rationale: str = Field(
        default="",
        description="Why this source was chosen as the primary source",
    )


class SearchResult(BaseModel):
    """Persisted to data/search/{slug}.json."""

    company_name: str
    ticker: str
    slug: str
    status: Literal["found", "not_found", "not_applicable"] = "not_found"
    report_year: int | None = None
    source_url: str = ""
    source_type: str = ""
    source_rationale: str = ""
    search_queries_used: list[str] = []
    error: str | None = None


# --- Reader agent schemas ---


class ReaderResponse(BaseModel):
    """Structured output schema for the reader agent."""

    # Quantitative pre-qualification
    acquisitions_mentioned: int = Field(
        default=0, description="Number of acquisitions identifiable from the report"
    )
    meets_quantitative_threshold: bool = Field(
        default=False,
        description="At least 5 acquisitions in 36 months AND at least 1 in last 12 months",
    )

    # Qualitative checklist
    core_growth_driver: bool = Field(
        default=False,
        description="Acquisitions/inorganic growth described as a core growth driver",
    )
    stated_programme: bool = Field(
        default=False,
        description="A stated acquisition model, programme, or pipeline exists",
    )
    repeated_references: bool = Field(
        default=False,
        description="Repeated references to acquisitions as integral to strategy",
    )
    clear_processes: bool = Field(
        default=False,
        description="Clear routines/processes for sourcing, evaluating, or integrating targets",
    )
    decentralized_model: bool = Field(
        default=False,
        description="Decentralized model where acquired companies keep autonomy",
    )
    quantitative_goals: bool = Field(
        default=False,
        description="Quantitative M&A goals (number per year, % of growth from M&A)",
    )

    # Disqualifiers
    only_high_deal_count: bool = Field(
        default=False,
        description="Evidence is limited to high deal count without programme language",
    )
    only_opportunistic: bool = Field(
        default=False,
        description="Only generic wording like 'we consider acquisitions opportunistically'",
    )
    only_single_deal: bool = Field(
        default=False, description="Only mentions one specific deal"
    )

    # Extracted evidence (stored separately for reproducibility / ex-ante audit)
    extracted_text: str = Field(
        default="",
        description="ALL verbatim M&A strategy excerpts from the annual report",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Specific quotes supporting each positive criterion marked true",
    )

    # Verdict
    is_programmatic: bool = Field(
        default=False, description="Final classification: is this a programmatic acquirer?"
    )
    confidence: Literal["high", "medium", "low"] = Field(
        default="low", description="Confidence level of the classification"
    )
    reasoning: str = Field(
        default="",
        description="1-3 sentence explanation referencing which criteria were met",
    )
    company_description: str = Field(
        default="", description="1-sentence description of what the company does"
    )


class ReaderResult(BaseModel):
    """Persisted to data/results/{slug}.json.

    The extracted_text field stores the raw evidence separately from the
    verdict, so the ex-ante constraint is verifiable — anyone can inspect
    exactly what text the classification was based on.
    """

    company_name: str
    ticker: str
    slug: str
    year: int | None = None
    source_url: str = ""
    source_type: str = ""
    # Quantitative
    acquisitions_mentioned: int = 0
    meets_quantitative_threshold: bool = False
    # Qualitative checklist
    core_growth_driver: bool = False
    stated_programme: bool = False
    repeated_references: bool = False
    clear_processes: bool = False
    decentralized_model: bool = False
    quantitative_goals: bool = False
    # Disqualifiers
    only_high_deal_count: bool = False
    only_opportunistic: bool = False
    only_single_deal: bool = False
    # Evidence
    extracted_text: str = ""
    evidence: list[str] = []
    # Verdict
    is_programmatic: bool = False
    confidence: str = "low"
    reasoning: str = ""
    company_description: str = ""
    # URL context verification
    url_retrieval_status: str = ""
    document_token_count: int = 0
    error: str | None = None
