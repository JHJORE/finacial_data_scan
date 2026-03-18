import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

class Company(BaseModel):
    name: str
    ticker: str
    slug: str

    @classmethod
    def from_row(cls, name: str, ticker: str) -> "Company":
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80]
        return cls(name=name, ticker=ticker, slug=slug)


# --- Search agent schemas ---


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
    url_validated: bool = False
    search_queries_used: list[str] = []
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    error: str | None = None


# --- Reader agent schemas ---


class ReaderResponse(BaseModel):
    """Structured output schema for the reader/classify agent."""

    # Quantitative pre-qualification
    acquisitions_mentioned: int = Field(
        description="Number of distinct acquisitions identifiable from the report text"
    )
    meets_quantitative_threshold: bool = Field(
        description="True only if >=5 acquisitions in last 36 months AND >=1 in last 12 months"
    )

    # Qualitative checklist — each must be set explicitly
    core_growth_driver: bool = Field(
        description="Acquisitions/inorganic growth described as a core growth driver"
    )
    stated_programme: bool = Field(
        description="A stated acquisition model, programme, or pipeline exists"
    )
    repeated_references: bool = Field(
        description="Repeated references to acquisitions as integral to strategy"
    )
    clear_processes: bool = Field(
        description="Clear routines/processes for sourcing, evaluating, or integrating targets"
    )
    decentralized_model: bool = Field(
        description="Decentralized model where acquired companies keep autonomy"
    )
    quantitative_goals: bool = Field(
        description="Quantitative M&A goals (number per year, percentage of growth from M&A)"
    )

    # Disqualifiers
    only_high_deal_count: bool = Field(
        description="Evidence is limited to high deal count without programme language"
    )
    only_opportunistic: bool = Field(
        description="Only generic wording like 'we consider acquisitions opportunistically'"
    )
    only_single_deal: bool = Field(
        description="Only mentions one specific deal"
    )

    extracted_text: str = Field(
        description="ALL verbatim M&A strategy excerpts from the annual report"
    )
    evidence: list[str] = Field(
        description="Specific quotes supporting each positive criterion marked true"
    )

    # Verdict
    is_programmatic: bool = Field(
        description="Final classification: is this a programmatic acquirer?"
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="Confidence level of the classification"
    )
    reasoning: str = Field(
        description="1-3 sentence explanation referencing which criteria were met"
    )
    company_description: str = Field(
        description="1-sentence description of what the company does"
    )

    @model_validator(mode="after")
    def _validate_programmatic(self):
        if not self.is_programmatic:
            return self
        if not self.reasoning:
            raise ValueError("reasoning is required when is_programmatic is true")
        if not self.evidence:
            raise ValueError("evidence list is required when is_programmatic is true")
        return self


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
    # Token usage across all API calls (fetch + classify)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    error: str | None = None
