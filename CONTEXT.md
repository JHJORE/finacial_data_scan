# Programmatic Acquirer Screening Pipeline

## Project Purpose

Master thesis tool: classify ~1,600 listed companies as **programmatic acquirers** using AI-assisted screening of annual reports.

A **programmatic acquirer** has an explicit, recurring acquisition programme as a core part of its business model. This is distinct from simply having done many acquisitions. The classification must be **ex ante** — only information publicly available at the time (primarily annual reports) may be used. No hindsight and no use of later reports to classify earlier years.

**Reproducibility is critical**: the extracted evidence (verbatim quotes from the annual report) is stored separately from the verdict, so the ex-ante constraint is verifiable — anyone can inspect exactly what text the classification was based on.

### Quantitative pre-qualification (minimum criteria):
- At least 5 acquisitions in the last 36 months to enter
- At least 1 acquisition in the last 12 months to stay in
- If a company falls out, it must re-qualify again
- These thresholds are evaluated relative to the annual report year

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

Two-agent pipeline using **Gemini 3 Flash** (`gemini-3-flash-preview`) via Google's `google-genai` SDK (standard Gemini AI Studio API, not Vertex AI):

```
[data/companies.xlsx]           (input: acquirer + ticker columns)
    ↓
Agent 1: SEARCH                 (two-step: Google Search grounding → structured output)
    → Find best single source URL for the annual report
    → Output: data/search/{slug}.json
    ↓
Agent 2: READER                 (url_context + structured output)
    → Read the source, extract M&A evidence
    → Apply quantitative thresholds + qualitative checklist
    → Output: data/results/{slug}.json
    ↓
ASSEMBLE
    → data/output/matrix.csv
```

### Why the search agent uses two API calls:

Gemini cannot combine Google Search grounding with structured JSON output in the same request. The search agent works around this with two calls per company:

1. **Grounding call** — uses `google_search` tool, no structured output. Gemini runs real web searches and returns free-text findings.
2. **Structure call** — uses `response_json_schema`, no grounding. Takes the free-text search results and formats them into the `SearchResponse` JSON schema.

The `source_url` comes from the model's structured output (not from grounding metadata). The search prompt prioritises SEC EDGAR for US-listed companies.

### Why two agents:
1. **Search agent** focuses only on finding the best source — source quality is critical
2. **Reader agent** reads the actual document and classifies — extraction and classification in one pass, but evidence stored separately from verdict for reproducibility
3. Non-classified companies (no source found, errors) are kept in the output with appropriate flags

### Parallel streaming pipeline:

`pilot.py` runs all companies in parallel. Each company goes through search → reader back-to-back as a single pipeline — as soon as a search completes, the reader starts immediately for that company. Concurrency is controlled by a shared `asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)`.

### Agent instructions:
Each agent has a dedicated markdown instruction file in `src/screener/agents/`:
- `search.md` — instructions for source discovery and selection
- `reader.md` — instructions for reading, extraction, and classification

## Tech Stack

- **Python 3.11+** managed with `uv`
- **google-genai** — Gemini API client (standard API with Google Search grounding)
- **pydantic** — data models (Company, SearchResponse, SearchResult, ReaderResponse, ReaderResult)
- **openpyxl** — read company list from Excel
- **pandas** — assemble output matrix

## Key Files

| File | Purpose |
|---|---|
| `src/screener/config.py` | Paths, API keys, model settings, rate limits, client factory |
| `src/screener/models.py` | Pydantic models: Company, SearchResponse, SearchResult, ReaderResponse, ReaderResult |
| `src/screener/agents/search.md` | Search agent instructions |
| `src/screener/agents/reader.md` | Reader agent instructions |
| `src/screener/companies.py` | Load company list from Excel |
| `src/screener/search.py` | Agent 1: two-step Google Search grounding → structured output |
| `src/screener/reader.py` | Agent 2: url_context → read source, extract, classify |
| `src/screener/assemble.py` | Build company × year matrix |
| `scripts/pilot.py` | Streaming parallel pipeline (search → read per company) |
| `scripts/run_search.py` | Batch Agent 1 run (search only) |
| `scripts/run_reader.py` | Batch Agent 2 run + matrix assembly |
| `scripts/run_assemble.py` | Matrix assembly only (no API calls) |
| `scripts/validate.py` | Random sample for manual review |

## Data Flow & Storage

Every company gets JSON files with full audit trail:

**Search JSON** (`data/search/{slug}.json`):
- `source_url` — the single best source URL (from model output, not grounding metadata)
- `source_type` — investor_relations, sec_edgar, stock_exchange, regulatory_filing, other
- `source_rationale` — why this source was chosen
- `search_queries_used` — what Gemini searched for (from grounding metadata)

**Reader JSON** (`data/results/{slug}.json`):
- `extracted_text` — verbatim M&A strategy excerpts (raw evidence, stored separately)
- `acquisitions_mentioned` — number of acquisitions found
- `meets_quantitative_threshold` — passes minimum criteria
- Checklist booleans: `core_growth_driver`, `stated_programme`, `repeated_references`, `clear_processes`, `decentralized_model`, `quantitative_goals`
- Disqualifiers: `only_high_deal_count`, `only_opportunistic`, `only_single_deal`
- `is_programmatic` — true/false verdict
- `confidence` — high/medium/low
- `evidence` — specific quotes per criterion
- `reasoning` — 1-3 sentence explanation
- `company_description` — 1-sentence description of what the company does

## Input Format

Excel file at `data/companies.xlsx` with columns:
- `acquirer` — company name (e.g., "Constellation Software Inc")
- `ticker` — Bloomberg ticker (e.g., "CSU CN Equity")

The Bloomberg exchange suffix (CN = Canada, GR = Germany, SS = Stockholm, US = US) helps Gemini search in the right country/language.

## Cost & Rate Limits

- Google Search grounding: **5,000 prompts/month free** (Gemini 3), then $14/1,000 search queries
- Gemini Flash tokens: $0.25/MTok input, $1.50/MTok output
- Each company requires **3 API calls**: search grounding + search structuring + reader
- `MAX_CONCURRENT_REQUESTS = 5` — up to 5 companies processed in parallel
- Both agents retry up to 3 times with exponential backoff (20s, 40s, 80s) on 429/RESOURCE_EXHAUSTED errors

## Running the Pipeline

```bash
# 1. Set up
cp .env.example .env  # add GOOGLE_API_KEY (from aistudio.google.com)

# 2. Pilot (default 50 companies, or pass a number)
uv run python scripts/pilot.py       # all 50
uv run python scripts/pilot.py 5     # just 5

# 3. Full run (two separate scripts, or use pilot.py)
uv run python scripts/run_search.py     # Agent 1: find sources
uv run python scripts/run_reader.py     # Agent 2: read + classify + matrix
```

## Design Decisions

- **Standard Gemini AI Studio API** (not Vertex AI): simpler auth, single API key
- **Two-step search**: grounding and structured output cannot be combined in Gemini, so the search agent makes two calls per company
- **Two separate agents**: search agent finds sources, reader agent reads and classifies — clear separation of concerns
- **Agent instructions in markdown**: easier to iterate on prompts than Python string templates
- **url_context tool**: reader agent reads the actual source document, not just search snippets
- **Structured output with checklist**: explicit boolean per criterion, not just a single verdict
- **Skip-existing**: all scripts skip companies that already have results, enabling safe resume
- **Async with semaphore**: `asyncio.gather` for true parallelism, semaphore for concurrency control, no artificial per-request sleep
