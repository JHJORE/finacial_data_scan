RESEARCH_PROMPT = """Find the most recent annual report (2025 or 2024) for {company_name} (Bloomberg ticker: {ticker}).

Search the company's own investor relations page, SEC EDGAR (for US-listed companies), or the relevant stock exchange filing database. Look for the annual report, 10-K filing, 20-F filing, annual review, or equivalent regulatory filing. Do NOT use press releases, news aggregators, or third-party summaries — only use primary filings from the company or its regulator.

Extract verbatim quotes about:
- How the company describes its acquisition/M&A strategy
- Whether acquisitions are described as a core part of growth
- Any acquisition model, programme, pipeline, or process mentioned
- Integration approach for acquired companies
- Number or pace of acquisitions mentioned
- Any quantitative M&A goals

If you cannot find an annual report, set annual_report_found to false and note what sources you did find in extracted_text."""


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
{extracted_text}"""
