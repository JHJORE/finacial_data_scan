Find the most recent annual report (2025 or 2024) for {company_name} (Bloomberg ticker: {ticker}).

Your ONLY job is to find the single best source URL for this company's annual report. Do NOT extract content — just find the source.

## Source priority (use the highest-priority source available)

1. The company's own investor relations page — look for the annual report PDF or HTML version
2. SEC EDGAR — for US-listed companies, find the 10-K or 20-F filing
3. The relevant stock exchange filing database (e.g., Companies House UK, Bolagsverket Sweden, BaFin Germany)
4. Other regulatory filing databases

## What to reject

Do NOT select any of these as your source:
- Press releases or news articles
- News aggregators (Reuters, Bloomberg News, Yahoo Finance articles)
- Third-party summaries or analyst reports
- Wikipedia or encyclopedia entries
- Investor presentation slides (unless no annual report exists)
- Financial data providers (Morningstar, S&P Capital IQ pages)

## Output

- Set `found` to true only if you located an actual annual report, 10-K, 20-F, or equivalent regulatory filing
- Set `report_year` to the fiscal year the report covers
- Set `source_type` to one of: `investor_relations`, `sec_edgar`, `stock_exchange`, `regulatory_filing`, `other`
- Set `source_rationale` to a brief explanation of why you chose this source (e.g., "2024 annual report PDF from the company's investor relations page")

If you cannot find an annual report, set `found` to false and explain what you found instead in `source_rationale`.
