"""Run Agent 2: Read annual reports and classify all searched companies."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from screener.reader import read_companies
from screener.config import init_run
from screener.assemble import assemble_matrix, print_summary, save_matrix


async def main():
    init_run(create_new=False)

    results = await read_companies(skip_existing=True)

    print(f"\nRead {len(results)} companies")

    # Assemble matrix
    df = assemble_matrix()
    output = save_matrix(df)
    print_summary(df)
    print(f"\nMatrix saved to: {output}")


if __name__ == "__main__":
    asyncio.run(main())
