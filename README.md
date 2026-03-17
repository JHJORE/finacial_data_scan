# Programmatic Acquirer Screener

Screens a list of companies to identify **programmatic acquirers** — companies with an explicit, recurring acquisition programme as a core part of their business model.

The pipeline uses the Gemini API to:

1. **Search** — Find each company's most recent annual report (10-K, 20-F, etc.) via Google Search grounding
2. **Read** — Read the report and classify the company against a quantitative + qualitative checklist
3. **Assemble** — Combine all results into a single CSV matrix

Each run is saved in a timestamped folder under `data/runs/`, so previous results are never overwritten.

## Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- A Google AI Studio API key ([get one here](https://aistudio.google.com/apikey))

### Install

```bash
git clone <repo-url>
cd finacial_data_scan

# Install dependencies
uv sync
```

### Configure

Copy the example env file and add your API key:

```bash
cp .env.example .env
```

Edit `.env` and paste your key:

```
GOOGLE_API_KEY=your-actual-api-key
```

### Prepare input

Place your company list as an Excel file at `data/companies.xlsx`. It must have two columns:

| acquirer | ticker |
|---|---|
| STERIS Corp | 1339679D US Equity |
| Johnson Controls Inc | 1436513D US Equity |

## Usage

Run the full pipeline for all companies:

```bash
uv run python main.py
```

Run on a random sample (useful for testing):

```bash
uv run python main.py --sample 50
```

### Step-by-step mode

You can also run each stage separately:

```bash
# 1. Search for annual reports (creates a new timestamped run)
uv run python main.py search

# 2. Read and classify (continues the latest run)
uv run python main.py read

# 3. Re-assemble the matrix without re-running API calls
uv run python main.py assemble
```

### Validate results

Print a random sample of results for manual review:

```bash
uv run python main.py validate
uv run python main.py validate --n 20
```

## Output

Each run creates a timestamped folder:

```
data/runs/
├── 2026-03-17T14-30-00/
│   ├── search/         ← one JSON per company (report URL, status)
│   ├── results/        ← one JSON per company (classification, evidence)
│   └── output/
│       └── matrix.csv  ← final screening matrix
└── latest              ← symlink to most recent run
```

The `matrix.csv` contains one row per company with columns for:

- Company info (name, ticker, description)
- Source (year, URL, type)
- Quantitative check (acquisition count, threshold met)
- Qualitative checklist (6 positive criteria, 3 disqualifiers)
- Verdict (is_programmatic, confidence, reasoning, evidence)

## Project structure

```
├── main.py                  ← entry point
├── pyproject.toml
├── .env.example
├── data/
│   ├── companies.xlsx       ← input
│   └── runs/                ← all run outputs (gitignored)
└── src/screener/
    ├── config.py            ← paths, API config, run management
    ├── companies.py         ← Excel loader
    ├── models.py            ← Pydantic schemas
    ├── search.py            ← Agent 1: find annual report URLs
    ├── reader.py            ← Agent 2: read + classify
    ├── assemble.py          ← build CSV matrix
    └── agents/
        ├── search.md        ← search agent prompt
        └── reader.md        ← reader agent prompt
```
