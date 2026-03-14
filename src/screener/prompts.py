RESEARCH_PROMPT = """Find the most recent annual report (2025 or 2024) for {company_name} (Bloomberg ticker: {ticker}).

Search for the company's annual report, 10-K filing, annual review, or equivalent regulatory filing. Extract the sections that describe the company's strategy, growth model, and approach to acquisitions or M&A.

Return verbatim quotes about:
- How the company describes its acquisition/M&A strategy
- Whether acquisitions are described as a core part of growth
- Any acquisition model, programme, pipeline, or process mentioned
- Integration approach for acquired companies
- Number or pace of acquisitions mentioned
- Any quantitative M&A goals

If you cannot find an annual report, state that clearly and note what sources you did find.

Return a JSON object with these exact keys:
{{
  "annual_report_found": true or false,
  "report_year": the year of the report found (integer or null),
  "source_urls": ["url1", "url2"],
  "extracted_text": "the relevant strategy/M&A excerpts as a single string (verbatim quotes preferred)",
  "company_description": "brief 1-sentence description of what the company does"
}}

Return ONLY the JSON object, no other text."""


CLASSIFICATION_PROMPT = """Based ONLY on the following excerpts from {company_name}'s annual report ({year}), classify whether this company is a PROGRAMMATIC ACQUIRER.

Definition: A programmatic acquirer has an explicit, recurring acquisition programme as a core part of its business model — not just a company that has done many acquisitions.

CLASSIFY AS PROGRAMMATIC if the report shows:
- Acquisitions/inorganic growth described as a CORE growth driver
- A stated acquisition model, programme, or pipeline
- Repeated references to acquisitions as an integral part of strategy
- Clear routines/processes for sourcing, evaluating, or integrating targets
- Often: a decentralized model where acquired companies keep autonomy
- Quantitative M&A goals (e.g., number of acquisitions per year, % of growth from M&A)

NOT SUFFICIENT on its own:
- Having completed many acquisitions (activity ≠ programme)
- Generic wording like "we consider acquisitions opportunistically"
- Mention of one specific deal only
- Being a PE-backed company doing bolt-ons

EVIDENCE FROM ANNUAL REPORT:
{extracted_text}

Return ONLY a JSON object with these exact keys:
{{
  "is_programmatic": true or false,
  "confidence": "high" or "medium" or "low",
  "evidence": ["direct quote 1 from the text above", "direct quote 2"],
  "reasoning": "1-3 sentence explanation of your verdict"
}}"""
