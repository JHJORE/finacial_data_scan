"""Run Stage 2: Classify all researched companies."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from screener.classify import classify_companies
from screener.assemble import assemble_matrix, print_summary, save_matrix


async def main():
    classifications = await classify_companies(skip_existing=True)

    print(f"\nClassified {len(classifications)} companies")

    # Also assemble matrix
    df = assemble_matrix()
    output = save_matrix(df)
    print_summary(df)
    print(f"\nMatrix saved to: {output}")


if __name__ == "__main__":
    asyncio.run(main())
