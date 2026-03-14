# Programmatic Acquirer Screening Pipeline

## Project Purpose

Master thesis tool: classify ~1,600 listed companies as **programmatic acquirers** using AI-assisted screening of annual reports.

A **programmatic acquirer** has an explicit, recurring acquisition programme as a core part of its business model. This is distinct from simply having done many acquisitions. The classification must be **ex ante** — only information publicly available at the time (primarily annual reports) may be used.

### What qualifies as programmatic:
- Acquisitions/inorganic growth described as a **core growth driver**
- A stated acquisition model, programme, or pipeline
- Repeated references to acquisitions as integral to strategy
- Clear routines/processes for sourcing, evaluating, or integrating targets
- Often a decentralized model where acquired companies keep autonomy
- Quantitative M&A goals (e.g., number of acquisitions per year)

### What does NOT qualify:
- High deal count alone (activity ≠ programme)
- Generic "we consider acquisitions opportunistically"
- Mention of one specific deal only
- Being PE-backed doing bolt-ons

### Reference companies (known positives):
Constellation Software, Lifco, Danaher, Roper Technologies, TransDigm, Addtech, Halma, Judges Scientific, SDiptech, Vitec Software

## Architecture

Two-stage pipeline, all using **Gemini 3 Flash** via Google's `google-genai` SDK:

```
[data/companies.xlsx]           (input: acquirer + ticker columns)
    ↓
Stage 1: RESEARCH               (Gemini Flash + Google Search grounding)
    → Find annual report, extract strategy/M&A sections
    → Output: data/research/{slug}.json (one per company)
    ↓
Stage 2: CLASSIFY                (Gemini Flash, no search)
    → Apply classification criteria to stored evidence
    → Output: data/classifications/{slug}.json (one per company)
    ↓
Stage 3: ASSEMBLE
    → Aggregate into data/output/matrix.csv
```

### Why two stages:
1. Re-run classification with different prompts without re-searching (saves cost)
2. Raw evidence stored for thesis audit/reproducibility
3. Ex ante constraint is verifiable — you can inspect what evidence was used

## Tech Stack

- **Python 3.11+** managed with `uv`
- **google-genai** — Gemini API client with Google Search grounding
- **pydantic** — data models (Company, ResearchResult, Classification)
- **openpyxl** — read company list from Excel
- **pandas** — assemble output matrix

## Key Files

| File | Purpose |
|---|---|
| `src/screener/config.py` | Paths, API keys, model settings, rate limits |
| `src/screener/models.py` | Pydantic models: Company, ResearchResult, Classification |
| `src/screener/prompts.py` | Research + classification prompt templates |
| `src/screener/companies.py` | Load company list from Excel |
| `src/screener/research.py` | Stage 1: Gemini + Google Search → find annual reports |
| `src/screener/classify.py` | Stage 2: Gemini → classify from stored evidence |
| `src/screener/assemble.py` | Stage 3: build company × year matrix |
| `scripts/pilot.py` | 50-company test run with known controls |
| `scripts/run_research.py` | Full Stage 1 run |
| `scripts/run_classify.py` | Full Stage 2 run + matrix assembly |
| `scripts/validate.py` | Random sample for manual QA |

## Data Flow & Storage

Every company gets a JSON file with full audit trail:

**Research JSON** (`data/research/{slug}.json`):
- `source_urls` — URLs where information was found
- `search_queries_used` — what Gemini searched for (from groundingMetadata)
- `grounding_chunks` — structured citations with URIs and titles
- `extracted_text` — verbatim quotes from annual report
- `raw_response` — full API response for debugging

**Classification JSON** (`data/classifications/{slug}.json`):
- `is_programmatic` — true/false verdict
- `confidence` — high/medium/low
- `evidence` — direct quotes supporting the verdict
- `reasoning` — 1-3 sentence explanation

## Input Format

Excel file at `data/companies.xlsx` with columns:
- `acquirer` — company name (e.g., "Constellation Software Inc")
- `ticker` — Bloomberg ticker (e.g., "CSU CN Equity")

The Bloomberg exchange suffix (CN = Canada, GR = Germany, SS = Stockholm, US = US) helps Gemini search in the right country/language.

## Cost & Rate Limits

- Google Search grounding: **1,500 batch requests/day free**, then $14/1,000
- Gemini Flash tokens (batch): $0.25/MTok input, $1.50/MTok output
- Estimated total for 1,600 companies (single year): **$13-35**
- Spread research over 2 days to stay within free search tier

## Running the Pipeline

```bash
# 1. Set up
cp .env.example .env  # add GOOGLE_API_KEY

# 2. Pilot (50 companies, <$3)
uv run python scripts/pilot.py

# 3. Full run
uv run python scripts/run_research.py    # Stage 1
uv run python scripts/run_classify.py    # Stage 2 + matrix

# 4. Validate
uv run python scripts/validate.py 20     # review 20 random results
```

## Design Decisions

- **All-Gemini** chosen over mixed Claude+Gemini approach: Google Search is likely better at finding financial documents than Brave Search (which Claude uses), and Gemini Flash is much cheaper
- **Agentic search**: Gemini decides its own search queries based on the prompt — no manual query construction needed
- **Skip-existing**: All scripts skip companies that already have results, enabling safe resume after interruptions
- **Async with rate limiting**: Research uses asyncio with semaphore for concurrent but rate-limited API calls
