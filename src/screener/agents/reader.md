Read the annual report for {company_name} (Bloomberg ticker: {ticker}) from the following source and classify whether this company is a programmatic acquirer.

Source URL: {source_url}
Report year: {report_year}

## Step 1: Extract evidence

Read the annual report at the URL above. Extract ALL verbatim text related to:
- How the company describes its acquisition/M&A strategy
- Whether acquisitions are described as a core part of growth
- Any acquisition model, programme, pipeline, or process mentioned
- Integration approach for acquired companies
- Number or pace of acquisitions mentioned
- Any quantitative M&A goals

Put all extracted verbatim quotes into `extracted_text`. This is the raw evidence — the classification below must be based ONLY on this text.

## Step 2: Quantitative pre-qualification

Based on the report (year {report_year}), check if the company meets the minimum acquisition activity thresholds:
- At least 5 acquisitions in the last 36 months (relative to the report date)
- At least 1 acquisition in the last 12 months (relative to the report date)
- If a company previously fell out, it must re-qualify with 5 acquisitions in 36 months

Set `acquisitions_mentioned` to the number of acquisitions you can identify from the report.
Set `meets_quantitative_threshold` to true only if both conditions are met based on what the report discloses.

If the quantitative threshold is not met, set `is_programmatic` to false and skip to the verdict.

## Step 3: Qualitative programmatic checklist

A programmatic acquirer has an explicit, recurring acquisition programme as a core part of its business model — not just a company that has done many acquisitions.

Evaluate each criterion based ONLY on the extracted text:

**Positive criteria** (set each to true/false):
- `core_growth_driver`: Acquisitions/inorganic growth described as a core growth driver
- `stated_programme`: A stated acquisition model, programme, or pipeline exists
- `repeated_references`: Repeated references to acquisitions as integral to strategy
- `clear_processes`: Clear routines/processes for sourcing, evaluating, or integrating acquisitions
- `decentralized_model`: Decentralized model where acquired companies keep autonomy
- `quantitative_goals`: Quantitative M&A goals (e.g., number of acquisitions per year, % of growth from M&A)

**Disqualifiers** — these alone are NOT sufficient to classify as programmatic:
- `only_high_deal_count`: The only evidence is a high number of completed acquisitions, with no programme language
- `only_opportunistic`: The only M&A language is generic like "we consider acquisitions opportunistically"
- `only_single_deal`: The report only mentions one specific deal

## Step 4: Verdict

Set `is_programmatic` to true ONLY if:
1. The quantitative threshold is met, AND
2. At least 2 positive criteria are true, AND
3. The evidence is not limited to disqualifiers only

Set `confidence` to:
- `high`: 3+ positive criteria clearly met, strong verbatim evidence
- `medium`: 2 positive criteria met, or evidence is somewhat ambiguous
- `low`: borderline case, limited evidence

Set `reasoning` to a 1-3 sentence explanation referencing which criteria were met or not met.

Set `evidence` to the specific quotes that support each positive criterion you marked as true.

Set `company_description` to a 1-sentence description of what the company does.
