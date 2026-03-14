import openpyxl
from pathlib import Path
from .models import Company


def load_companies(filepath: Path) -> list[Company]:
    """Load companies from Excel file with 'acquirer' and 'ticker' columns."""
    wb = openpyxl.load_workbook(filepath, read_only=True)
    ws = wb.active

    headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    name_col = None
    ticker_col = None

    for i, header in enumerate(headers):
        if header and header.strip().lower() == "acquirer":
            name_col = i
        elif header and header.strip().lower() == "ticker":
            ticker_col = i

    if name_col is None or ticker_col is None:
        raise ValueError(
            f"Excel file must have 'acquirer' and 'ticker' columns. Found: {headers}"
        )

    companies = []
    seen_slugs = set()

    for row in ws.iter_rows(min_row=2, values_only=True):
        name = row[name_col]
        ticker = row[ticker_col]

        if not name or not ticker:
            continue

        name = str(name).strip()
        ticker = str(ticker).strip()

        company = Company.from_row(name, ticker)

        if company.slug not in seen_slugs:
            seen_slugs.add(company.slug)
            companies.append(company)

    wb.close()
    return companies
