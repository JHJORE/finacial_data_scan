Find the most recent annual report (2025 or 2024) for {company_name} (Bloomberg ticker: {ticker}).

Your ONLY job is to find the single best source URL for this company's annual report. Do NOT extract content — just find the source.

## CRITICAL: You must return an actual URL

You MUST set `source_url` to the actual, direct URL of the filing or report page. Not a description of where it is — the real URL.

Good examples:
- `https://www.sec.gov/Archives/edgar/data/833444/000083344424000056/jci-20240930.htm` (US 10-K filing)
- `https://www.sec.gov/ix?doc=/Archives/edgar/data/1757898/000175789825000005/ste-20250331.htm` (US iXBRL viewer)
- `https://investors.johnsoncontrols.com/annual-reports` (investor relations page)
- `https://www.sedarplus.ca/landingpage/...` (Canadian SEDAR+ filing)
- `https://find-and-update.company-information.service.gov.uk/company/...` (UK Companies House)

Bad examples:
- "" (empty string)
- "Available on the investor relations page" (that's a description, not a URL)
- Any search/index page that lists filings rather than being the filing itself (e.g., EDGAR `cgi-bin/browse-edgar`, SEDAR+ search results, or filing index pages ending in `-index.htm`)

## Source priority (use the highest-priority source available)

1. **SEC EDGAR** (strongly preferred for US-listed companies) — search for the 10-K or 20-F filing on sec.gov. Use the EDGAR full-text search or company search to find the filing page URL.
2. The company's own investor relations page — look for the annual report PDF or HTML version. Return the direct URL to the PDF or the investor relations page.
3. The relevant stock exchange filing database (e.g., Companies House UK, Bolagsverket Sweden, BaFin Germany)
4. Other regulatory filing databases

## What to reject

Do NOT select any of these as your source:
- Filing search or index pages from any regulator (e.g., EDGAR `cgi-bin/browse-edgar`, SEDAR+ search results, filing index pages) — you must find the actual filing document, not a page that lists filings
- Press releases or news articles
- News aggregators (Reuters, Bloomberg News, Yahoo Finance articles)
- Third-party summaries or analyst reports (Publicnow, Simply Wall St, Macrotrends, etc.)
- Wikipedia or encyclopedia entries
- Investor presentation slides (unless no annual report exists)
- Financial data providers (Morningstar, S&P Capital IQ pages)

## Output

- Set `status` to one of:
  - `found` — you located an actual annual report, 10-K, 20-F, or equivalent regulatory filing for this company
  - `not_applicable` — the company was acquired, merged, delisted, or otherwise no longer files its own independent annual report (provide the parent/acquirer's report URL in `source_url` if available, and explain in `source_rationale`)
  - `not_found` — you searched but could not locate any annual report or determine the company's status
- Set `source_url` to the direct URL of the filing or report page — this is the most important field
- Set `report_year` to the fiscal year the report covers
- Set `source_type` to one of: `investor_relations`, `sec_edgar`, `stock_exchange`, `regulatory_filing`, `other`
- Set `source_rationale` to a brief explanation of why you chose this source
